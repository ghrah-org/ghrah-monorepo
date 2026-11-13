# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``agents`` 分组的处理器。

所有操作都经 :class:`ghrah.core.units.RuntimeRegistry` 转交给项目单元——
Agent 的真实生命周期由 CoreUnit 承载，本层只做协议转换与错误映射。
"""

from __future__ import annotations

import uuid
from typing import Any

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import AckView, AgentIdView, Page
from ghrah.protocol.views.core import (
    AgentRuntimeView,
    AgentSpawnSpec,
    AgentTemplateSpec,
    AgentView,
    ToolListView,
    ToolView,
)

from ghrah.core.api.capabilities import (
    AgentCreateFromTemplateRequest,
    AgentCreateRequest,
    AgentDeleteRequest,
    AgentGetRequest,
    AgentListRequest,
    AgentRuntimeRequest,
)
from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.handlers.session_write import _ok_or_raise, resolve_agent_name
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_agent_handlers"]


def _fail(capability: str, result: dict[str, Any]) -> CoreApiError:
    """把单元返回的失败结果映射成稳定错误。"""
    error = str(result.get("error") or "rejected")
    code = (
        ProblemCode.NOT_FOUND
        if "not_found" in error or "not found" in error
        else ProblemCode.FAILED_PRECONDITION
    )
    return CoreApiError(
        Problem.make(code, capability=capability, detail=error),
    )


async def _online_agents(registry: RuntimeRegistry, project_id: str) -> list[dict[str, Any]]:
    """读取单元的在册 Agent（``list_agents``）。"""
    result = await registry.handle_command(
        project_id=project_id,
        command="list_agents",
        payload={"project_id": project_id},
    )
    if not result.get("success"):
        raise _fail("agents.list", result)
    return list((result.get("data") or {}).get("agents") or [])


def _spec_from_template(template: AgentTemplateSpec, agent_id: str | None) -> AgentSpawnSpec:
    """把模板解析成 spawn 规格。

    模板里尚未实现的策略（能力选择、审批策略）**明确拒绝**，不静默忽略。
    """
    ability = template.ability_request
    if ability is not None and (ability.packages or ability.mcp_refs or ability.surface):
        raise CoreApiError(
            Problem.make(
                ProblemCode.UNAVAILABLE,
                capability="agents.create_from_template",
                detail="ability_request 尚未实现（按包/技能选择能力）",
            ),
        )
    permission = template.permission_request
    if permission is not None and (
        permission.auto_approve
        or permission.safe_extra_commands
        or permission.safe_extra_sub_commands
    ):
        raise CoreApiError(
            Problem.make(
                ProblemCode.UNAVAILABLE,
                capability="agents.create_from_template",
                detail="permission_request 尚未实现（审批策略）",
            ),
        )
    model = template.model
    policy = template.context_policy
    return AgentSpawnSpec(
        name=template.name or template.title,
        agent_id=agent_id or "",
        manifest_ref=template.manifest_ref,
        endpoint_name=model.endpoint_name if model is not None else None,
        description=template.description,
        system_prompt=template.prompt.system_prompt if template.prompt is not None else "",
        max_iterations=policy.max_iterations if policy is not None else 10,
        communication_timeout=policy.communication_timeout if policy is not None else 300.0,
        window=policy.window if policy is not None else None,
        model_overrides=model.model_overrides if model is not None else None,
    )


def make_agent_handlers(registry: RuntimeRegistry) -> dict[str, Handler]:
    """构造 ``agents`` 分组处理器。

    Args:
        registry: 运行时注册表

    Returns:
        能力名到处理器的映射
    """

    async def _spawn(capability: str, project_id: str, spec: AgentSpawnSpec) -> AgentIdView:
        """把一份 spawn 规格落成 Core 命令。"""
        agent_id = spec.agent_id or uuid.uuid4().hex
        payload: dict[str, Any] = {
            "project_id": project_id,
            "manifest_ref": spec.manifest_ref,
            "validate_model": spec.validate_model,
            "config": {
                "name": spec.name,
                "agent_id": agent_id,
                "endpoint_name": spec.endpoint_name,
                "description": spec.description,
                "system_prompt": spec.system_prompt,
                "max_iterations": spec.max_iterations,
                "communication_timeout": spec.communication_timeout,
                "window": spec.window,
                "context": spec.context,
                "model_overrides": spec.model_overrides,
            },
        }
        result = await registry.handle_command(
            project_id=project_id,
            command="spawn_agent",
            payload=payload,
        )
        if not result.get("success"):
            raise _fail(capability, result)
        return AgentIdView(agent_id=agent_id)

    async def create(request: AgentCreateRequest) -> AgentIdView:
        """创建并启动一个 Agent（单元内同步完成激活）。"""
        return await _spawn("agents.create", request.project_id, request.spec)

    async def create_from_template(request: AgentCreateFromTemplateRequest) -> AgentIdView:
        """按模板创建 Agent：模板先解析成 spawn 规格。"""
        return await _spawn(
            "agents.create_from_template",
            request.project_id,
            _spec_from_template(request.template, request.agent_id),
        )

    async def get_agent(request: AgentGetRequest) -> AgentView:
        """读取在线 Agent 的定义与状态。"""
        agents = await _online_agents(registry, request.project_id)
        record = next(
            (item for item in agents if str(item.get("agent_id") or "") == request.agent_id),
            None,
        )
        if record is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="agents.get",
                    detail=f"agent {request.agent_id!r} is not online",
                ),
            )
        name = str(record.get("name") or "")
        info = await registry.handle_command(
            project_id=request.project_id,
            command="get_agent_info",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "name": name,
            },
        )
        if not info.get("success"):
            raise _fail("agents.get", info)
        data = dict(info.get("data") or {})
        return AgentView(
            agent_id=request.agent_id,
            project_id=request.project_id,
            name=name,
            state=dict(data.get("state") or {}),
            abilities=[str(item) for item in (data.get("abilities") or [])],
        )

    async def list_agents(request: AgentListRequest) -> Page[str]:
        """列出项目内在线 Agent 的 id。"""
        agents = await _online_agents(registry, request.project_id)
        ids = [str(item.get("agent_id") or "") for item in agents]
        return Page[str](items=[item for item in ids if item])

    async def delete(request: AgentDeleteRequest) -> AckView:
        """停止并移除一个 Agent。"""
        # terminate_agent 的载荷需要 name（协议既有形状）；按 agent_id 解析。
        online = await _online_agents(registry, request.project_id)
        name = next(
            (str(item.get("name")) for item in online if item.get("agent_id") == request.agent_id),
            "",
        )
        if not name:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="agents.delete",
                    detail=f"agent '{request.agent_id}' is not online",
                ),
            )
        result = await registry.handle_command(
            project_id=request.project_id,
            command="terminate_agent",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "name": name,
            },
        )
        if not result.get("success"):
            raise _fail("agents.delete", result)
        return AckView(ok=True)

    async def runtime(request: AgentRuntimeRequest) -> AgentRuntimeView:
        """读取 Agent 运行时快照。"""
        data = await registry.get_entity_runtime(
            project_id=request.project_id,
            agent_id=request.agent_id,
        )
        return AgentRuntimeView(
            agent_id=request.agent_id,
            availability="online" if data.get("availability") == "online" else "offline",
            incarnation_id=data.get("incarnation_id"),
            busy=data.get("busy"),
            loaded_abilities=(
                [str(item) for item in data["loaded_abilities"]]
                if isinstance(data.get("loaded_abilities"), (list, tuple))
                else None
            ),
        )

    async def tools(request: AgentRuntimeRequest) -> ToolListView:
        """列出 Agent 当前装载的工具。"""
        agents = await _online_agents(registry, request.project_id)
        record = next(
            (item for item in agents if str(item.get("agent_id") or "") == request.agent_id),
            None,
        )
        if record is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="agents.tools",
                    detail=f"agent {request.agent_id!r} is not online",
                ),
            )
        info = await registry.handle_command(
            project_id=request.project_id,
            command="get_agent_info",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "name": str(record.get("name") or ""),
            },
        )
        data = _ok_or_raise(info, "agents.tools")
        names = [str(item) for item in (data.get("abilities") or [])]
        return ToolListView(tools=[ToolView(name=item) for item in names])

    async def compact(request: AgentRuntimeRequest) -> AckView:
        """触发上下文压缩。"""
        name = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="agent_compact_context",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "agent_name": name,
            },
        )
        _ok_or_raise(result, "agents.compact")
        return AckView(ok=True)

    async def reset(request: AgentRuntimeRequest) -> AckView:
        """重置 Agent 上下文。"""
        result = await registry.handle_command(
            project_id=request.project_id,
            command="agent_reset",
            payload={"project_id": request.project_id, "agent_id": request.agent_id},
        )
        _ok_or_raise(result, "agents.reset")
        return AckView(ok=True)

    return {
        "agents.create": create,
        "agents.create_from_template": create_from_template,
        "agents.get": get_agent,
        "agents.list": list_agents,
        "agents.delete": delete,
        "agents.runtime": runtime,
        "agents.tools": tools,
        "agents.compact": compact,
        "agents.reset": reset,
    }
