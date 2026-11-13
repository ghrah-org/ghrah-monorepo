# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力管理面：总览装配与内容读取。

这一段原先在 Subject 的 ``ability_control`` 路由里，属于**后端装配逻辑**
（把关系库、可见面投影与运行时工具表合成一个视图），因此搬进 Core。
Subject 侧今后退化为透传。

装配所需的外部事实只有两个，都是注入的：

- ``runtime_tools``：Agent 当前实际装载的工具名（离线为 ``None``）；
- ``grant_policy``：部署的授权策略（未配置为 ``None``）。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityBoundToolView,
    AbilityGrantPolicyView,
    AbilityGrantView,
    AbilityLifecycleView,
    AbilityPendingRespawnView,
    AbilitySurfaceEntryView,
    AbilitySurfaceView,
)
from ghrah.protocol.ability_tree import AbilityTreeView, MemberAxes
from ghrah.protocol.views.core import ContentReadResult

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.content import ContentReadRequest
from ghrah.ability.surface.projection import project_grants

__all__ = ["build_admin_view", "read_content"]


def _axes_list(axes: dict[str, dict[str, bool]]) -> list[MemberAxes]:
    """把关系库的轴字典转成协议的成员轴列表。"""
    return [
        MemberAxes(
            member=member,
            visible=bool(values.get("visible", True)),
            available=bool(values.get("available", True)),
        )
        for member, values in sorted(axes.items())
    ]


def _grant_view(record: Any, digests: dict[str, dict[str, Any]]) -> AbilityGrantView:
    """把一条授权记录转成视图（摘要缺失时留空，不编造）。"""
    facts = digests.get(record.alias, {})
    return AbilityGrantView(
        alias=record.alias,
        instance_id=record.instance_id,
        instance_revision=int(facts.get("instance_revision", 0)),
        version_digest=str(facts.get("version_digest", "")),
        definition_digest=str(facts.get("definition_digest", "")),
        members=list(record.members),
        content=list(record.content),
        roots=list(record.roots),
        axes=_axes_list(record.axes),
        revision=record.revision,
        source=record.source,
    )


async def _digest_map(store: AbilityAccessStore, records: list[Any]) -> dict[str, dict[str, Any]]:
    """从实例读取实例代次与版本摘要；定义摘要缺失时留空。"""
    digests: dict[str, dict[str, Any]] = {}
    for record in records:
        try:
            instance = await store.instance(record.instance_id)
        except AccessError:
            continue
        digests[record.alias] = {
            "instance_revision": int(instance.get("revision", 0)),
            "version_digest": str(instance.get("digest", "")),
            "definition_digest": "",
        }
    return digests


def _surface_view(
    view: AbilityTreeView | None,
    current_runtime: tuple[str, ...] | None,
) -> AbilitySurfaceView:
    """由授权投影算出可见面，并与运行时工具表比对出待重启项。"""
    if view is None:
        return AbilitySurfaceView()
    entries = [
        AbilitySurfaceEntryView(
            tool_name=item.tool_name,
            member=item.member.member,
            package=item.package,
            static=item.static,
            available=item.available,
        )
        for item in project_grants(view)
    ]
    effective_names = {item.tool_name for item in entries}
    runtime = set(current_runtime) if current_runtime is not None else None
    pending = AbilityPendingRespawnView()
    if runtime is not None:
        pending = AbilityPendingRespawnView(
            added=sorted(effective_names - runtime),
            removed=sorted(runtime - effective_names),
        )
    return AbilitySurfaceView(
        mode="auto",
        static=[],
        effective=entries,
        current_runtime=list(current_runtime) if current_runtime is not None else None,
        pending_respawn=pending,
    )


async def build_admin_view(
    store: AbilityAccessStore,
    operator: AccessIdentity,
    agent_id: str,
    *,
    runtime_tools: tuple[str, ...] | None,
    grant_policy: AbilityGrantPolicyView | None,
) -> AbilityAdminView:
    """装配一个 Agent 的能力管理总览。

    Args:
        store: 能力关系库
        operator: 受信操作员身份
        agent_id: 稳定 Agent ID
        runtime_tools: 当前实际装载的工具名；离线为 ``None``
        grant_policy: 部署的授权策略视图；未配置为 ``None``

    Returns:
        管理面视图

    Raises:
        AccessError: 关系库拒绝（非「未装配」类）
    """
    assembled = True
    revision = 0
    try:
        revision = await store.agent_revision(operator, agent_id)
    except AccessError as error:
        if getattr(error, "code", None) != "not_found":
            raise
        assembled = False

    grants: list[Any] = []
    if assembled:
        try:
            grants = await store.grants(operator, agent_id)
        except AccessError:
            assembled = False

    try:
        view = await store.view(operator, agent_id)
    except AccessError:
        view = None

    digests = await _digest_map(store, grants) if assembled else {}
    digests = {
        **digests,
        **(
            {
                item.alias: {
                    "instance_revision": item.instance_revision,
                    "version_digest": item.version_digest,
                    "definition_digest": item.definition_digest,
                }
                for item in (view.abilities if view is not None else [])
            }
        ),
    }

    pending_reasons: list[str] = []
    requires_respawn = False
    if not assembled:
        state = "unassembled"
    elif view is not None or runtime_tools is not None:
        state = "running"
    else:
        state = "stopped"
        if grants:
            pending_reasons.append("Agent 未运行；启动后按关系库重新装配工具表")

    surface = _surface_view(view, runtime_tools)
    if state == "running" and surface.pending_respawn.added:
        requires_respawn = True
        pending_reasons.append("新增授权/放宽可见面需下次启动生效")

    bound_tools = (
        [
            AbilityBoundToolView(
                name=tool.name,
                alias=tool.alias,
                member=tool.member,
                grant_revision=tool.grant_revision,
                revision=tool.revision,
            )
            for tool in view.bound_tools
        ]
        if view is not None
        else []
    )

    return AbilityAdminView(
        project_id=operator.project_id,
        agent_id=agent_id,
        revision=revision,
        run_id=view.run_id if view is not None else None,
        lifecycle=AbilityLifecycleView(
            state=state,  # type: ignore[arg-type]
            requires_respawn=requires_respawn,
            pending_reasons=pending_reasons,
        ),
        grant_policy=grant_policy,
        grants=[_grant_view(record, digests) for record in grants],
        bound_tools=bound_tools,
        surface=surface,
        changes_cursor=0,
    )


async def read_content(
    store: AbilityAccessStore,
    operator: AccessIdentity,
    agent_id: str,
    *,
    alias: str,
    member: str,
    offset: int,
    limit: int,
) -> ContentReadResult | None:
    """读取某授权下的成员内容分块。

    Args:
        store: 能力关系库
        operator: 受信操作员身份
        agent_id: 稳定 Agent ID
        alias: 能力别名
        member: 成员名
        offset: 字节偏移
        limit: 分块大小（1..65536）

    Returns:
        内容分块结果；该别名未获授权时返回 ``None``（调用方转 NOT_FOUND）
    """
    grants = await store.grants(operator, agent_id)
    grant = next((item for item in grants if item.alias == alias), None)
    if grant is None:
        return None
    fence = await store.resolve(operator, grant.instance_id, member, global_instance=True)
    raw = await store.read_content(
        operator,
        fence,
        ContentReadRequest(offset=offset, limit=limit),
    )
    return ContentReadResult.model_validate(raw.model_dump(mode="json"))
