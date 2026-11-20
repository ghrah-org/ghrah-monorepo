# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Observer 的轮次、链历史、审批和投递 REST 读面。"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from ghrah.protocol.errors import ApiError
from ghrah.protocol.payloads.project import ProjectInfoPayload
from ghrah.protocol.ports.core import CorePort
from ghrah.protocol.views.core import (
    BranchListView,
    DeliveryQueueView,
    HistoryView,
    HitlRequestListView,
    SessionCatalogView,
    TurnListView,
)
from ghrah.protocol.views.observer_room import TurnDetailView

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import CORE_PORT_SERVICE, OBSERVER_ENDPOINT, PROJECT_MANAGER
from ghrah.subject.unit.base import SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import PROBLEM_TO_HTTP, AbilityApiError, _authorized

__all__ = ["TurnControlUnit", "build_turn_control_router"]


async def _read[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except ApiError as error:
        problem = error.problem
        code = PROBLEM_TO_HTTP.get(problem.code.value, "internal")
        raise AbilityApiError(
            code, problem.detail or code, detail=problem.details or None
        ) from error


def build_turn_control_router(*, config: SubjectConfig, ctx: Any) -> APIRouter:
    """构造按项目及 Agent 定位的只读路由。"""
    projects = ctx.get(PROJECT_MANAGER.name)
    ports = ctx.get(CORE_PORT_SERVICE.name)

    async def _port(request: Request, project_id: str, agent_id: str) -> CorePort:
        if not config.observer_http_api_enabled:
            raise AbilityApiError(
                "capability_unavailable",
                "观测端 HTTP API 未启用",
            )
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        result = await projects.handle_command("project_get", {"project_id": project_id})
        if not result["success"]:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        project = ProjectInfoPayload.model_validate(result["data"]["project"])
        if project.archived_at or project.deleted_at:
            raise AbilityApiError("project_archived", "项目已归档", status=409)
        if not any(agent.agent_id == agent_id for agent in project.agents):
            raise AbilityApiError("not_found", f"项目内的 Agent 不存在：{agent_id}")
        if not project.project_root_locator:
            raise AbilityApiError("capability_unavailable", "项目根定位符不可用")
        ports.remember_locator(project_id, project.project_root_locator)
        return ports.for_project(project_id)

    router = APIRouter(prefix="/api/v1/projects/{project_id}/agents/{agent_id}")

    @router.get("/turns")
    async def _turns(
        agent_id: str,
        port: CorePort = Depends(_port),
        session_id: str | None = None,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> TurnListView:
        return await _read(
            port.turns.list(agent_id=agent_id, session_id=session_id, status=status, limit=limit)
        )

    @router.get("/turns/{turn_id}")
    async def _turn(agent_id: str, turn_id: str, port: CorePort = Depends(_port)) -> TurnDetailView:
        return await _read(port.turns.detail(agent_id=agent_id, turn_id=turn_id))

    @router.get("/sessions")
    async def _sessions(agent_id: str, port: CorePort = Depends(_port)) -> SessionCatalogView:
        return await _read(port.sessions.catalog(agent_id=agent_id))

    @router.get("/sessions/{session_id}/branches")
    async def _branches(
        agent_id: str, session_id: str, port: CorePort = Depends(_port)
    ) -> BranchListView:
        branches = await _read(port.branches.list(agent_id=agent_id, session_id=session_id))
        return BranchListView(branches=branches)

    @router.get("/sessions/{session_id}/branches/{branch_id}/nodes")
    async def _nodes(
        agent_id: str,
        session_id: str,
        branch_id: str,
        port: CorePort = Depends(_port),
        limit: Annotated[int, Query(ge=-1)] = -1,
    ) -> HistoryView:
        if limit == 0:
            raise AbilityApiError("invalid_request", "limit 必须为 -1 或正整数")
        return await _read(
            port.sessions.history(
                agent_id=agent_id, session_id=session_id, branch_id=branch_id, limit=limit
            )
        )

    @router.get("/hitl/pending")
    async def _hitl(
        agent_id: str, port: CorePort = Depends(_port), session_id: str | None = None
    ) -> HitlRequestListView:
        return await _read(port.hitl.list_pending(agent_id=agent_id, session_id=session_id))

    @router.get("/deliveries/queue")
    async def _queue(
        agent_id: str, port: CorePort = Depends(_port), session_id: str | None = None
    ) -> DeliveryQueueView:
        try:
            return await _read(port.deliveries.queue(agent_id=agent_id, session_id=session_id))
        except AbilityApiError as error:
            if error.code != "not_found":
                raise
            # Agent 已按项目校验。尚无 MM 数据的项目视为无待投递项。
            return DeliveryQueueView(agent_id=agent_id, session_id=session_id)

    return router


class TurnControlUnit(SubjectUnit):
    """将 Core 只读能力挂到 full profile 的 Observer HTTP 应用。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config

    @property
    def meta(self) -> UnitMeta:
        return UnitMeta(
            name="turn_control",
            requires=frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER, CORE_PORT_SERVICE}),
        )

    async def init(self, ctx: Any) -> None:
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        endpoint.app.include_router(build_turn_control_router(config=self._config, ctx=ctx))
