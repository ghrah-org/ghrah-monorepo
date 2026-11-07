# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从同一次关系快照生成工具目录；定义只取已提交版本，不扫描安装目录。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ghrah.protocol.ability_tree import AbilityTreeView

from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.surface.projection import project_grants
from ghrah.ability.surface.types import SurfaceEntry

if TYPE_CHECKING:
    from ghrah.ability.access.store import AbilityAccessStore


@dataclass(frozen=True)
class SurfaceTool:
    """已获授且已选择的工具及其确定版本的定义；不是搜索索引条目。"""

    entry: SurfaceEntry
    full_name: str
    version_digest: str
    description: str
    tags: tuple[str, ...]
    effects: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    skills: tuple[str, ...] = ()
    mcp_route: tuple[str, str] | None = None

    def definition(self) -> dict[str, Any]:
        """生成与生产工具表相同形状的函数定义，供 token 估算与后续注入。"""
        return {
            "type": "function",
            "function": {
                "name": self.entry.tool_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass(frozen=True)
class SurfaceCatalog:
    """一个项目、Agent、run 的只读目录快照；所有成员已通过共用授权投影。"""

    project_id: str
    agent_id: str
    run_id: str
    revision: int
    tools: tuple[SurfaceTool, ...]


def catalog_from_view(
    view: AbilityTreeView, definitions: Mapping[str, AbilityDefinition]
) -> SurfaceCatalog:
    """先复用授权投影，再按版本摘要取成员元数据；不把包内其他成员带入目录。

    ``definitions`` 按 version_digest 定位，只能由宿主提供已提交版本。
    选择、可见性与撤销的判断全部由 ``project_grants`` 承担。
    """
    nodes = {node.alias: node for node in view.abilities}
    tools = []
    for entry in project_grants(view):
        node = nodes[entry.package]
        definition = definitions[node.version_digest]
        operation = definition.operations[entry.member.member]
        identity = definition.identity
        tools.append(
            SurfaceTool(
                entry=entry,
                full_name=f"{identity.namespace}.{identity.name}.{entry.member.member}",
                version_digest=node.version_digest,
                description=operation.description,
                tags=tuple(operation.tags),
                effects=tuple(operation.effects),
                input_schema=operation.input_schema,
                output_schema=operation.output_schema,
                mcp_route=(operation.mcp.server, operation.mcp.tool)
                if operation.mcp is not None
                else None,
                skills=tuple(
                    f"{node.alias}/{skill.key}" for skill in node.skills if skill.body_allowed
                ),
            )
        )
    return SurfaceCatalog(view.project_id, view.agent_id, view.run_id, view.revision, tuple(tools))


async def read_agent_catalog(agent: str, run: str, *, store: AbilityAccessStore) -> SurfaceCatalog:
    """以固定的受信 Agent/run 读取目录；旧 run、跨项目或跨 Agent 的快照拒绝。"""
    from ghrah.ability.access.errors import AccessError
    from ghrah.ability.access.store import AccessIdentity

    if not run or store.current_run(agent) != run:
        raise AccessError("access_denied")
    catalog = await store.surface_snapshot(AccessIdentity(store.project_id, agent, run), agent)
    if (catalog.project_id, catalog.agent_id, catalog.run_id) != (
        store.project_id,
        agent,
        run,
    ) or store.current_run(agent) != run:
        raise AccessError("access_denied")
    return catalog
