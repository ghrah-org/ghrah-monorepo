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
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from ghrah.ability.access import AccessError
from ghrah.ability.content import ContentReadRequest
from ghrah.ability.surface.projection import project_grants
from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiFeatures,
    AbilityApiStatus,
    AbilityBoundToolView,
    AbilityChangesPage,
    AbilityChangeView,
    AbilityGrantPolicyView,
    AbilityGrantView,
    AbilityLifecycleView,
    AbilityPendingRespawnView,
    AbilitySurfaceEntryView,
    AbilitySurfaceView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    GrantAbilityRequest,
    MemberAxes,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from ghrah.protocol.enums import EventType
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.service_keys import (
    ABILITY_ACCESS,
    OBSERVER_ENDPOINT,
    OBSERVER_EVENT_BUS,
    PROJECT_STORE,
    AbilityAccessHandle,
    AbilityAccessService,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units.ability_catalog import (
    CatalogError,
    build_instances_view,
    build_package_catalog,
    build_package_detail,
    build_package_groups,
    valid_identity_segment,
)

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


@dataclass(frozen=True)
class _Resolved:
    """一次请求解析出的受信上下文；身份全部来自服务端注入。"""

    project_id: str
    access: AbilityAccessService
    handle: AbilityAccessHandle


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


def _access_code(error: AccessError) -> str:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else "unavailable"


def _raise_from_access(error: AccessError) -> None:
    code = _access_code(error)
    raise AbilityApiError(code, f"能力关系库拒绝：{code}")


def _jsonable(model: Any) -> Any:
    return model.model_dump(mode="json")


def _axes_list(axes: dict[str, dict[str, bool]]) -> list[MemberAxes]:
    return [
        MemberAxes(
            member=member,
            visible=bool(values.get("visible", True)),
            available=bool(values.get("available", True)),
        )
        for member, values in sorted(axes.items())
    ]


def _grant_view(record: Any, digests: dict[str, dict[str, Any]]) -> AbilityGrantView:
    facts = digests.get(record.alias, {})
    return AbilityGrantView(
        alias=record.alias,
        instance_id=record.instance_id,
        instance_revision=int(facts.get("instance_revision", 0)),
        version_digest=str(facts.get("version_digest", "")),
        definition_digest=str(facts.get("definition_digest", "")),
        members=list(record.members),
        content=list(record.content),
        roots=list(record.roots),
        axes=_axes_list(record.axes),
        revision=record.revision,
        source=record.source,
    )


async def _digest_map(store: Any, operator: Any, records: list[Any]) -> dict[str, dict[str, Any]]:
    """尽量从实例读取实例代次与版本摘要；定义摘要缺失时留空，不编造。"""
    del operator
    digests: dict[str, dict[str, Any]] = {}
    for record in records:
        try:
            instance = await store.instance(record.instance_id)
        except AccessError:
            continue
        digests[record.alias] = {
            "instance_revision": int(instance.get("revision", 0)),
            "version_digest": str(instance.get("digest", "")),
            "definition_digest": "",
        }
    return digests


def _surface_view(
    view: AbilityTreeView | None,
    current_runtime: tuple[str, ...] | None,
) -> AbilitySurfaceView:
    if view is None:
        return AbilitySurfaceView()
    entries = [
        AbilitySurfaceEntryView(
            tool_name=item.tool_name,
            member=item.member.member,
            package=item.package,
            static=item.static,
            available=item.available,
        )
        for item in project_grants(view)
    ]
    effective_names = {item.tool_name for item in entries}
    runtime = set(current_runtime) if current_runtime is not None else None
    pending = AbilityPendingRespawnView()
    if runtime is not None:
        pending = AbilityPendingRespawnView(
            added=sorted(effective_names - runtime),
            removed=sorted(runtime - effective_names),
        )
    return AbilitySurfaceView(
        mode="auto",
        static=[],
        effective=entries,
        current_runtime=list(current_runtime) if current_runtime is not None else None,
        pending_respawn=pending,
    )


def _changes_page(rows: list[dict[str, Any]], after_seq: int, limit: int) -> AbilityChangesPage:
    changes: list[AbilityChangeView] = []
    for row in rows:
        raw_actor = row.get("actor")
        actor: dict[str, Any] = {}
        if isinstance(raw_actor, str):
            try:
                parsed = json.loads(raw_actor)
            except json.JSONDecodeError:
                parsed = {}
            actor = parsed if isinstance(parsed, dict) else {}
        elif isinstance(raw_actor, dict):
            actor = raw_actor
        changes.append(
            AbilityChangeView(
                seq=int(row.get("seq", 0)),
                agent=str(row.get("agent", "")),
                revision=int(row.get("revision", 0)),
                reason=str(row.get("reason", "")),
                actor=actor,
                before_revision=int(row.get("before_revision", 0)),
                version_digest=row.get("version_digest"),
                source=str(row.get("source", "")),
                policy=row.get("policy"),
            )
        )
    next_after = changes[-1].seq if changes else after_seq
    return AbilityChangesPage(
        changes=changes,
        next_after_seq=next_after,
        has_more=len(changes) == limit,
    )


def build_ability_control_router(
    *,
    config: SubjectConfig,
    project_store: Any,
    access: AbilityAccessService,
    event_bus: Any,
) -> APIRouter:
    """构造管理面路由；依赖由 unit 从受信 ctx 注入（测试可直接构造）。"""
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

    def _catalog_error(error: CatalogError) -> None:
        raise AbilityApiError(error.code, error.message) from error

    async def _resolve(request: Request, project_id: str) -> _Resolved:
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "非回环请求需要有效 Bearer token")
        project = await project_store.get(project_id)
        if project is None:
            raise AbilityApiError("not_found", f"项目不存在：{project_id}")
        handle = await access.acquire(project_id, project.project_root_locator)
        return _Resolved(project_id=project_id, access=access, handle=handle)

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

    async def _try_view(store: Any, operator: Any, agent_id: str) -> AbilityTreeView | None:
        try:
            return await store.view(operator, agent_id)
        except AccessError:
            return None

    async def _write_error(store: Any, operator: Any, agent_id: str, error: AccessError) -> None:
        if _access_code(error) == "conflict":
            detail: dict[str, Any] = {}
            try:
                detail["revision"] = await store.agent_revision(operator, agent_id)
            except AccessError:
                detail["revision"] = None
            view = await _try_view(store, operator, agent_id)
            detail["view"] = _jsonable(view) if view is not None else None
            raise AbilityApiError(
                "conflict", "关系修订已变化，请重读后再提交", detail=detail
            ) from error
        raise AbilityApiError(
            _access_code(error), f"能力关系库拒绝：{_access_code(error)}"
        ) from error

    async def _admin_view(resolved: _Resolved, agent_id: str) -> AbilityAdminView:
        store, operator = resolved.handle.store, resolved.handle.operator
        assembled = True
        revision = 0
        try:
            revision = await store.agent_revision(operator, agent_id)
        except AccessError as error:
            if _access_code(error) != "not_found":
                raise
            assembled = False
        grants: list[Any] = []
        if assembled:
            try:
                grants = await store.grants(operator, agent_id)
            except AccessError:
                assembled = False
                grants = []
        view = await _try_view(store, operator, agent_id)
        runtime = await resolved.access.agent_tool_snapshot(resolved.project_id, agent_id)
        digests = await _digest_map(store, operator, grants) if assembled else {}
        digests = {
            **digests,
            **(
                {
                    item.alias: {
                        "instance_revision": item.instance_revision,
                        "version_digest": item.version_digest,
                        "definition_digest": item.definition_digest,
                    }
                    for item in (view.abilities if view is not None else [])
                }
            ),
        }
        pending_reasons: list[str] = []
        requires_respawn = False
        if not assembled:
            state = "unassembled"
        elif view is not None or runtime is not None:
            state = "running"
        else:
            state = "stopped"
            if grants:
                pending_reasons.append("Agent 未运行；启动后按关系库重新装配工具表")
        surface = _surface_view(view, runtime)
        if state == "running" and surface.pending_respawn.added:
            requires_respawn = True
            pending_reasons.append("新增授权/放宽可见面需下次启动生效")
        bound_tools = (
            [
                AbilityBoundToolView(
                    name=tool.name,
                    alias=tool.alias,
                    member=tool.member,
                    grant_revision=tool.grant_revision,
                    revision=tool.revision,
                )
                for tool in view.bound_tools
            ]
            if view is not None
            else []
        )
        policy = config.ability_grant_policy
        grant_policy = (
            AbilityGrantPolicyView(
                mode=policy.mode,
                digest=policy.digest,
                rules=len(policy.rules),
            )
            if policy is not None
            else None
        )
        return AbilityAdminView(
            project_id=resolved.project_id,
            agent_id=agent_id,
            revision=revision,
            run_id=view.run_id if view is not None else None,
            lifecycle=AbilityLifecycleView(
                state=state,  # type: ignore[arg-type]
                requires_respawn=requires_respawn,
                pending_reasons=pending_reasons,
            ),
            grant_policy=grant_policy,
            grants=[_grant_view(record, digests) for record in grants],
            bound_tools=bound_tools,
            surface=surface,
            changes_cursor=0,
        )

    async def status() -> dict[str, Any]:
        reason: str | None = None
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
        resolved = await _resolve(request, project_id)
        try:
            view = await resolved.handle.store.view(resolved.handle.operator, agent_id)
        except AccessError as error:
            _raise_from_access(error)
        return _jsonable(view)

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
        resolved = await _resolve(request, project_id)
        return _jsonable(await _admin_view(resolved, agent_id))

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
        resolved = await _resolve(request, project_id)
        try:
            rows = await resolved.handle.store.changes(
                resolved.handle.operator, after_seq=after_seq, limit=limit
            )
        except AccessError as error:
            _raise_from_access(error)
        return _jsonable(_changes_page(rows, after_seq, limit))

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
        resolved = await _resolve(request, project_id)
        store, operator = resolved.handle.store, resolved.handle.operator
        try:
            grants = await store.grants(operator, agent_id)
            grant = next((item for item in grants if item.alias == alias), None)
            if grant is None:
                raise AbilityApiError("not_found", f"该 Agent 未获授能力：{alias}")
            fence = await store.resolve(operator, grant.instance_id, member, global_instance=True)
            result = await store.read_content(
                operator, fence, ContentReadRequest(offset=offset, limit=limit)
            )
        except AccessError as error:
            _raise_from_access(error)
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
        resolved: _Resolved, agent_id: str, revision: int, reason: str
    ) -> dict[str, Any]:
        view = await _try_view(resolved.handle.store, resolved.handle.operator, agent_id)
        await _publish_tree(resolved.project_id, agent_id, revision, reason)
        return _jsonable(AbilityTreeChangeResult(view=view, revision=revision))

    async def _put_grant(
        project_id: str,
        agent_id: str,
        alias: str,
        request: Request,
        _: None = Depends(_ensure_enabled),
    ) -> dict[str, Any]:
        resolved = await _resolve(request, project_id)
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
        try:
            revision = await resolved.handle.store.write_grant(
                resolved.handle.operator, agent_id, change
            )
        except AccessError as error:
            await _write_error(resolved.handle.store, resolved.handle.operator, agent_id, error)
        return await _after_write(resolved, agent_id, revision, "grant")

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
        resolved = await _resolve(request, project_id)
        change = RevokeAbilityRequest(
            alias=alias, expected_revision=expected_revision, members=members
        )
        try:
            revision = await resolved.handle.store.write_revoke(
                resolved.handle.operator, agent_id, change
            )
        except AccessError as error:
            await _write_error(resolved.handle.store, resolved.handle.operator, agent_id, error)
        return await _after_write(resolved, agent_id, revision, "revoke")

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
        resolved = await _resolve(request, project_id)
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
        try:
            revision = await resolved.handle.store.write_bind_tool(
                resolved.handle.operator, agent_id, change
            )
        except AccessError as error:
            await _write_error(resolved.handle.store, resolved.handle.operator, agent_id, error)
        return await _after_write(resolved, agent_id, revision, "bind_tool")

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
        resolved = await _resolve(request, project_id)
        change = UnbindToolRequest(tool_name=tool_name, expected_revision=expected_revision)
        try:
            revision = await resolved.handle.store.write_unbind_tool(
                resolved.handle.operator, agent_id, change
            )
        except AccessError as error:
            await _write_error(resolved.handle.store, resolved.handle.operator, agent_id, error)
        return await _after_write(resolved, agent_id, revision, "unbind_tool")

    router.add_api_route(
        "/projects/{project_id}/agents/{agent_id}/ability-tree/tools/{tool_name}",
        _delete_tool,
        methods=["DELETE"],
    )

    async def _packages(_: None = Depends(_ensure_catalog)) -> dict[str, Any]:
        try:
            catalog = build_package_catalog(config.ability.install_root)
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(catalog)

    router.add_api_route("/ability-packages", _packages, methods=["GET"])

    async def _package_detail(
        namespace: str,
        name: str,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        if not valid_identity_segment(namespace) or not valid_identity_segment(name):
            raise AbilityApiError("invalid_request", "能力包身份不合法")
        try:
            detail = build_package_detail(config.ability.install_root, namespace, name)
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(detail)

    router.add_api_route("/ability-packages/{namespace}/{name}", _package_detail, methods=["GET"])

    async def _instances(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        resolved = await _resolve(request, project_id)
        try:
            view = await build_instances_view(
                resolved.handle.store, resolved.handle.operator, project_id
            )
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(view)

    router.add_api_route("/projects/{project_id}/ability-instances", _instances, methods=["GET"])

    async def _package_groups(_: None = Depends(_ensure_catalog)) -> dict[str, Any]:
        try:
            groups = await build_package_groups(config.ability.install_root)
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(groups)

    router.add_api_route("/ability-package-groups", _package_groups, methods=["GET"])

    async def _project_package_groups(
        project_id: str,
        request: Request,
        _: None = Depends(_ensure_catalog),
    ) -> dict[str, Any]:
        resolved = await _resolve(request, project_id)
        try:
            groups = await build_package_groups(
                config.ability.install_root,
                store=resolved.handle.store,
                operator=resolved.handle.operator,
                project_id=project_id,
            )
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(groups)

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
        resolved = await _resolve(request, project_id)
        try:
            groups = await build_package_groups(
                config.ability.install_root,
                store=resolved.handle.store,
                operator=resolved.handle.operator,
                project_id=project_id,
                agent_id=agent_id,
            )
        except CatalogError as error:
            _catalog_error(error)
        return _jsonable(groups)

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
                {OBSERVER_ENDPOINT, ABILITY_ACCESS, PROJECT_STORE, OBSERVER_EVENT_BUS}
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
        access = ctx.get(ABILITY_ACCESS.name)
        event_bus = ctx.get(OBSERVER_EVENT_BUS.name)
        router = build_ability_control_router(
            config=self._config,
            project_store=project_store,
            access=access,
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
