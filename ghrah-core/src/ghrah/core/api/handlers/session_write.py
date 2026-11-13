# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``sessions`` / ``branches`` 的写面处理器。

单元命令以 ``agent_name`` 索引，端口只带 ``agent_id``，因此写操作先经
``list_agents`` 解析名字——这是单元的既有契约，不在本层复制名字映射。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import AckView, BranchIdView, SessionIdView

from ghrah.core.api.capabilities import (
    BranchesActivateRequest,
    BranchesArchiveRequest,
    BranchesCreateRequest,
    BranchesDeleteRequest,
    SessionsActivateRequest,
    SessionsArchiveRequest,
    SessionsCreateRequest,
    SessionsDeleteRequest,
)
from ghrah.core.api.errors import CoreApiError, translate
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_session_write_handlers"]


async def resolve_agent_name(registry: RuntimeRegistry, project_id: str, agent_id: str) -> str:
    """按 agent_id 解析单元使用的 agent_name。

    Args:
        registry: 运行时注册表
        project_id: 项目标识
        agent_id: 稳定 Agent ID

    Returns:
        单元登记的名字

    Raises:
        CoreApiError: Agent 不在线（``NOT_FOUND``）
    """
    capability = "agents.resolve"
    with translate(capability):
        result = await registry.handle_command(
            project_id=project_id,
            command="list_agents",
            payload={"project_id": project_id},
        )
    if not result.get("success"):
        raise CoreApiError(
            Problem.make(
                ProblemCode.FAILED_PRECONDITION,
                capability=capability,
                detail=str(result.get("error") or "list_agents failed"),
            ),
        )
    agents = list((result.get("data") or {}).get("agents") or [])
    for item in agents:
        if str(item.get("agent_id") or "") == agent_id:
            return str(item.get("name") or "")
    raise CoreApiError(
        Problem.make(
            ProblemCode.NOT_FOUND,
            capability=capability,
            detail=f"agent {agent_id!r} is not online",
        ),
    )


def _id_from(result: dict[str, Any], key: str) -> str:
    """从单元结果里取 id（兼容 ``data.<key>`` 与扁平形状）。"""
    data = dict(result.get("data") or {})
    value = data.get(key)
    if isinstance(value, dict):
        value = value.get(key)
    return str(value or "")


def _ok_or_raise(result: dict[str, Any], capability: str) -> dict[str, Any]:
    """失败结果统一映射成 ``ApiError``。"""
    if not result.get("success"):
        error = str(result.get("error") or "rejected")
        code = (
            ProblemCode.NOT_FOUND
            if "not_found" in error or "not found" in error
            else ProblemCode.FAILED_PRECONDITION
        )
        raise CoreApiError(Problem.make(code, capability=capability, detail=error))
    return dict(result.get("data") or {})


def make_session_write_handlers(registry: RuntimeRegistry) -> dict[str, Handler]:
    """构造 ``sessions`` / ``branches`` 的写面处理器。

    Args:
        registry: 运行时注册表

    Returns:
        能力名到处理器的映射
    """

    async def session_create(request: SessionsCreateRequest) -> SessionIdView:
        """新建会话。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="session_create",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "name": request.name,
                "system_prompt": request.system_prompt,
            },
        )
        data = _ok_or_raise(result, "sessions.create")
        return SessionIdView(session_id=_id_from({"data": data}, "session_id"))

    async def session_activate(request: SessionsActivateRequest) -> AckView:
        """激活会话。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="session_activate",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
            },
        )
        _ok_or_raise(result, "sessions.activate")
        return AckView(ok=True)

    async def session_archive(request: SessionsArchiveRequest) -> AckView:
        """归档会话。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="session_archive",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
            },
        )
        _ok_or_raise(result, "sessions.archive")
        return AckView(ok=True)

    async def session_delete(request: SessionsDeleteRequest) -> AckView:
        """软删除会话。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="session_delete",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
            },
        )
        _ok_or_raise(result, "sessions.delete")
        return AckView(ok=True)

    async def branch_create(request: BranchesCreateRequest) -> BranchIdView:
        """分叉出新分支。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="branch_create",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
                "name": request.name,
                "from_node_id": request.from_node_id,
                "parent_branch_id": request.parent_branch_id,
            },
        )
        data = _ok_or_raise(result, "branches.create")
        return BranchIdView(branch_id=_id_from({"data": data}, "branch_id"))

    async def branch_activate(request: BranchesActivateRequest) -> AckView:
        """激活分支。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="branch_activate",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
                "branch_id": request.branch_id,
            },
        )
        _ok_or_raise(result, "branches.activate")
        return AckView(ok=True)

    async def branch_archive(request: BranchesArchiveRequest) -> AckView:
        """归档分支。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="branch_archive",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
                "branch_id": request.branch_id,
            },
        )
        _ok_or_raise(result, "branches.archive")
        return AckView(ok=True)

    async def branch_delete(request: BranchesDeleteRequest) -> AckView:
        """软删除分支。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="branch_delete",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
                "session_id": request.session_id,
                "branch_id": request.branch_id,
            },
        )
        _ok_or_raise(result, "branches.delete")
        return AckView(ok=True)

    return {
        "sessions.create": session_create,
        "sessions.activate": session_activate,
        "sessions.archive": session_archive,
        "sessions.delete": session_delete,
        "branches.create": branch_create,
        "branches.activate": branch_activate,
        "branches.archive": branch_archive,
        "branches.delete": branch_delete,
    }
