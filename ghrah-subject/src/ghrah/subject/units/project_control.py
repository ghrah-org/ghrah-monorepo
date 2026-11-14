# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 管理 REST 读面（观察端边缘）。

本单元只是传输适配器：所有读取都经 ``bridge_command`` 走与 WS 相同的
``project_list`` / ``project_get`` 命令总线，因此 ``ProjectUnit`` 的
desired-state 投影、归档语义与事件行为全部沿用，不新增第二读路径。

不做的事：

- 不直接读 ``ProjectStore``（避免绕过 ``ProjectUnit`` 的投影与事件）；
- 不注册异常处理器/CORS（复用 ``AbilityControlUnit`` 已注册的共享设施，
  因此本单元只在 full profile、且在 ability_control 同存时挂载）；
- 不实现写面（新建/更新/生命周期由后续任务在同一 router 上追加）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from ghrah.protocol.enums import ProjectStatus
from ghrah.protocol.payloads.project import ProjectInfoPayload, ProjectListResultPayload

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT, PROJECT_MANAGER
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

__all__ = ["ProjectControlUnit", "build_project_control_router"]

logger = logging.getLogger(__name__)

_VERSION_GOT = re.compile(r"got (\d+)")

#: 命令失败码/文案 → (wire code, HTTP status)。读面只需要前几项；写面命令的
#: 冲突/归档/级联映射在写面任务里扩展，保持同一张表。
_COMMAND_STATUS: dict[str, tuple[str, int]] = {
    "invalid_request": ("invalid_request", 400),
    "resource_archived": ("resource_archived", 409),
    "project_has_rooms": ("project_has_rooms", 409),
    "project_shutdown_failed": ("project_shutdown_failed", 503),
    "project_resource_close_failed": ("project_resource_close_failed", 503),
    "project_root_purge_failed": ("project_root_purge_failed", 503),
    "capability_unavailable": ("capability_unavailable", 503),
}


def _command_error(result: dict[str, Any]) -> AbilityApiError:
    """把命令总线失败回执映射成 ``AbilityApiError``。

    读面可能遇到的只有「项目不存在」「载荷非法」「未知命令」；写面的
    版本冲突/归档/级联分支由写面任务补齐（正则解析 ``got (\\d+)`` 已在
    本模块预留）。
    """
    message = str(result.get("error") or "")
    code = _COMMAND_STATUS.get(message)
    if code is not None:
        wire, status = code
        return AbilityApiError(wire, str(result.get("error_detail") or message), status=status)
    if message.startswith("project not found"):
        return AbilityApiError("not_found", message)
    if message.startswith("Unknown command"):
        return AbilityApiError("capability_unavailable", message)
    if message.startswith("invalid payload") or message.endswith(" required"):
        return AbilityApiError("invalid_request", message)
    return AbilityApiError("invalid_change", message or "Project 命令被拒绝")


def build_project_control_router(*, config: SubjectConfig, ctx: Any) -> APIRouter:
    """构造 Project 管理路由（读面）。

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
        data = await _dispatch("project_get", {"project_id": project_id})
        project = ProjectInfoPayload.model_validate(data["project"])
        return project.model_dump(mode="json")

    router.add_api_route("/projects", _list, methods=["GET"])
    router.add_api_route("/projects/{project_id}", _get, methods=["GET"])
    return router


class ProjectControlUnit(SubjectUnit):
    """Project 管理 REST 读面 unit。

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
