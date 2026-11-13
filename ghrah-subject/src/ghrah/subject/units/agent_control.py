# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 资源的观察端 REST。

写者仍是 ProjectManager 与 AgentEntityManager。本单元不改能力授权，
也不替换正在运行的进程。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from ghrah.protocol.agent_control import (
    AgentAbilitySummary,
    AgentAdminView,
    AgentCreateRequest,
    AgentDeletedView,
    AgentDesiredStateRequest,
    AgentListView,
    AgentPromptSummary,
    AgentRuntimeView,
    AgentSpecificationDetail,
    AgentSpecificationSummary,
    AgentSpecPatch,
    AgentSummaryView,
)
from ghrah.protocol.errors import ApiError
from ghrah.protocol.payloads.entity import AgentEntityPayload
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.models import AgentSpec, ProjectRecord
from ghrah.subject.project.store import ConcurrentModificationError
from ghrah.subject.runtime.service_keys import (
    ABILITY_ACCESS,
    CORE_PORT_SERVICE,
    OBSERVER_ENDPOINT,
    PROJECT_MANAGER,
    PROJECT_STORE,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

logger = logging.getLogger(__name__)

__all__ = ["AgentControlUnit", "build_agent_control_router"]

_VERSION_GOT = re.compile(r"got (\d+)")

_COMMAND_STATUS: dict[str, tuple[str, int]] = {
    "agent_manifest_required": ("agent_manifest_required", 409),
    "agent_name_exists": ("agent_name_exists", 409),
    "agent_not_found": ("not_found", 404),
    "project_not_active": ("project_not_active", 409),
    "agent_terminate_failed": ("agent_terminate_failed", 503),
    "agent_stop_failed": ("agent_stop_failed", 503),
    "agent_start_failed": ("agent_start_failed", 503),
    "agent_runtime_unavailable": ("agent_runtime_unavailable", 503),
    "model_configuration_invalid": ("model_configuration_invalid", 400),
    "agent_specification_conflict": ("conflict", 409),
    "project_unavailable": ("not_found", 404),
    "abilities_managed_by_access_store": ("invalid_change", 400),
    "invalid_request": ("invalid_request", 400),
}


def _prompt(text: str) -> AgentPromptSummary:
    raw = text.encode("utf-8")
    return AgentPromptSummary(
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )


def _runtime(payload: dict[str, Any] | None) -> AgentRuntimeView:
    if payload is None:
        return AgentRuntimeView(availability="unknown", error="运行态不可读")
    availability = payload.get("availability")
    if availability not in {"online", "offline", "unknown"}:
        availability = "unknown"
    return AgentRuntimeView(
        availability=availability,
        incarnation_id=payload.get("incarnation_id"),
        busy=payload.get("busy") if isinstance(payload.get("busy"), bool) else None,
        error=payload.get("error") if isinstance(payload.get("error"), str) else None,
    )


def _summary_spec(project: ProjectRecord, agent: AgentSpec) -> AgentSpecificationSummary:
    return AgentSpecificationSummary(
        agent_id=agent.agent_id,
        name=agent.name,
        endpoint_name=agent.endpoint_name,
        prompt=_prompt(agent.system_prompt or ""),
        desired_state=agent.desired_state,
        manifest_ref=agent.manifest_ref,
        runtime_status=agent.runtime_status,
        runtime_error=agent.runtime_error,
        project_version=project.version,
    )


def _detail_spec(project: ProjectRecord, agent: AgentSpec) -> AgentSpecificationDetail:
    summary = _summary_spec(project, agent)
    return AgentSpecificationDetail(
        **summary.model_dump(),
        system_prompt=agent.system_prompt or "",
    )


def build_agent_control_router(
    *,
    config: SubjectConfig,
    project_store: Any,
    projects: Any,
    access: Any,
    core_port: Any = None,
) -> APIRouter:
    """构造 Agent 资源路由。认证与能力管理面相同。"""
    router = APIRouter(prefix="/api/v1")
    enabled = config.observer_http_api_enabled
    builtin = config.ability_source == "builtin"

    async def _ensure_enabled() -> None:
        if enabled:
            return
        raise AbilityApiError(
            "capability_unavailable",
            "观测端能力管理面未启用（observer_http_api_enabled=false）",
        )

    async def _project(request: Request, project_id: str) -> ProjectRecord:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        project = await project_store.get(project_id)
        if project is None:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        return project

    async def _runtimes(project_id: str) -> dict[str, dict[str, Any]]:
        try:
            items = await projects.agents.list(project_id)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in _COMMAND_STATUS:
                wire, status = _COMMAND_STATUS[code]
                raise AbilityApiError(
                    wire, getattr(exc, "detail", "") or wire, status=status
                ) from exc
            raise
        return {
            item["identity"]["agent_id"]: item["runtime"]
            for item in items
            if item.get("identity", {}).get("agent_id")
        }

    async def _ability(
        project: ProjectRecord, agent_id: str
    ) -> tuple[AgentAbilitySummary | None, str | None]:
        if not builtin:
            return None, "能力来源不是 builtin，授权摘要不可用"
        if core_port is None:
            return None, "Core 能力端口不可用"
        try:
            port = core_port.project(project.project_id)
            view = await port.abilities.admin(agent_id=agent_id)
        except ApiError as error:
            code = str(getattr(error.problem.code, "value", error.problem.code))
            if code == "NOT_FOUND":
                return AgentAbilitySummary(revision=0, state="unassembled"), None
            return None, code.lower()
        except AbilityApiError:
            raise
        except Exception as exc:
            logger.warning("agent ability summary failed", exc_info=True)
            return None, str(exc)
        reasons: list[str] = []
        respawn = False
        state = view.lifecycle.state
        if state == "running":
            if view.surface.pending_respawn.added:
                respawn = True
                reasons.append("新增授权/放宽可见面需下次启动生效")
        elif state == "stopped" and view.grants:
            reasons.append("Agent 未运行；启动后按关系库重新装配工具表")
        return (
            AgentAbilitySummary(
                revision=view.revision,
                state=state,  # type: ignore[arg-type]
                requires_respawn=respawn,
                pending_reasons=reasons,
            ),
            None,
        )

    async def _admin(
        project: ProjectRecord,
        agent: AgentSpec,
        runtime: dict[str, Any] | None,
        *,
        effective: str | None = None,
    ) -> AgentAdminView:
        ability, unavailable = await _ability(project, agent.agent_id)
        return AgentAdminView(
            project_id=project.project_id,
            specification=_detail_spec(project, agent),
            runtime=_runtime(runtime),
            ability=ability,
            ability_unavailable=unavailable,
            effective=effective,  # type: ignore[arg-type]
            spec_overrides_template=bool(agent.manifest_ref) and effective == "next_start",
        )

    async def _find(project: ProjectRecord, agent_id: str) -> AgentSpec:
        agent = next((item for item in project.agents if item.agent_id == agent_id), None)
        if agent is None:
            raise AbilityApiError("not_found", f"Agent 不存在：{agent_id}")
        return agent

    async def _fail_command(
        result: dict[str, Any],
        *,
        project_id: str,
        agent_id: str = "",
        kept: bool | None = None,
    ) -> None:
        if result.get("success"):
            return
        message = str(result.get("error") or "")
        if "error_detail" in result:
            wire, status = _COMMAND_STATUS.get(message, ("invalid_change", 400))
            detail: dict[str, Any] | None = None
            if message == "agent_manifest_required":
                detail = {"kept": bool(kept)}
            text = str(result.get("error_detail") or message)
            raise AbilityApiError(wire, text, detail=detail, status=status)
        if "modified:" in message or "expected version" in message:
            found = _VERSION_GOT.search(message)
            detail: dict[str, Any] = {}
            if found:
                detail["project_version"] = int(found.group(1))
            if agent_id:
                current = await project_store.get(project_id)
                if current is not None and any(
                    item.agent_id == agent_id for item in current.agents
                ):
                    runtimes = await _runtimes(project_id)
                    agent = next(item for item in current.agents if item.agent_id == agent_id)
                    detail["view"] = (
                        await _admin(current, agent, runtimes.get(agent_id))
                    ).model_dump(mode="json")
            raise AbilityApiError("conflict", message, detail=detail or None)
        if message.startswith("project not found") or message == "resource_archived":
            raise AbilityApiError("not_found", message)
        raise AbilityApiError("invalid_change", message or "项目命令被拒绝")

    async def _raise_domain(error: Exception, project_id: str, agent_id: str) -> None:
        if isinstance(error, ConcurrentModificationError):
            project = await project_store.get(project_id)
            detail = {"project_version": project.version} if project is not None else None
            raise AbilityApiError("conflict", str(error), detail=detail) from error
        if isinstance(error, ValidationError):
            raise AbilityApiError(
                "invalid_request",
                "请求不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        code = getattr(error, "code", None)
        if not isinstance(code, str):
            raise error
        wire, status = _COMMAND_STATUS.get(code, ("invalid_change", 400))
        detail: dict[str, Any] = {}
        if wire == "conflict" or code == "agent_manifest_required":
            project = await project_store.get(project_id)
            if project is not None:
                detail["project_version"] = project.version
                agent = next((item for item in project.agents if item.agent_id == agent_id), None)
                if code == "agent_manifest_required":
                    detail["kept"] = agent is not None
                elif wire == "conflict" and agent is not None:
                    runtimes = await _runtimes(project_id)
                    latest = await _admin(project, agent, runtimes.get(agent_id))
                    detail["view"] = latest.model_dump(mode="json")
        text = getattr(error, "detail", "") or wire
        raise AbilityApiError(wire, text, detail=detail or None, status=status) from error

    async def _body(request: Request) -> dict[str, Any]:
        try:
            raw = await request.json()
        except Exception as error:
            raise AbilityApiError("invalid_request", "请求体必须是 JSON") from error
        if not isinstance(raw, dict):
            raise AbilityApiError("invalid_request", "请求体必须是 JSON 对象")
        return raw

    async def _list(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        project = await _project(request, project_id)
        runtimes = await _runtimes(project_id)
        agents: list[AgentSummaryView] = []
        for agent in project.agents:
            if not agent.agent_id:
                continue
            ability, unavailable = await _ability(project, agent.agent_id)
            agents.append(
                AgentSummaryView(
                    project_id=project_id,
                    specification=_summary_spec(project, agent),
                    runtime=_runtime(runtimes.get(agent.agent_id)),
                    ability=ability,
                    ability_unavailable=unavailable,
                )
            )
        return AgentListView(project_id=project_id, agents=agents).model_dump(mode="json")

    router.add_api_route("/projects/{project_id}/agents", _list, methods=["GET"])

    async def _get(
        project_id: str,
        agent_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        project = await _project(request, project_id)
        agent = await _find(project, agent_id)
        runtimes = await _runtimes(project_id)
        view = await _admin(project, agent, runtimes.get(agent_id))
        return view.model_dump(mode="json")

    router.add_api_route("/projects/{project_id}/agents/{agent_id}", _get, methods=["GET"])

    async def _create(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> JSONResponse:
        await _project(request, project_id)
        try:
            change = AgentCreateRequest.model_validate(await _body(request))
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "创建请求不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        if change.endpoint_name:
            try:
                projects.agents.validate_model_name(change.endpoint_name)
            except Exception as error:
                await _raise_domain(error, project_id, "")
        result = await projects.handle_command(
            "project_add_agent",
            {
                "project_id": project_id,
                "expected_version": change.expected_revision,
                "agent": {
                    "name": change.name,
                    "endpoint_name": change.endpoint_name,
                    "system_prompt": change.system_prompt,
                    "desired_state": change.desired_state,
                    "manifest_ref": "",
                    "abilities": None,
                },
            },
        )
        await _fail_command(result, project_id=project_id, kept=False)
        agent_id = str(result["data"]["agent_id"])
        project = await project_store.get(project_id)
        agent = await _find(project, agent_id)
        runtimes = await _runtimes(project_id)
        view = await _admin(project, agent, runtimes.get(agent_id))
        return JSONResponse(
            status_code=201,
            content=view.model_dump(mode="json"),
            headers={"Location": f"/api/v1/projects/{project_id}/agents/{agent_id}"},
        )

    router.add_api_route("/projects/{project_id}/agents", _create, methods=["POST"])

    async def _patch(
        project_id: str,
        agent_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _project(request, project_id)
        try:
            change = AgentSpecPatch.model_validate(await _body(request))
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "规格修改不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        fields: dict[str, Any] = {}
        if "endpoint_name" in change.model_fields_set:
            fields["endpoint_name"] = change.endpoint_name or None
        if "system_prompt" in change.model_fields_set:
            fields["system_prompt"] = change.system_prompt
        try:
            project, agent = await projects.agents.update_persisted_spec(
                project_id,
                agent_id,
                expected_revision=change.expected_revision,
                **fields,
            )
        except Exception as error:
            await _raise_domain(error, project_id, agent_id)
            raise
        runtimes = await _runtimes(project_id)
        view = await _admin(project, agent, runtimes.get(agent_id), effective="next_start")
        return view.model_dump(mode="json")

    router.add_api_route("/projects/{project_id}/agents/{agent_id}", _patch, methods=["PATCH"])

    async def _desired(
        project_id: str,
        agent_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _project(request, project_id)
        try:
            change = AgentDesiredStateRequest.model_validate(await _body(request))
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "期望状态不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        operation = "start" if change.state == "running" else "stop"
        try:
            await projects.agents.execute(
                AgentEntityPayload.model_validate(
                    {
                        "project_id": project_id,
                        "agent_id": agent_id,
                        "operation": {
                            "type": operation,
                            "expected_revision": change.expected_revision,
                        },
                    }
                )
            )
        except Exception as error:
            await _raise_domain(error, project_id, agent_id)
            raise
        project = await project_store.get(project_id)
        agent = await _find(project, agent_id)
        runtimes = await _runtimes(project_id)
        view = await _admin(project, agent, runtimes.get(agent_id), effective="stored")
        return view.model_dump(mode="json")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/desired-state",
        _desired,
        methods=["PUT"],
    )

    async def _delete(
        project_id: str,
        agent_id: str,
        request: Request,
        expected_revision: int = Query(ge=0),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        project = await _project(request, project_id)
        agent = await _find(project, agent_id)
        result = await projects.handle_command(
            "project_remove_agent",
            {
                "project_id": project_id,
                "agent_id": agent_id,
                "agent_name": agent.name,
                "expected_version": expected_revision,
            },
        )
        await _fail_command(result, project_id=project_id, agent_id=agent_id, kept=True)
        updated = await project_store.get(project_id)
        version = updated.version if updated is not None else project.version
        return AgentDeletedView(
            project_id=project_id, agent_id=agent_id, project_version=version
        ).model_dump(mode="json")

    router.add_api_route("/projects/{project_id}/agents/{agent_id}", _delete, methods=["DELETE"])
    return router


class AgentControlUnit(SubjectUnit):
    """Agent 管理面。挂在能力管理面之后，共用同一个 FastAPI 应用。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="agent_control",
            requires=frozenset(
                {
                    OBSERVER_ENDPOINT,
                    PROJECT_MANAGER,
                    PROJECT_STORE,
                    ABILITY_ACCESS,
                    CORE_PORT_SERVICE,
                }
            ),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("AgentControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        router = build_agent_control_router(
            config=self._config,
            project_store=ctx.get(PROJECT_STORE.name),
            projects=ctx.get(PROJECT_MANAGER.name),
            access=ctx.get(ABILITY_ACCESS.name),
            core_port=ctx.get(CORE_PORT_SERVICE.name),
        )
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "agent control REST mounted (enabled=%s)",
            self._config.observer_http_api_enabled,
        )

    async def stop(self) -> None:
        return None
