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
from pydantic import BaseModel, ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT
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
        metadata=_metadata(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
        archived_at=record.archived_at,
        reference_count=0,
    )


def _detail(record: AgentTemplateRecord) -> AgentTemplateDetailView:
    """记录 → 详情视图（全量配置 + ``reference_count`` 恒 0）。"""
    return AgentTemplateDetailView(
        template_id=record.template_id,
        revision=record.revision,
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
        reference_count=0,
    )


def build_agent_template_control_router(
    *,
    config: SubjectConfig,
    service: AgentTemplateService,
) -> APIRouter:
    """构造模板路由；鉴权与能力管理面同形（回环免 token）。"""
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
        summaries = await _guarded(service.history(template_id))
        return AgentTemplateRevisionsView(
            template_id=template_id,
            revisions=[
                AgentTemplateRevisionSummaryView(
                    revision=summary.revision,
                    created_at=summary.created_at,
                    digest=summary.digest,
                    size_bytes=summary.size_bytes,
                )
                for summary in summaries
            ],
        ).model_dump(mode="json")

    router.add_api_route("/agent-templates/{template_id}/revisions", _revisions, methods=["GET"])

    async def _create(
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> JSONResponse:
        await _authorize(request)
        change = _parse(await _body(request), AgentTemplateCreateRequest, "创建请求不合法")
        record = await _guarded(service.create(change.model_dump(mode="json")))
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
            requires=frozenset({OBSERVER_ENDPOINT}),
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
        store = TemplateStore(self._config.persistence.db_path)
        await store.start()
        self._store = store
        service = AgentTemplateService(store)
        self._service = service
        router = build_agent_template_control_router(config=self._config, service=service)
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
