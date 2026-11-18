# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板（``agent-templates``）的观察端 REST。

实例级资源，无项目前缀：模板是 Subject 侧资源，由 Subject 持有 CRUD 与
持久化；Core 不读模板库。本 unit 只做 HTTP 形状、鉴权与错误映射，业务与
规范化在 ``template_store``（service/store）。

错误映射与信封沿用能力管理面：``{"error": {code, message, detail}}``。异常
处理器与 CORS 由 ``AbilityControlUnit`` 注册，本 unit **必须排在它之后**挂载。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from ghrah.protocol.agent_control import (
    AgentTemplateAbilityRequestView,
    AgentTemplateArchiveRequest,
    AgentTemplateContextPolicyView,
    AgentTemplateCreateRequest,
    AgentTemplateDetailView,
    AgentTemplateListView,
    AgentTemplateMetadataView,
    AgentTemplateModelView,
    AgentTemplatePathRequestView,
    AgentTemplatePermissionRequestView,
    AgentTemplatePromptView,
    AgentTemplateReferencesView,
    AgentTemplateRevisionSummaryView,
    AgentTemplateRevisionsView,
    AgentTemplateSummaryView,
    AgentTemplateUpdateRequest,
)
from ghrah.protocol.views.core import AGENT_CONTROL_MEMBER_KEYS
from pydantic import BaseModel, ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import (
    CORE_CATALOG_SERVICE,
    OBSERVER_ENDPOINT,
    TEMPLATE_STORE,
)
from ghrah.subject.template_store import (
    AgentTemplateRecord,
    AgentTemplateService,
    TemplateConflictError,
    TemplateIdentityConflictError,
    TemplateNotFoundError,
    TemplateStore,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_control import (
    AbilityApiError,
    _authorized,
    _serializable_validation_errors,
)

__all__ = ["AgentTemplateControlUnit", "build_agent_template_control_router"]

logger = logging.getLogger(__name__)


def _metadata(record: AgentTemplateRecord) -> AgentTemplateMetadataView:
    """记录元数据 → wire 视图。"""
    return AgentTemplateMetadataView.model_validate(record.metadata.model_dump(mode="json"))


def _summary(record: AgentTemplateRecord) -> AgentTemplateSummaryView:
    """记录 → 列表摘要（不含五组配置正文）。"""
    return AgentTemplateSummaryView(
        template_id=record.template_id,
        revision=record.revision,
        is_latest=record.latest,
        metadata=_metadata(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
        archived_at=record.archived_at,
        reference_count=record.reference_count,
    )


def _detail(record: AgentTemplateRecord) -> AgentTemplateDetailView:
    """记录 → 详情视图（全量配置 + ``reference_count`` 恒 0）。"""
    return AgentTemplateDetailView(
        template_id=record.template_id,
        revision=record.revision,
        is_latest=record.latest,
        metadata=_metadata(record),
        model=AgentTemplateModelView.model_validate(record.model.model_dump(mode="json")),
        prompt=AgentTemplatePromptView.model_validate(record.prompt.model_dump(mode="json")),
        context_policy=AgentTemplateContextPolicyView.model_validate(
            record.context_policy.model_dump(mode="json")
        ),
        ability_request=AgentTemplateAbilityRequestView.model_validate(
            record.ability_request.model_dump(mode="json")
        ),
        permission_request=AgentTemplatePermissionRequestView.model_validate(
            record.permission_request.model_dump(mode="json")
        ),
        path_request=[
            AgentTemplatePathRequestView.model_validate(item.model_dump(mode="json"))
            for item in record.path_request
        ],
        created_at=record.created_at,
        updated_at=record.updated_at,
        archived_at=record.archived_at,
        reference_count=record.reference_count,
    )


def build_agent_template_control_router(
    *,
    config: SubjectConfig,
    service: AgentTemplateService,
    catalog: Any | None = None,
) -> APIRouter:
    """构造模板路由；鉴权与能力管理面同形（回环免 token）。

    Args:
        catalog: Core 目录端口；用来把「整包」展开成逐个成员。None 时整包申请
            会被拒绝——落库不留整包，必须能展开。
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

    async def _authorize(request: Request) -> None:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")

    async def _guarded(call: Awaitable[Any]) -> Any:
        """把 store/service 的领域错误映射到 REST 错误码。"""
        try:
            return await call
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                "模板请求不合法",
                detail={"errors": _serializable_validation_errors(error.errors())},
                status=400,
            ) from error
        except TemplateNotFoundError as error:
            raise AbilityApiError("template_not_found", str(error), status=404) from error
        except TemplateIdentityConflictError as error:
            raise AbilityApiError("template_identity_exists", str(error), status=409) from error
        except TemplateConflictError as error:
            detail: dict[str, Any] = {"current_revision": error.current_revision}
            if error.archived:
                detail["archived"] = True
            raise AbilityApiError(
                "template_revision_conflict", str(error), detail=detail, status=409
            ) from error

    async def _profile(namespace: str, name: str, version: str | None) -> Any:
        """取某个包版本的画像；``version`` 为空时取目录里的当前版本。"""
        detail = await catalog.package_detail(namespace=namespace, name=name)
        versions = [item for item in detail.versions if item.origin != "mcp"]
        if not versions:
            raise AbilityApiError(
                "package_not_found", f"能力包没有可申请版本：{namespace}.{name}", status=404
            )
        chosen = next((item for item in versions if item.version == version), None)
        if version and chosen is None:
            raise AbilityApiError(
                "package_version_not_found",
                f"能力包 {namespace}.{name} 没有版本 {version}",
                status=404,
            )
        chosen = chosen or versions[-1]
        profile = await catalog.package_version_profile(
            namespace=namespace, name=name, version=chosen.version
        )
        return profile

    async def _expand_packages(payload: dict[str, Any]) -> None:
        """把整包申请展开成逐成员，并给能力件钉住版本。

        存储层不留「整包」概念：即便前端选的是整包，落库也是逐个成员。
        这样快照、恢复与成员级选择只有一种形状。

        Raises:
            AbilityApiError: 没有目录端口、或包/版本解析不到（不能存一个空壳）。
        """
        request = payload.get("ability_request")
        if not isinstance(request, dict):
            return
        packages = request.get("packages")
        if not isinstance(packages, list):
            return
        for package in packages:
            full_name = str(package.get("package") or "")
            if not full_name:
                continue
            # 控制成员由装配边界自己加：无论整包展开还是用户逐个勾选，都不落库
            # （显式传给 Core 会被拒绝，留着只会制造无效条目）。
            if full_name == "ghrah.core":
                kept = [
                    item
                    for item in package.get("operations") or []
                    if str(item.get("key") or "") not in AGENT_CONTROL_MEMBER_KEYS
                ]
                if len(kept) != len(package.get("operations") or []):
                    package["operations"] = kept
            namespace, _, name = full_name.partition(".")
            origin = package.get("origin")
            expand = bool(package.get("whole_container"))
            pin = origin != "builtin" and not package.get("version")
            if not expand and not pin:
                continue
            if catalog is None:
                raise AbilityApiError(
                    "capability_unavailable",
                    "目录端口未装配，无法把整包展开成成员；Core 目录服务不可用",
                )
            profile = await _profile(namespace, name or namespace, package.get("version"))
            if pin:
                package["version"] = None if origin == "builtin" else profile.version
            if not expand:
                continue
            package["whole_container"] = False
            package["operations"] = [
                {"key": item.key, "visible": bool(item.expose), "available": True}
                for item in profile.operations
                if not (
                    full_name == "ghrah.core" and item.key in AGENT_CONTROL_MEMBER_KEYS
                )
            ]
            package["skills"] = [
                {
                    "key": item.key,
                    "visible": True,
                    "scripts": [
                        {"key": script.key, "visible": True, "exec": False}
                        for script in item.scripts
                    ],
                }
                for item in profile.skills
            ]

    async def _body(request: Request) -> dict[str, Any]:
        try:
            raw = await request.json()
        except Exception as error:
            raise AbilityApiError("invalid_request", "请求体必须是 JSON") from error
        if not isinstance(raw, dict):
            raise AbilityApiError("invalid_request", "请求体必须是 JSON 对象")
        return raw

    def _parse[ModelT: BaseModel](
        body: dict[str, Any], model: type[ModelT], message: str
    ) -> ModelT:
        try:
            return model.model_validate(body)
        except ValidationError as error:
            raise AbilityApiError(
                "invalid_request",
                message,
                detail={"errors": _serializable_validation_errors(error.errors())},
                status=400,
            ) from error

    async def _list(
        request: Request,
        namespace: str | None = Query(default=None),
        name: str | None = Query(default=None),
        tag: str | None = Query(default=None),
        deprecated: bool | None = Query(default=None),
        include_archived: bool = Query(default=False),
        include_history: bool = Query(default=False),
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        records = await _guarded(
            service.list(
                namespace=namespace,
                name=name,
                tag=tag,
                deprecated=deprecated,
                include_archived=include_archived,
                include_history=include_history,
            )
        )
        return AgentTemplateListView(templates=[_summary(record) for record in records]).model_dump(
            mode="json"
        )

    router.add_api_route("/agent-templates", _list, methods=["GET"])

    async def _get(
        template_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        record = await _guarded(service.get(template_id))
        return _detail(record).model_dump(mode="json")

    router.add_api_route("/agent-templates/{template_id}", _get, methods=["GET"])

    async def _revisions(
        template_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        records = await _guarded(service.versions(template_id))
        return AgentTemplateRevisionsView(
            template_id=template_id,
            revisions=[
                AgentTemplateRevisionSummaryView(
                    revision=record.revision,
                    created_at=record.created_at,
                    is_latest=record.latest,
                    reference_count=record.reference_count,
                )
                for record in records
            ],
        ).model_dump(mode="json")

    router.add_api_route("/agent-templates/{template_id}/revisions", _revisions, methods=["GET"])

    async def _create(
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> JSONResponse:
        await _authorize(request)
        change = _parse(await _body(request), AgentTemplateCreateRequest, "创建请求不合法")
        payload = change.model_dump(mode="json")
        await _expand_packages(payload)
        record = await _guarded(service.create(payload))
        return JSONResponse(
            status_code=201,
            content=_detail(record).model_dump(mode="json"),
            headers={"Location": f"/api/v1/agent-templates/{record.template_id}"},
        )

    router.add_api_route("/agent-templates", _create, methods=["POST"])

    async def _update(
        template_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        change = _parse(await _body(request), AgentTemplateUpdateRequest, "编辑请求不合法")
        payload = change.model_dump(mode="json", exclude_unset=True, exclude={"expected_revision"})
        await _expand_packages(payload)
        record = await _guarded(service.update(template_id, change.expected_revision, payload))
        return _detail(record).model_dump(mode="json")

    router.add_api_route("/agent-templates/{template_id}", _update, methods=["PUT"])

    async def _archive(
        template_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        change = _parse(await _body(request), AgentTemplateArchiveRequest, "归档请求不合法")
        record = await _guarded(service.archive(template_id, change.expected_revision))
        return _detail(record).model_dump(mode="json")

    router.add_api_route("/agent-templates/{template_id}/archive", _archive, methods=["POST"])

    async def _references(
        template_id: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        await _authorize(request)
        # T1 不跨项目扫 ProjectStore：先确认模板存在，引用列表恒空（T2 接入）。
        await _guarded(service.get(template_id))
        return AgentTemplateReferencesView(template_id=template_id, agents=[]).model_dump(
            mode="json"
        )

    router.add_api_route("/agent-templates/{template_id}/references", _references, methods=["GET"])

    return router


class AgentTemplateControlUnit(SubjectUnit):
    """模板管理面：挂在能力管理面之后，共用同一个 FastAPI 应用。"""

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._store: TemplateStore | None = None
        self._service: AgentTemplateService | None = None
        self._router: APIRouter | None = None
        self._meta = UnitMeta(
            name="agent_template_control",
            requires=frozenset({OBSERVER_ENDPOINT, CORE_CATALOG_SERVICE}),
            provides=frozenset({TEMPLATE_STORE}),
            routes=RouteSpec(),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def router(self) -> APIRouter:
        if self._router is None:
            raise RuntimeError("AgentTemplateControlUnit has not been initialized.")
        return self._router

    @property
    def service(self) -> AgentTemplateService:
        if self._service is None:
            raise RuntimeError("AgentTemplateControlUnit has not been initialized.")
        return self._service

    @property
    def store(self) -> TemplateStore:
        if self._store is None:
            raise RuntimeError("AgentTemplateControlUnit has not been initialized.")
        return self._store

    async def init(self, ctx: Any) -> None:
        endpoint = ctx.get(OBSERVER_ENDPOINT.name)
        catalog = ctx.get(CORE_CATALOG_SERVICE.name)
        store = TemplateStore(self._config.persistence.db_path)
        await store.start()
        self._store = store
        # 模板库是实例级唯一权威：以服务形式提供，创建服务与 ProjectUnit 共用同一实例。
        ctx.provide(TEMPLATE_STORE.name, store)
        service = AgentTemplateService(store)
        self._service = service
        router = build_agent_template_control_router(
            config=self._config, service=service, catalog=catalog
        )
        endpoint.app.include_router(router)
        self._router = router
        logger.info(
            "agent template control REST mounted (enabled=%s, db=%s)",
            self._config.observer_http_api_enabled,
            self._config.persistence.db_path,
        )

    async def stop(self) -> None:
        store = self._store
        self._store = None
        self._service = None
        if store is not None:
            await store.stop()
