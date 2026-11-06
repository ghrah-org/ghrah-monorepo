# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""授权面投影：把关系库投影裁成表面入口，供生产工具表与表面装配共用。

关系过滤只有一份：**只列“已获授、已选择、可暴露、未撤销”的成员**。
获授与可见性由关系库投影给出（不可见成员根本不在 ``operations`` 里），
“已选择”看 ``bound_tools``，可用性沿 ``OperationNode.availability`` 保留事实
而不在这里重新推断。

投影不扫安装目录、不读模板、不重新计算授权；工具名取已选记录里的固定名字，
不按成员键二次改名。静态集与搜索集都从同一份结果裁切，不另写第二份目录。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ghrah.protocol.ability_tree import AbilityTreeView, OperationNode

from ghrah.ability.surface.types import MemberRef, SurfaceEntry

if TYPE_CHECKING:
    from ghrah.ability.access.store import AbilityAccessStore

__all__ = ["project_grants", "project_surface"]


def project_grants(view: AbilityTreeView) -> list[SurfaceEntry]:
    """从关系库投影得到授权面（尚未裁静态集与搜索集）。

    Args:
        view: 同一 Agent 的关系库投影。

    Returns:
        按工具名升序排列的表面条目；未选择、不可见或已撤销的成员不出现。
    """
    entries: list[SurfaceEntry] = []
    for tool in view.bound_tools:
        node = next((item for item in view.abilities if item.alias == tool.alias), None)
        if node is None:
            continue
        member = next((item for item in node.operations if item.key == tool.member), None)
        if member is None:
            continue
        entries.append(
            SurfaceEntry(
                tool_name=tool.name,
                member=MemberRef(instance_id=node.instance_id, member=member.key),
                package=node.alias,
                static=False,
                available=_available(member),
            )
        )
    return sorted(entries, key=lambda item: item.tool_name)


async def project_surface(
    agent: str,
    run: str,
    *,
    store: AbilityAccessStore,
    backend_available: Callable[[SurfaceEntry], bool],
) -> list[SurfaceEntry]:
    """读取指定 Agent/run 的可执行授权面。

    关系成员仍只由 :func:`project_grants` 过滤。本入口额外核对受信运行代次，
    并排除关系库已显式降级或当前没有可用后端的成员；不可用成员不会出现在
    返回的工具面里。后端就绪状态由装配方注入，不能由模型参数指定。

    Args:
        agent: 稳定 Agent ID。
        run: 调用方持有的受信 run；必须与生命周期权威中的当前 run 一致。
        store: 该 Agent 所属项目的关系库。
        backend_available: 按表面条目查询本次装配的执行后端是否就绪。

    Returns:
        按工具名稳定排序的可用表面条目。

    Raises:
        AccessError: Agent/run 不属于当前运行代次，或关系库拒绝读取。
    """
    # 延迟导入避免 access 启动物化与 surface 导出之间形成模块初始化环。
    from ghrah.ability.access.errors import AccessError
    from ghrah.ability.access.store import AccessIdentity

    if store.current_run(agent) != run:
        raise AccessError("access_denied")
    actor = AccessIdentity(store.project_id, agent, run)
    view = await store.view(actor, agent)
    if view.project_id != store.project_id or view.agent_id != agent or view.run_id != run:
        raise AccessError("access_denied")
    return [entry for entry in project_grants(view) if entry.available and backend_available(entry)]


def _available(member: OperationNode) -> bool:
    """关系库只声明“不可用”时该成员不可执行；未知留给后端观测。"""
    return member.availability != "unavailable"
