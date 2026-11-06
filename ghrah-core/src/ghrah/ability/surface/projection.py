# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""授权面投影：把关系库投影裁成表面入口，供生产工具表与表面装配共用。

这里只有一条规则：**表面只列“已获授、已选择、可暴露、未显式降级”的成员**。
获授与可见性由关系库投影给出（不可见成员根本不在 ``operations`` 里），
“已选择”看 ``bound_tools``，可用性沿 ``OperationNode.availability`` 保留事实
而不在这里重新推断。

投影不扫安装目录、不读模板、不重新计算授权；工具名取已选记录里的固定名字，
不按成员键二次改名。T1 的静态集/搜索集都从同一份结果裁切，不另写第二份。
"""

from __future__ import annotations

from ghrah.protocol.ability_tree import AbilityTreeView, OperationNode

from ghrah.ability.surface.types import MemberRef, SurfaceEntry

__all__ = ["project_grants"]


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


def _available(member: OperationNode) -> bool:
    """关系库只声明“不可用”时该成员不可执行；未知留给后端观测。"""
    return member.availability != "unavailable"
