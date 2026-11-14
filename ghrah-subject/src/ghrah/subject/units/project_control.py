# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 管理 REST（观察端边缘）：读面 + 写面 + 生命周期。

本单元只是传输适配器：所有读写都经 ``bridge_command`` 走与 WS 相同的
``project_*`` 命令总线，因此 ``ProjectUnit`` 的 desired-state 投影、
``project_*`` 事件、生命周期锁与补偿事务全部沿用，不新增第二写路径。

边界：

- 不直接读/写 ``ProjectStore``；
- 不注册异常处理器/CORS（复用 ``AbilityControlUnit`` 已注册的共享设施，
  因此本单元只在 full profile、且与 ability_control 同存时挂载）；
- 业务判断（能否归档、能否 transition、房间数、级联）全部交给 Manager。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from ghrah.protocol.enums import ProjectStatus
from ghrah.protocol.payloads.project import (
    ProjectCreatePayload,
    ProjectDeletedView,
    ProjectInfoPayload,
    ProjectLifecycleRequest,
    ProjectListResultPayload,
    ProjectTransitionRequest,
    ProjectUpdateRequest,
)

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT, PROJECT_MANAGER
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

__all__ = ["ProjectControlUnit", "build_project_control_router"]

logger = logging.getLogger(__name__)

_VERSION_GOT = re.compile(r"got (\d+)")

#: ``_err_code`` 形式的稳定失败码 → (wire code, HTTP status)。
#: ``project_has_rooms`` / 版本冲突 / 归档等需要 detail 的分支在
#: :func:`_command_error` 里单独处理。
_COMMAND_STATUS: dict[str, tuple[str, int]] = {
    "project_shutdown_failed": ("project_shutdown_failed", 503),
    "project_resource_close_failed": ("project_resource_close_failed", 503),
    "project_root_purge_failed": ("project_root_purge_failed", 503),
    "capability_unavailable": ("capability_unavailable", 503),
}


def _command_error(result: dict[str, Any]) -> AbilityApiError:
    """把命令总线失败回执映射成 ``AbilityApiError``。

    写面命令的失败形态有三种：``_err_code``（error + error_detail）、
    ``_err`` 的稳定文案（如 ``project not found``）与 ``_err`` 的描述性
    文案（如版本冲突）。错误体统一为 T3 任务书的 409/503/400 口径。
    """
    message = str(result.get("error") or "")
    detail_text = str(result.get("error_detail") or "")

    known = _COMMAND_STATUS.get(message)
    if known is not None:
        wire, status = known
        detail = {"detail": detail_text} if detail_text else None
        return AbilityApiError(wire, detail_text or message, detail=detail, status=status)
    if message.startswith("project not found"):
        return AbilityApiError("not_found", message)
    if "modified:" in message or "expected version" in message:
        found = _VERSION_GOT.search(message)
        detail = {"current_version": int(found.group(1))} if found else None
        return AbilityApiError("conflict", message, detail=detail)
    if message == "resource_archived":
        return AbilityApiError("resource_archived", message, status=409)
    if message.startswith("illegal transition"):
        return AbilityApiError("illegal_transition", message, status=409)
    if message == "project_has_rooms":
        detail = {"room_count": int(detail_text)} if detail_text.isdigit() else None
        return AbilityApiError(
            "project_has_rooms", "项目仍有房间，禁止删除", detail=detail, status=409
        )
    if message.startswith(("project_resource_restore_failed", "project_room_count_failed")):
        return AbilityApiError(message.split(":", 1)[0], message, status=503)
    if message.startswith("Unknown command"):
        return AbilityApiError("capability_unavailable", message)
    if (
        message.startswith("invalid payload")
        or message.startswith("project creation failed")
        or message.startswith("writable_workspaces")
        or message.endswith(" required")
    ):
        return AbilityApiError("invalid_request", message)
    return AbilityApiError("invalid_change", message or "Project 命令被拒绝")


def build_project_control_router(*, config: SubjectConfig, ctx: Any) -> APIRouter:
    """构造 Project 管理路由。

    认证与门控与能力管理面相同：回环受信，非回环需 Bearer；
    ``observer_http_api_enabled=false`` 时统一 503。
    """
    router = APIRouter(prefix="/api/v1")
    enabled = config.observer_http_api_enabled

    async def _ensure_enabled() -> None:
        if enabled:
            return
        raise AbilityApiError(
            "capability_unavailable",
            "观测端能力管理面未启用（observer_http_api_enabled=false）",
        )

    def _ensure_authorized(request: Request) -> None:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")

    async def _dispatch(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await bridge_command(ctx, command, payload)
        if not result.get("success"):
            raise _command_error(result)
        data = result.get("data")
        if not isinstance(data, dict):
            raise AbilityApiError("internal", f"{command} 返回了非对象结果")
        return data

    async def _project_view(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = await _dispatch(command, payload)
        return ProjectInfoPayload.model_validate(data["project"]).model_dump(mode="json")

    # ─── 读面 ───

    async def _list(
        request: Request,
        archived: bool = False,
        status: ProjectStatus | None = None,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        payload: dict[str, Any] = {"archived": archived}
        if status is not None:
            payload["status"] = status.value
        data = await _dispatch("project_list", payload)
        return ProjectListResultPayload.model_validate(data).model_dump(mode="json")

    async def _get(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _project_view("project_get", {"project_id": project_id})

    # ─── 写面 ───

    async def _create(
        request: Request,
        body: ProjectCreatePayload,
        _: None = Depends(_ensure_enabled),
    ) -> Response:
        _ensure_authorized(request)
        project = await _project_view("project_create", body.model_dump())
        return JSONResponse(
            status_code=201,
            content=project,
            headers={"Location": f"/api/v1/projects/{project['project_id']}"},
        )

    async def _update(
        project_id: str,
        request: Request,
        body: ProjectUpdateRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        payload: dict[str, Any] = {
            "project_id": project_id,
            "expected_version": body.expected_version,
        }
        for field in ("name", "description", "manifest_ref", "shell_cwd"):
            value = getattr(body, field)
            if value is not None:
                payload[field] = value
        return await _project_view("project_update", payload)

    # ─── 生命周期 ───

    async def _lifecycle(
        project_id: str,
        command: str,
        expected_version: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"project_id": project_id}
        if expected_version is not None:
            payload["expected_version"] = expected_version
        return await _project_view(command, payload)

    async def _archive(
        project_id: str,
        request: Request,
        body: ProjectLifecycleRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _lifecycle(project_id, "project_archive", body.expected_version)

    async def _restore(
        project_id: str,
        request: Request,
        body: ProjectLifecycleRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _lifecycle(project_id, "project_restore", body.expected_version)

    async def _pause(
        project_id: str,
        request: Request,
        body: ProjectTransitionRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _lifecycle(project_id, "project_pause", body.expected_version)

    async def _resume(
        project_id: str,
        request: Request,
        body: ProjectTransitionRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _lifecycle(project_id, "project_resume", body.expected_version)

    async def _stop(
        project_id: str,
        request: Request,
        body: ProjectTransitionRequest,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        return await _lifecycle(project_id, "project_stop", body.expected_version)

    async def _delete(
        project_id: str,
        request: Request,
        expected_version: int = Query(..., ge=1),
        cascade_rooms: bool = False,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        _ensure_authorized(request)
        data = await _dispatch(
            "project_delete",
            {
                "project_id": project_id,
                "expected_version": expected_version,
                "cascade_rooms": cascade_rooms,
            },
        )
        return ProjectDeletedView.model_validate(data).model_dump(mode="json")

    router.add_api_route("/projects", _list, methods=["GET"])
    router.add_api_route("/projects", _create, methods=["POST"], status_code=201)
    router.add_api_route("/projects/{project_id}", _get, methods=["GET"])
    router.add_api_route("/projects/{project_id}", _update, methods=["PATCH"])
    router.add_api_route("/projects/{project_id}", _delete, methods=["DELETE"])
    router.add_api_route("/projects/{project_id}/archive", _archive, methods=["POST"])
    router.add_api_route("/projects/{project_id}/restore", _restore, methods=["POST"])
    router.add_api_route("/projects/{project_id}/pause", _pause, methods=["POST"])
    router.add_api_route("/projects/{project_id}/resume", _resume, methods=["POST"])
    router.add_api_route("/projects/{project_id}/stop", _stop, methods=["POST"])
    return router


class ProjectControlUnit(SubjectUnit):
    """Project 管理 REST unit。

    ``requires`` 里的 ``PROJECT_MANAGER`` 用于声明「ProjectUnit 必须先挂载」
    （Ouroboros inject 等依赖就绪），handler 本身只经命令总线间接调用它。
    """

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="project_control",
            requires=frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER}),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("ProjectControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        router = build_project_control_router(config=self._config, ctx=ctx)
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "project control REST mounted (enabled=%s)",
            self._config.observer_http_api_enabled,
        )

    async def stop(self) -> None:
        self._ctx = None
