# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力域管理面 Subject unit：REST ``/api/v1`` 的传输适配。

边界：本 unit 只做 HTTP 形态适配、身份注入、DTO 组合与错误映射；授权事实
仍只经 ``AbilityAccessStore`` 的 operator API 写入，运行期不重算模板，也不
提供执行旁路。WS 面不新增命令，变更通过 ``ABILITY_TREE_CHANGED`` 事件通知
观察端重读。
"""

from __future__ import annotations

import hmac
import importlib.util
import logging
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ghrah.protocol.ability_control import (
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityMcpProbeRequest,
)
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from ghrah.protocol.enums import EventType
from ghrah.protocol.errors import ApiError
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import (
    CORE_PORT_SERVICE,
    OBSERVER_ENDPOINT,
    OBSERVER_EVENT_BUS,
    PROJECT_STORE,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta

__all__ = [
    "AbilityApiError",
    "AbilityControlUnit",
    "build_ability_control_router",
    "register_ability_control_error_handlers",
]

logger = logging.getLogger(__name__)

_ERROR_STATUS: dict[str, int] = {
    "invalid_request": 400,
    "invalid_change": 400,
    "access_denied": 403,
    "not_found": 404,
    "conflict": 409,
    "unavailable": 503,
    "capability_unavailable": 503,
    "internal": 500,
}

#: Core 的 ``ProblemCode`` → 本单元的 HTTP 错误码（保持既有状态码语义）。
PROBLEM_TO_HTTP: dict[str, str] = {
    "NOT_FOUND": "not_found",
    "FAILED_PRECONDITION": "conflict",
    "INVALID_ARGUMENT": "invalid_request",
    "UNAUTHENTICATED": "access_denied",
    "PERMISSION_DENIED": "access_denied",
    "UNIMPLEMENTED": "capability_unavailable",
    "UNAVAILABLE": "unavailable",
    "INTERNAL": "internal",
}

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class AbilityApiError(Exception):
    """管理面可映射错误；detail 在 409 时携带最新 revision/view。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        self.status = status if status is not None else _ERROR_STATUS.get(code, 500)
        super().__init__(message)


def _jsonable(model: Any) -> Any:
    """把协议模型转成可 JSON 化的字典。"""
    return model.model_dump(mode="json")


def _is_loopback(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    host = client.host
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _authorized(request: Request, config: SubjectConfig) -> bool:
    if _is_loopback(request):
        return True
    token = config.observer_http_api_token
    header = request.headers.get("authorization", "")
    if not token or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer ") :], token)


def _mcp_probe_ready() -> bool:
    """检查可选的 MCP SDK 是否可用。"""
    try:
        return importlib.util.find_spec("mcp") is not None
    except ImportError, ValueError:
        return False


def build_ability_control_router(
    *,
    config: SubjectConfig,
    project_store: Any,
    core_port: Any = None,
    event_bus: Any = None,
) -> APIRouter:
    """构造管理面路由。

    本单元只做 HTTP 形状与鉴权，**全部业务都走 Core 能力端口**——
    装配逻辑（能力树、管理总览、内容读取、授权写入）都在 Core 内。
    """
    router = APIRouter(prefix="/api/v1")
    enabled = config.observer_http_api_enabled
    source = config.ability_source if config.ability_source in {"builtin", "legacy"} else "legacy"
    ready = enabled and source == "builtin"

    async def _ensure_enabled() -> None:
        if enabled:
            return
        raise AbilityApiError(
            "capability_unavailable",
            "观测端能力管理面未启用（observer_http_api_enabled=false）",
        )

    async def _ensure_catalog() -> None:
        await _ensure_enabled()
        if source != "builtin":
            raise AbilityApiError(
                "capability_unavailable",
                "能力来源不是 builtin，目录与实例读面不可用",
                detail={"legacy_deprecated": source == "legacy"},
            )

    async def _port(request: Request, project_id: str) -> Any:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        project = await project_store.get(project_id)
        if project is None:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        if core_port is None:
            raise AbilityApiError(
                "capability_unavailable",
                "Core 能力端口不可用（未装配 core.port 服务）",
            )
        return core_port.project(project_id)

    def _global_port() -> Any:
        """全局读面（能力包目录）用的端口；未装配时明确拒绝。"""
        if core_port is None:
            raise AbilityApiError(
                "capability_unavailable",
                "Core 能力端口不可用（未装配 core.port 服务）",
            )
        return core_port

    async def _call(awaitable: Any) -> Any:
        """把 Core 的 ApiError 转成 HTTP 形状的错误（保持既有状态码语义）。"""
        try:
            return await awaitable
        except ApiError as error:
            problem = error.problem
            raw = getattr(problem.code, "value", str(problem.code))
            if problem.details and problem.details.get("reason") == "definition_not_mcp":
                raise AbilityApiError(
                    "invalid_change", problem.detail, detail=problem.details, status=409
                ) from error
            code = PROBLEM_TO_HTTP.get(str(raw), "internal")
            raise AbilityApiError(
                code,
                problem.detail or code,
                detail=problem.details or None,
            ) from error

    async def _publish_tree(project_id: str, agent_id: str, revision: int, reason: str) -> None:
        try:
            await event_bus.publish(
                EventType.ABILITY_TREE_CHANGED.value,
                {
                    "project_id": project_id,
                    "agent_id": agent_id,
                    "revision": revision,
                    "reason": reason,
                    "resource": f"/scheme/agent/{agent_id}",
                },
            )
        except Exception:  # pragma: no cover - 事件只是失效信号，不阻断写入
            logger.warning("ability tree change event publish failed", exc_info=True)

    async def status() -> dict[str, Any]:
        """管理面可用性：能力来源 × 本进程启用开关。"""
        reason = ""
        if not enabled:
            reason = "观测端能力管理面未启用（observer_http_api_enabled=false）"
        elif source != "builtin":
            reason = "能力来源不是 builtin，目录与实例读面不可用"
        features = AbilityApiFeatures(
            tree=ready,
            grants_write=ready,
            versions_read=ready,
            versions_write=False,
            packages_read=ready,
            packages_install=False,
            mcp_probe=ready and core_port is not None and _mcp_probe_ready(),
            surface_write=False,
        )
        payload = AbilityApiStatus(
            ability_source=source,  # type: ignore[arg-type]
            legacy_deprecated=source == "legacy",
            operator="deployment" if enabled else None,
            features=features,
            reason=reason,
        )
        return _jsonable(payload)

    router.add_api_route("/abilities/status", status, methods=["GET"])

    async def _tree(
        project_id: str,
        agent_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        return _jsonable(await _call(port.abilities.tree(agent_id=agent_id)))

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree", _tree, methods=["GET"]
    )

    async def _admin(
        project_id: str,
        agent_id: str,
        request: Request,
        changes_limit: int = Query(default=0, ge=0, le=1000),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        del changes_limit
        port = await _port(request, project_id)
        return _jsonable(await _call(port.abilities.admin(agent_id=agent_id)))

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-admin", _admin, methods=["GET"]
    )

    async def _changes(
        project_id: str,
        agent_id: str,
        request: Request,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        del agent_id
        port = await _port(request, project_id)
        page = await _call(port.abilities.changes(after_seq=after_seq, limit=limit))
        return _jsonable(page)

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-changes", _changes, methods=["GET"]
    )

    async def _content(
        project_id: str,
        agent_id: str,
        alias: str,
        member: str,
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=65536, ge=1, le=65536),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        result = await _call(
            port.abilities.content(
                agent_id=agent_id,
                alias=alias,
                member=member,
                offset=offset,
                limit=limit,
            )
        )
        return _jsonable(result)

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/abilities/"
        "{alias}/content/{member:path}",
        _content,
        methods=["GET"],
    )

    async def _body(request: Request) -> dict[str, Any]:
        try:
            raw = await request.json()
        except Exception as error:
            raise AbilityApiError("invalid_request", "请求体必须是 JSON") from error
        if not isinstance(raw, dict):
            raise AbilityApiError("invalid_request", "请求体必须是 JSON 对象")
        return raw

    async def _after_write(
        project_id: str, agent_id: str, result: Any, reason: str
    ) -> dict[str, Any]:
        await _publish_tree(project_id, agent_id, result.revision, reason)
        return _jsonable(result)

    async def _put_grant(
        project_id: str,
        agent_id: str,
        alias: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        payload = await _body(request)
        try:
            change = GrantAbilityRequest.model_validate(payload)
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "授权请求不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        if change.alias != alias:
            raise AbilityApiError("invalid_request", "路径 alias 与请求体不一致")
        result = await _call(port.abilities.grant(agent_id=agent_id, change=change))
        return await _after_write(project_id, agent_id, result, "grant")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/grants/{alias}",
        _put_grant,
        methods=["PUT"],
    )

    async def _delete_grant(
        project_id: str,
        agent_id: str,
        alias: str,
        request: Request,
        expected_revision: int = Query(ge=0),
        members: list[str] | None = Query(default=None),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        change = RevokeAbilityRequest(
            alias=alias, expected_revision=expected_revision, members=members
        )
        result = await _call(port.abilities.revoke(agent_id=agent_id, change=change))
        return await _after_write(project_id, agent_id, result, "revoke")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/grants/{alias}",
        _delete_grant,
        methods=["DELETE"],
    )

    async def _put_tool(
        project_id: str,
        agent_id: str,
        tool_name: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        payload = await _body(request)
        try:
            change = BindToolRequest.model_validate(payload)
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "工具绑定请求不合法",
                detail={"errors": error.errors(include_url=False)},
            ) from error
        if change.tool_name != tool_name:
            raise AbilityApiError("invalid_request", "路径工具名与请求体不一致")
        result = await _call(port.abilities.enable_tool(agent_id=agent_id, change=change))
        return await _after_write(project_id, agent_id, result, "bind_tool")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/tools/{tool_name}",
        _put_tool,
        methods=["PUT"],
    )

    async def _delete_tool(
        project_id: str,
        agent_id: str,
        tool_name: str,
        request: Request,
        expected_revision: int = Query(ge=0),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        change = UnbindToolRequest(tool_name=tool_name, expected_revision=expected_revision)
        result = await _call(port.abilities.disable_tool(agent_id=agent_id, change=change))
        return await _after_write(project_id, agent_id, result, "unbind_tool")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/tools/{tool_name}",
        _delete_tool,
        methods=["DELETE"],
    )

    async def _packages(_: None = Depends(_ensure_catalog)) -> dict[str, Any]:
        return _jsonable(await _call(_global_port().abilities.packages()))

    router.add_api_route("/ability-packages", _packages, methods=["GET"])

    async def _package_detail(
        namespace: str,
        name: str,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        view = await _call(_global_port().abilities.package_detail(namespace=namespace, name=name))
        return _jsonable(view)

    router.add_api_route("/ability-packages/{namespace}/{name}", _package_detail, methods=["GET"])

    @router.get("/ability-packages/{namespace}/{name}/versions/{version}")
    async def _package_version_profile(
        namespace: str, name: str, version: str, _: None = Depends(_ensure_catalog)
    ) -> dict[str, Any]:
        profile = await _call(
            _global_port().abilities.package_version_profile(
                namespace=namespace, name=name, version=version
            )
        )
        return _jsonable(profile)

    @router.post("/ability-packages/{namespace}/{name}/versions/{version}/mcp/probe")
    async def _package_mcp_probe(
        namespace: str,
        name: str,
        version: str,
        body: AbilityMcpProbeRequest,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        if not _mcp_probe_ready():
            raise AbilityApiError("capability_unavailable", "MCP 探测未启用：缺少 MCP SDK")
        view = await _call(
            _global_port().abilities.package_mcp_probe(
                namespace=namespace, name=name, version=version, request=body
            )
        )
        return _jsonable(view)

    async def _instances(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        return _jsonable(await _call(port.abilities.instances()))

    router.add_api_route("/projects/{project_id}/ability-instances", _instances, methods=["GET"])

    async def _package_groups(_: None = Depends(_ensure_catalog)) -> dict[str, Any]:
        return _jsonable(await _call(_global_port().abilities.package_groups(scoped=False)))

    router.add_api_route("/ability-package-groups", _package_groups, methods=["GET"])

    async def _project_package_groups(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        return _jsonable(await _call(port.abilities.package_groups()))

    router.add_api_route(
        "/projects/{project_id}/ability-package-groups",
        _project_package_groups,
        methods=["GET"],
    )

    async def _agent_package_groups(
        project_id: str,
        agent_id: str,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        port = await _port(request, project_id)
        return _jsonable(await _call(port.abilities.package_groups(agent_id=agent_id)))

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-package-groups",
        _agent_package_groups,
        methods=["GET"],
    )

    async def _closed_write(
        namespace: str = "",
        name: str = "",
        version: str = "",
        project_id: str = "",
        instance_id: str = "",
        _: None = Depends(_ensure_catalog),
    ) -> None:
        del namespace, name, version, project_id, instance_id
        raise AbilityApiError("capability_unavailable", "该写入尚未开放")

    router.add_api_route("/ability-packages/import", _closed_write, methods=["POST"])
    router.add_api_route(
        "/ability-packages/{namespace}/{name}/versions/{version}/verify",
        _closed_write,
        methods=["POST"],
    )
    router.add_api_route(
        "/ability-packages/{namespace}/{name}/versions/{version}",
        _closed_write,
        methods=["DELETE"],
    )
    router.add_api_route(
        "/projects/{project_id}/ability-instances/{instance_id}/select",
        _closed_write,
        methods=["POST"],
    )

    return router


def register_ability_control_error_handlers(app: FastAPI) -> None:
    """注册管理面错误映射；不改既有 WS 路由行为。"""

    async def _api_error(_request: Request, exc: AbilityApiError) -> JSONResponse:
        payload: dict[str, Any] = {
            "error": {"code": exc.code, "message": exc.message, "detail": exc.detail}
        }
        return JSONResponse(status_code=exc.status, content=payload)

    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        payload = {
            "error": {
                "code": "invalid_request",
                "message": "请求参数不合法",
                "detail": {"errors": exc.errors()},
            }
        }
        return JSONResponse(status_code=400, content=payload)

    app.add_exception_handler(AbilityApiError, _api_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]


class AbilityControlUnit(SubjectUnit):
    """管理面 unit：始终挂载，``/abilities/status`` 始终可达，其余端点受开关约束。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="ability_control",
            requires=frozenset(
                {
                    OBSERVER_ENDPOINT,
                    CORE_PORT_SERVICE,
                    PROJECT_STORE,
                    OBSERVER_EVENT_BUS,
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
            raise RuntimeError("AbilityControlUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        app: FastAPI = endpoint.app
        project_store = ctx.get(PROJECT_STORE.name)
        core_port = ctx.get(CORE_PORT_SERVICE.name)
        event_bus = ctx.get(OBSERVER_EVENT_BUS.name)
        router = build_ability_control_router(
            config=self._config,
            project_store=project_store,
            core_port=core_port,
            event_bus=event_bus,
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(self._config.observer_cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Accept"],
        )
        register_ability_control_error_handlers(app)
        app.include_router(router)
        self._router = router
        logger.info(
            "ability control REST mounted (enabled=%s, ability_source=%s)",
            self._config.observer_http_api_enabled,
            self._config.ability_source,
        )

    async def stop(self) -> None:
        self._ctx = None
