# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""授权装配边界：builtin 模式下把 Agent 模板变成可运行工具表的唯一入口。

顶层 spawn（CoreUnit 命令面）、子 Agent spawn（``spawn_agent`` 工具）与
程序化装配都必须经这里拿到工具表：关系库是授权唯一权威，模板只表达申请。
边界之外自行拼装工具会绕过启动核定、执行接纳与权限范围，因此
``SupervisorActor`` 在 builtin 模式拒绝非边界产出的工具表。

该模块只做构造与转发，不持有额外状态；真正的核定算法在
:mod:`ghrah.ability.access.startup`。
"""

from __future__ import annotations

from typing import Any

from ghrah.ability.access.startup import (
    AuthorizedToolTable,
    StartupGrantMaterializer,
)
from ghrah.ability.access.store import AccessIdentity

__all__ = [
    "AuthorizedToolTable",
    "build_startup_materializer",
]


def build_startup_materializer(
    *,
    store: Any,
    source: Any,
    project_id: str,
    policy: Any,
    workspace_root: str | None,
    auto_approve_abilities: tuple[str, ...] = (),
    require_approval_by_default: bool = True,
    command_runner: Any = None,
    safe_extra_commands: tuple[str, ...] = (),
    safe_extra_sub_commands: tuple[tuple[str, tuple[str, ...]], ...] = (),
    mcp_deployment: Any = None,
    surface_thresholds: Any = None,
    surface_search_limit: int = 8,
    surface_loaded_limit: int = 8,
) -> StartupGrantMaterializer:
    """构造本次部署唯一的启动核定器（授权装配边界）。

    Args:
        store: 启动授权关系库（``AbilityAccessStore``）。
        source: 新版能力来源（``BuiltinAbilitySource``），用成员视图构造工具。
        project_id: 项目标识，写入操作员身份。
        policy: 部署方启动策略；None = 不授予任何能力。
        workspace_root: 工作区根，文件类成员的唯一沙盒。
        auto_approve_abilities: HITL 运行时覆盖层。
        require_approval_by_default: 未声明审批要求时的兜底策略。
        command_runner: 命令执行器注入（duck-typed）。
        safe_extra_commands: 部署方追加的基础命令 safe 白名单。
        safe_extra_sub_commands: 部署方追加的子命令 safe 白名单。

    Returns:
        启动核定器；同一部署内复用一个实例，按需对每个 Agent 调用
        ``materialize``。
    """
    return StartupGrantMaterializer(
        store=store,
        source=source,
        actor=AccessIdentity(project_id, "deployment", "deployment", True, "deployment"),
        policy=policy,
        workspace_root=workspace_root,
        auto_approve_abilities=auto_approve_abilities,
        require_approval_by_default=require_approval_by_default,
        command_runner=command_runner,
        safe_extra_commands=safe_extra_commands,
        safe_extra_sub_commands=safe_extra_sub_commands,
        mcp_deployment=mcp_deployment,
        surface_thresholds=surface_thresholds,
        surface_search_limit=surface_search_limit,
        surface_loaded_limit=surface_loaded_limit,
    )
