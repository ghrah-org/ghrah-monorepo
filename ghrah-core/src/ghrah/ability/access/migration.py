# SPDX-License-Identifier: Apache-2.0
"""已部署 Agent 的一次性授权迁移 bootstrap。

写入模型的裁决是「未配置策略就不授予」，已部署 Agent 不能因此丢能力：
升级时由操作员执行一次显式迁移，把**旧运行时的有效事实**写成
``source=migration`` 的初始获授，之后关系库成为唯一权威。

边界（零隐式）：

- 迁移只写初始事实：Agent 已有获授时拒绝覆盖，迁移过一次就不再重放；
- 迁移不读 manifest 模板：要写入的旧事实由调用方显式给出（旧注册表/
  旧配置的导出快照），本模块不做“从模板猜”的兜底；
- 迁移只登记内置能力包；外部目录包不在当前分发链路内；
- 撤销后不会复活：是否迁移过由关系库变更历史判定，不依赖当前获授行，
  因此操作员撤销迁移写入的授权后，下一次启动不会再次补回。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghrah.protocol.ability_tree import (
    GrantAbilityRequest,
    MemberAxes,
    SkillReadScope,
    TreeKey,
)
from pydantic import TypeAdapter

from ghrah.ability.access.errors import AccessError
from ghrah.ability.access.startup import StartupGrantError, register_package_instance
from ghrah.ability.access.store import (
    MIGRATION_SOURCE,
    AbilityAccessStore,
    AccessIdentity,
)
from ghrah.ability.loading.ability_catalog import load_ability_definitions

__all__ = [
    "MIGRATION_SOURCE",
    "MigrationError",
    "MigrationGrant",
    "MigrationResult",
    "migrate_agent",
    "migrate_agents",
]

logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """迁移失败；失败即失败，不自动重试、不回退模板或旧源。"""


@dataclass(frozen=True)
class MigrationGrant:
    """一条旧事实：旧运行时对某个内置能力包的有效获授。

    这是显式输入，不是从 manifest 模板推导出来的：``members`` / ``skills``
    / ``axes`` 由旧注册表或旧配置的导出快照给出。

    Attributes:
        package: 包身份全名（``ghrah.fs``）。
        members: 获授的 Operation 键。
        skills: 获授的内容读范围（skill 正文/附件/引用/脚本可见性）。
        axes: 成员的可见性与可用性显式事实；未列出时按默认值。
    """

    package: str
    members: tuple[str, ...] = ()
    skills: tuple[SkillReadScope, ...] = ()
    axes: tuple[MemberAxes, ...] = ()


@dataclass(frozen=True)
class MigrationResult:
    """一次迁移的结果摘要。

    Attributes:
        agent: 被迁移的 Agent 标识。
        revision: 迁移后的关系修订。
        aliases: 本次写入（或已存在）的包别名。
        already_migrated: 该 Agent 之前已完成迁移，本次没有写入。
    """

    agent: str
    revision: int
    aliases: tuple[str, ...]
    already_migrated: bool


def _migration_actor(project_id: str) -> AccessIdentity:
    """迁移操作员身份；来源与主体都固定为 ``migration``。"""
    return AccessIdentity(project_id, MIGRATION_SOURCE, MIGRATION_SOURCE, True, MIGRATION_SOURCE)


def _alias_for(catalog: Mapping[str, Any], package: str) -> str:
    """按定义面取包在 Agent 内的能力别名（容器名）；不猜命名规则。"""
    for _, definition in catalog.items():
        if definition.identity.full_name == package:
            return TypeAdapter(TreeKey).validate_python(definition.container_name)
    raise MigrationError(f"未找到内置能力包：{package}")


async def migrate_agent(
    store: AbilityAccessStore,
    agent: str,
    *,
    grants: Sequence[MigrationGrant],
    workspace_root: str | None,
) -> MigrationResult:
    """一次性写入已部署 Agent 的初始获授。

    Args:
        store: 授权关系库。
        agent: 已部署的 Agent 标识。
        grants: 旧事实快照；显式给出，不读模板。
        workspace_root: 工作区根；文件类成员必须显式给出。

    Returns:
        迁移结果；重复调用返回 ``already_migrated=True``。

    Raises:
        MigrationError: 没有可写入事实、Agent 已有授权、快照非法或迁移记录
            不完整。
    """
    entries = list(grants)
    if not entries:
        raise MigrationError(f"agent '{agent}' 的迁移快照没有可写入的获授事实")
    actor = _migration_actor(store.project_id)
    await store.ensure_agent(actor, agent)
    catalog = load_ability_definitions()
    requested_aliases = tuple(_alias_for(catalog, entry.package) for entry in entries)
    if await store.migrated(actor, agent):
        # 迁移只发生一次：操作员事后撤销部分授权是正常状态，缺项按“已撤销”
        # 处理，不自动补回；只把缺项记进日志，便于发现半途失败的迁移。
        existing = {record.alias for record in await store.grants(actor, agent)}
        missing = [alias for alias in requested_aliases if alias not in existing]
        if missing:
            logger.warning(
                "ability migration: agent=%s has migration history but aliases %s are absent "
                "— treated as revoked, not replayed",
                agent,
                missing,
            )
        return MigrationResult(
            agent=agent,
            revision=await store.agent_revision(actor, agent),
            aliases=tuple(alias for alias in requested_aliases if alias in existing),
            already_migrated=True,
        )
    existing = tuple(await store.grants(actor, agent))
    if existing:
        raise MigrationError(
            f"agent '{agent}' 已有获授事实（{sorted(record.alias for record in existing)}）；"
            "迁移只写初始 grant，不覆盖既有授权"
        )
    roots = (str(Path(workspace_root).resolve()),) if workspace_root else ()
    revision = await store.agent_revision(actor, agent)
    written: list[str] = []
    for entry in entries:
        alias = _alias_for(catalog, entry.package)
        try:
            registered = await register_package_instance(
                store=store,
                actor=actor,
                catalog=catalog,
                package=entry.package,
                members=set(entry.members),
                workspace_root=workspace_root,
            )
        except StartupGrantError as error:
            raise MigrationError(str(error)) from error
        request = GrantAbilityRequest(
            alias=alias,
            instance_id=registered.instance.instance_id,
            members=list(entry.members),
            allowed_roots=list(roots),
            skills=list(entry.skills),
            axes=list(entry.axes),
            expected_revision=revision,
        )
        try:
            revision = await store.write_grant(actor, agent, request)
        except AccessError as error:
            raise MigrationError(
                f"迁移在写入 {alias} 时失败（{error.code}）；已写入 {written}，"
                "请核对关系库后显式处理，本流程不自动重试"
            ) from error
        written.append(alias)
    logger.info("ability migration: agent=%s aliases=%s revision=%s", agent, written, revision)
    return MigrationResult(
        agent=agent,
        revision=revision,
        aliases=tuple(written),
        already_migrated=False,
    )


async def migrate_agents(
    store: AbilityAccessStore,
    agents: Mapping[str, Sequence[MigrationGrant]],
    *,
    workspace_root: str | None,
) -> dict[str, MigrationResult]:
    """按快照批量迁移；逐个 Agent 独立写入，失败即停并保留已完成结果。

    Args:
        store: 授权关系库。
        agents: ``agent -> 旧事实快照``。
        workspace_root: 工作区根。

    Returns:
        已完成迁移的 Agent → 结果（失败时只含失败之前完成的项）。

    Raises:
        MigrationError: 任一 Agent 迁移失败。
    """
    done: dict[str, MigrationResult] = {}
    for agent, grants in agents.items():
        done[agent] = await migrate_agent(
            store, agent, grants=grants, workspace_root=workspace_root
        )
    return done
