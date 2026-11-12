# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力管理面 unit：路由装配、状态、认证与 REST 端点行为。"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity, GrantRecord
from ghrah.ability.loading.ability_catalog import load_ability_definitions
from ghrah.ability.versions import AbilityVersion
from ghrah.protocol.ability_tree import AbilityTreeView, BindToolRequest, GrantAbilityRequest
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active
from starlette.requests import Request

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.runtime.service_keys import (
    CORE_PORT_SERVICE,
    OBSERVER_EVENT_BUS,
    PROJECT_STORE,
)
from ghrah.subject.units.ability_catalog import build_package_catalog, build_package_groups
from ghrah.subject.units.ability_control import (
    AbilityControlUnit,
    _authorized,
    build_ability_control_router,
    register_ability_control_error_handlers,
)
from ghrah.subject.units.websocket_observer_endpoint import (
    WebSocketObserverEndpointUnit,
)


class _ProjectStore:
    """最小项目读面：只回答 locator，不存在返回 None。"""

    def __init__(self, project_id: str, locator: str) -> None:
        self._project_id = project_id
        self._locator = locator

    async def get(
        self,
        project_id: str,
        *,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> Any:
        del include_archived, include_deleted
        if project_id != self._project_id:
            return None
        return SimpleNamespace(project_id=project_id, project_root_locator=self._locator)


@dataclass(frozen=True)
class _Handle:
    project_id: str
    store: Any
    operator: Any


class _Access:
    def __init__(self, handle: _Handle, runtime: tuple[str, ...] | None = None) -> None:
        self._handle = handle
        self._runtime = runtime

    async def acquire(self, project_id: str, project_root_locator: str) -> _Handle:
        del project_id, project_root_locator
        return self._handle

    async def agent_tool_snapshot(self, project_id: str, agent_id: str) -> tuple[str, ...] | None:
        del project_id, agent_id
        return self._runtime

    async def close(self) -> None:
        return None


class _FakePort:
    """最小 Core 端口替身：把能力调用映射回本文件的假 store。

    这一层只用于验证 Subject 的 HTTP 适配（鉴权/错误码/事件/开关），
    Core 的真实行为由 ghrah-core 自己的测试覆盖。
    """

    def __init__(self, handle: _Handle, runtime: tuple[str, ...] | None = None) -> None:
        self._handle = handle
        self._runtime = runtime
        self._abilities = _FakeAbilities(handle, runtime)

    @property
    def abilities(self) -> Any:
        return self._abilities

    def project(self, project_id: str) -> Any:
        del project_id
        return self


class _FakeAbilities:
    def __init__(self, handle: _Handle, runtime: tuple[str, ...] | None) -> None:
        self._handle = handle
        self._runtime = runtime

    async def status(self) -> Any:
        from ghrah.protocol.ability_control import AbilityApiFeatures, AbilityApiStatus

        return AbilityApiStatus(
            ability_source="builtin",
            operator="deployment",
            features=AbilityApiFeatures(tree=True, grants_write=True),
        )

    async def tree(self, *, agent_id: str) -> Any:
        try:
            return await self._handle.store.view(self._handle.operator, agent_id)
        except AccessError as error:
            raise _api_error("NOT_FOUND", str(error)) from error

    async def admin(self, *, agent_id: str) -> Any:
        from ghrah.protocol.ability_control import (
            AbilityAdminView,
            AbilityLifecycleView,
            AbilitySurfaceView,
        )

        assembled = True
        revision = 0
        try:
            revision = await self._handle.store.agent_revision(self._handle.operator, agent_id)
        except AccessError as error:
            if getattr(error, "code", None) != "not_found" and str(error) != "not_found":
                raise
            assembled = False
        if assembled:
            try:
                await self._handle.store.grants(self._handle.operator, agent_id)
            except AccessError:
                assembled = False
        state = "unassembled" if not assembled else ("running" if self._runtime else "stopped")
        return AbilityAdminView(
            project_id=self._handle.project_id,
            agent_id=agent_id,
            revision=revision,
            run_id=None,
            lifecycle=AbilityLifecycleView(state=state),
            grants=[],
            bound_tools=[],
            surface=AbilitySurfaceView(),
            changes_cursor=0,
        )

    async def changes(self, *, after_seq: int = 0, limit: int = 100) -> Any:
        from ghrah.protocol.ability_control import AbilityChangesPage

        return AbilityChangesPage(changes=[], next_after_seq=after_seq, has_more=False)

    async def content(self, **kwargs: Any) -> Any:
        from ghrah.protocol.errors import ApiError
        from ghrah.protocol.problem import Problem, ProblemCode

        del kwargs
        raise ApiError(Problem.make(ProblemCode.NOT_FOUND, capability="abilities.content"))

    async def _write(self, method: str, agent_id: str, change: Any) -> Any:
        from ghrah.protocol.ability_control import AbilityTreeChangeResult

        try:
            revision = await getattr(self._handle.store, method)(
                self._handle.operator, agent_id, change
            )
        except AccessError as error:
            code = getattr(error, "code", None) or str(error)
            if "conflict" not in str(code):
                raise _api_error("INTERNAL", code) from error
            details: dict[str, Any] = {}
            try:
                details["revision"] = await self._handle.store.agent_revision(
                    self._handle.operator, agent_id
                )
            except AccessError:
                pass
            try:
                view = await self._handle.store.view(self._handle.operator, agent_id)
                details["view"] = view.model_dump(mode="json")
            except AccessError:
                pass
            raise _api_error("FAILED_PRECONDITION", "conflict", details) from error
        try:
            view = await self._handle.store.view(self._handle.operator, agent_id)
        except AccessError:
            view = None
        return AbilityTreeChangeResult(view=view, revision=revision)

    async def grant(self, *, agent_id: str, change: Any) -> Any:
        return await self._write("write_grant", agent_id, change)

    async def revoke(self, *, agent_id: str, change: Any) -> Any:
        return await self._write("write_revoke", agent_id, change)

    async def enable_tool(self, *, agent_id: str, change: Any) -> Any:
        return await self._write("write_bind_tool", agent_id, change)

    async def disable_tool(self, *, agent_id: str, change: Any) -> Any:
        return await self._write("write_unbind_tool", agent_id, change)


def _api_error(code: str, detail: Any, details: dict[str, Any] | None = None) -> Any:
    """构造协议层的 ApiError。"""
    from ghrah.protocol.errors import ApiError
    from ghrah.protocol.problem import Problem, ProblemCode

    return ApiError(
        Problem.make(ProblemCode(code), capability="test", detail=str(detail), details=details)
    )


class _Bus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        self.calls.append((event_type, payload))
        return 1


class _FakeStore:
    """只提供写端点的最小假 store；用于验证事件与冲突映射。"""

    def __init__(self, error: str | None = None) -> None:
        self.error = error

    async def write_bind_tool(self, actor: Any, agent: str, change: Any) -> int:
        del actor, agent, change
        if self.error is not None:
            raise AccessError(self.error)  # type: ignore[arg-type]
        return 5

    async def agent_revision(self, actor: Any, agent: str) -> int:
        del actor, agent
        return 6

    async def grants(self, actor: Any, agent: str) -> list[Any]:
        del actor, agent
        return []

    async def view(self, actor: Any, agent: str) -> Any:
        del actor
        return AbilityTreeView.model_validate(
            {
                "format": "ghrah.ability-tree.v2",
                "project_id": "proj",
                "agent_id": agent,
                "run_id": "run-1",
                "revision": 5,
                "abilities": [],
                "bound_tools": [],
                "runtime": None,
            }
        )


async def _open_store(tmp_path: Any) -> tuple[AbilityAccessStore, AccessIdentity]:
    store = await AbilityAccessStore.open(
        tmp_path / "ability.db",
        "proj",
        operators=frozenset({"deployment"}),
        runs=lambda agent: None,
    )
    operator = AccessIdentity("proj", "deployment", None, operator=True, source="admin")
    await store.ensure_agent(operator, "agent-1")
    return store, operator


async def _serve(app: FastAPI) -> tuple[uvicorn.Server, asyncio.Task[Any], int]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(400):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    sockets = server.servers[0].sockets
    return server, task, sockets[0].getsockname()[1]


async def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    def run() -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    return await asyncio.to_thread(run)


def _app(config: SubjectConfig, handle: _Handle, bus: _Bus, runtime: Any = None) -> FastAPI:
    core_port = _FakePort(handle, runtime)
    app = FastAPI()
    app.include_router(
        build_ability_control_router(
            config=config,
            project_store=_ProjectStore("proj", "/tmp/proj"),
            core_port=core_port,
            event_bus=bus,
        )
    )
    register_ability_control_error_handlers(app)
    return app


def _request_with_host(host: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
        ],
        "client": (host, 1234),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


async def test_unit_mounts_router_and_status_route_on_observer_app() -> None:
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    async with Context() as ctx:
        endpoint = WebSocketObserverEndpointUnit(config)
        fiber = ctx.plugin(mount_unit(endpoint))
        await wait_active(fiber)
        ctx.provide(
            PROJECT_STORE.name,
            _ProjectStore("proj", "/tmp/proj"),
        )
        ctx.provide(CORE_PORT_SERVICE.name, _FakePort(_Handle("proj", None, None)))
        unit = AbilityControlUnit(config)
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)
        paths = {route.path for route in unit.router.routes}
        assert "/api/v1/abilities/status" in paths
        assert "/api/v1/projects/{project_id}/agents/{agent_id}/ability-tree" in paths
        # FastAPI 0.162 将 include_router 记录为 _IncludedRouter，不摊平子路由。
        assert any(
            getattr(route, "original_router", None) is unit.router for route in endpoint.app.routes
        )


async def test_status_reports_enabled_and_disabled() -> None:
    enabled = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    router = build_ability_control_router(
        config=enabled,
        project_store=_ProjectStore("proj", "/tmp/proj"),
        core_port=_FakePort(_Handle("proj", None, None)),
        event_bus=_Bus(),
    )
    status = next(route.endpoint for route in router.routes if route.path.endswith("/status"))
    payload = await status()
    assert payload["ability_source"] == "builtin"
    assert payload["features"]["tree"] is True
    assert payload["features"]["packages_read"] is True
    assert payload["features"]["versions_read"] is True
    assert payload["features"]["packages_install"] is False
    assert payload["legacy_deprecated"] is False

    disabled = SubjectConfig(ability_source="builtin", observer_http_api_enabled=False)
    router = build_ability_control_router(
        config=disabled,
        project_store=_ProjectStore("proj", "/tmp/proj"),
        core_port=_FakePort(_Handle("proj", None, None)),
        event_bus=_Bus(),
    )
    status = next(route.endpoint for route in router.routes if route.path.endswith("/status"))
    payload = await status()
    assert payload["features"]["tree"] is False
    assert payload["features"]["packages_read"] is False
    assert "未启用" in str(payload["reason"])

    # 部署配置已只接受 builtin。这里直接改读到的来源，确认 HTTP 层仍会关闭目录。
    closed = SubjectConfig(observer_http_api_enabled=True)
    closed.ability_source = "legacy"
    router = build_ability_control_router(
        config=closed,
        project_store=_ProjectStore("proj", "/tmp/proj"),
        access=_Access(_Handle("proj", None, None)),  # type: ignore[arg-type]
        event_bus=_Bus(),
    )
    status = next(route.endpoint for route in router.routes if route.path.endswith("/status"))
    payload = await status()
    assert payload["ability_source"] == "legacy"
    assert payload["legacy_deprecated"] is True
    assert payload["features"]["packages_read"] is False
    assert payload["features"]["versions_read"] is False


def test_authorization_trusts_loopback_and_requires_token_elsewhere() -> None:
    config = SubjectConfig(observer_http_api_token="secret")
    assert _authorized(_request_with_host("127.0.0.1"), config) is True
    assert _authorized(_request_with_host("testclient"), config) is True
    assert _authorized(_request_with_host("10.0.0.5"), config) is False
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": "Bearer wrong"}), config)
        is False
    )
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": "Bearer secret"}), config)
        is True
    )
    no_token = SubjectConfig()
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": "Bearer x"}), no_token)
        is False
    )


async def test_admin_and_tree_rest_behaviour(tmp_path: Any) -> None:
    store, operator = await _open_store(tmp_path)
    try:
        config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
        bus = _Bus()
        app = _app(config, _Handle("proj", store, operator), bus)
        server, task, port = await _serve(app)
        base = f"http://127.0.0.1:{port}"
        try:
            status, _ = await _request(f"{base}/api/v1/abilities/status")
            assert status == 200
            admin_status, admin = await _request(
                f"{base}/api/v1/projects/proj/agents/agent-1/ability-admin"
            )
            assert admin_status == 200
            assert admin["lifecycle"]["state"] == "stopped"
            assert admin["revision"] == 0
            ghost_status, ghost = await _request(
                f"{base}/api/v1/projects/proj/agents/ghost/ability-admin"
            )
            assert ghost_status == 200
            assert ghost["lifecycle"]["state"] == "unassembled"
            tree_status, tree = await _request(
                f"{base}/api/v1/projects/proj/agents/ghost/ability-tree"
            )
            assert tree_status == 404
            assert tree["error"]["code"] == "not_found"
            bad_status, bad = await _request(
                f"{base}/api/v1/projects/proj/agents/agent-1/ability-tree/grants/files",
                method="PUT",
                body={"alias": "files"},
            )
            assert bad_status == 400
            assert bad["error"]["code"] == "invalid_request"
            missing_status, missing = await _request(
                f"{base}/api/v1/projects/proj/agents/agent-1/ability-tree/tools/"
                "read_file?expected_revision=0",
                method="DELETE",
            )
            # 对不存在工具的解绑是幂等 no-op（关系修订不变），不是 not_found。
            assert missing_status == 200
            assert missing["revision"] == 0
            disabled = SubjectConfig(ability_source="builtin", observer_http_api_enabled=False)
            app = _app(disabled, _Handle("proj", store, operator), _Bus())
            server2, task2, port2 = await _serve(app)
            try:
                off_status, off = await _request(
                    f"http://127.0.0.1:{port2}/api/v1/projects/proj/agents/agent-1/ability-tree"
                )
                assert off_status == 503
                assert off["error"]["code"] == "capability_unavailable"
            finally:
                server2.should_exit = True
                await task2
        finally:
            server.should_exit = True
            await task
    finally:
        await store.close()


async def test_write_publishes_tree_event_and_maps_conflict(tmp_path: Any) -> None:
    del tmp_path
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    bus = _Bus()
    store = _FakeStore()
    app = _app(config, _Handle("proj", store, object()), bus)  # type: ignore[arg-type]
    server, task, port = await _serve(app)
    base = f"http://127.0.0.1:{port}"
    try:
        request = BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_file",
            expected_revision=1,
        )
        status, payload = await _request(
            f"{base}/api/v1/projects/proj/agents/agent-1/ability-tree/tools/read_file",
            method="PUT",
            body=request.model_dump(),
        )
        assert status == 200
        assert payload["revision"] == 5
        assert payload["view"]["agent_id"] == "agent-1"
        assert bus.calls and bus.calls[-1][0] == "ability_tree_changed"
        assert bus.calls[-1][1]["agent_id"] == "agent-1"

        conflict_app = _app(
            config,
            _Handle("proj", _FakeStore(error="conflict"), object()),  # type: ignore[arg-type]
            _Bus(),
        )
        server2, task2, port2 = await _serve(conflict_app)
        try:
            conflict_status, conflict = await _request(
                f"http://127.0.0.1:{port2}/api/v1/projects/proj/agents/agent-1/"
                "ability-tree/tools/read_file",
                method="PUT",
                body=request.model_dump(),
            )
            assert conflict_status == 409
            assert conflict["error"]["code"] == "conflict"
            assert conflict["error"]["detail"]["revision"] == 6
            assert conflict["error"]["detail"]["view"]["format"] == "ghrah.ability-tree.v2"
        finally:
            server2.should_exit = True
            await task2
    finally:
        server.should_exit = True
        await task


@pytest.mark.parametrize("grant", [{"alias": "files"}])
def test_grant_request_requires_expected_revision(grant: dict[str, Any]) -> None:
    with pytest.raises(Exception):
        GrantAbilityRequest.model_validate(grant)


async def test_ability_tree_event_is_forwarded_to_observer_bus() -> None:
    config = SubjectConfig()
    unit = WebSocketObserverEndpointUnit(config)
    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)
        adapter = ctx.get(OBSERVER_EVENT_BUS.name)
        await adapter.publish(
            "ability_tree_changed",
            {
                "project_id": "p1",
                "agent_id": "a1",
                "revision": 3,
                "reason": "grant",
                "resource": "/scheme/agent/a1",
            },
        )
        await adapter.publish(
            "ability_tree_changed",
            {"agent_id": "a1", "revision": 4},
        )
        events = unit.observer_event_bus.event_store.replay_since(0)
        assert events[-1]["type"] == "ability_tree_changed"
        assert events[-1]["payload"]["project_id"] == "p1"
        assert len(events) == 1


def _reject_root(value: Any) -> None:
    if isinstance(value, dict):
        assert "root" not in value
        for item in value.values():
            _reject_root(item)
    elif isinstance(value, list):
        for item in value:
            _reject_root(item)


async def test_package_catalog_is_readable_and_writes_stay_closed(tmp_path: Any) -> None:
    store, operator = await _open_store(tmp_path)
    try:
        config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
        app = _app(config, _Handle("proj", store, operator), _Bus())
        server, task, port = await _serve(app)
        base = f"http://127.0.0.1:{port}"
        try:
            status, catalog = await _request(f"{base}/api/v1/ability-packages")
            assert status == 200
            assert catalog["format"] == "ghrah.ability-control.packages.v1"
            assert catalog["installed_available"] is False
            assert catalog["packages"]
            assert all(item["origin"] == "builtin" for item in catalog["packages"])
            _reject_root(catalog)
            sample = next(item for item in catalog["packages"] if item["name"])
            detail_status, detail = await _request(
                f"{base}/api/v1/ability-packages/{sample['namespace']}/{sample['name']}"
            )
            assert detail_status == 200
            assert detail["full_name"] == sample["full_name"]
            assert detail["versions"]
            assert detail["versions"][0]["verify"] is None
            _reject_root(detail)
            instance_status, instances = await _request(
                f"{base}/api/v1/projects/proj/ability-instances"
            )
            assert instance_status == 200
            assert instances["instances"] == []
            group_status, groups = await _request(f"{base}/api/v1/ability-package-groups")
            assert group_status == 200
            assert groups["format"] == "ghrah.ability-control.package-groups.v1"
            assert groups["scope"] == "distribution"
            assert groups["project_id"] is None
            assert groups["agent_id"] is None
            assert groups["installed_available"] is False
            assert groups["groups"]
            assert any(
                version["origin"] == "builtin"
                for group in groups["groups"]
                for version in group["versions"]
            )
            assert all(
                version["project"] is None and version["agent"] is None
                for group in groups["groups"]
                for version in group["versions"]
            )
            _reject_root(groups)
            project_status, project_groups = await _request(
                f"{base}/api/v1/projects/proj/ability-package-groups"
            )
            assert project_status == 200
            assert project_groups["scope"] == "project"
            assert project_groups["project_id"] == "proj"
            assert all(
                version["project"]["committed"] is False and version["agent"] is None
                for group in project_groups["groups"]
                for version in group["versions"]
            )
            _reject_root(project_groups)
            agent_status, agent_groups = await _request(
                f"{base}/api/v1/projects/proj/agents/ghost/ability-package-groups"
            )
            assert agent_status == 200
            assert agent_groups["scope"] == "agent"
            assert agent_groups["agent_id"] == "ghost"
            assert all(
                version["agent"]["granted"] is False
                for group in agent_groups["groups"]
                for version in group["versions"]
            )
            _reject_root(agent_groups)
            closed, body = await _request(
                f"{base}/api/v1/ability-packages/import", method="POST", body={}
            )
            assert closed == 503
            assert body["error"]["code"] == "capability_unavailable"
            package = f"{base}/api/v1/ability-packages/{sample['namespace']}/{sample['name']}"
            for method, path in (
                ("POST", f"{package}/versions/0/verify"),
                ("DELETE", f"{package}/versions/0"),
                ("POST", f"{base}/api/v1/projects/proj/ability-instances/inst/select"),
            ):
                closed, body = await _request(path, method=method, body={})
                assert closed == 503, (method, path, body)
                assert body["error"]["code"] == "capability_unavailable"
            bad, rejected = await _request(f"{base}/api/v1/ability-packages/Bad/Name")
            assert bad == 400
            assert rejected["error"]["code"] == "invalid_request"
            closed_config = SubjectConfig(observer_http_api_enabled=True)
            closed_config.ability_source = "legacy"
            closed_app = _app(closed_config, _Handle("proj", store, operator), _Bus())
            closed_server, closed_task, closed_port = await _serve(closed_app)
            try:
                denied, denied_body = await _request(
                    f"http://127.0.0.1:{closed_port}/api/v1/ability-packages"
                )
                assert denied == 503
                assert denied_body["error"]["code"] == "capability_unavailable"
                assert denied_body["error"]["detail"]["legacy_deprecated"] is True
                for path in (
                    "/api/v1/ability-package-groups",
                    "/api/v1/projects/proj/ability-package-groups",
                    "/api/v1/projects/proj/agents/ghost/ability-package-groups",
                ):
                    denied, denied_body = await _request(f"http://127.0.0.1:{closed_port}{path}")
                    assert denied == 503
                    assert denied_body["error"]["code"] == "capability_unavailable"
            finally:
                closed_server.should_exit = True
                await closed_task
            disabled = SubjectConfig(ability_source="builtin", observer_http_api_enabled=False)
            disabled_app = _app(disabled, _Handle("proj", store, operator), _Bus())
            disabled_server, disabled_task, disabled_port = await _serve(disabled_app)
            try:
                for path in (
                    "/api/v1/ability-package-groups",
                    "/api/v1/projects/proj/ability-package-groups",
                    "/api/v1/projects/proj/agents/ghost/ability-package-groups",
                ):
                    denied, denied_body = await _request(f"http://127.0.0.1:{disabled_port}{path}")
                    assert denied == 503
                    assert denied_body["error"]["code"] == "capability_unavailable"
            finally:
                disabled_server.should_exit = True
                await disabled_task
        finally:
            server.should_exit = True
            await task
    finally:
        await store.close()


class _RelationStore:
    """只提供包组读取要用的关系库方法。"""

    def __init__(
        self,
        versions: list[AbilityVersion],
        instances: dict[str, dict[str, Any]],
        grants: list[GrantRecord],
        *,
        missing_agent: bool = False,
    ) -> None:
        self.versions = versions
        self.instances = instances
        self.grant_rows = grants
        self.missing_agent = missing_agent

    async def list_versions(self, actor: object) -> list[AbilityVersion]:
        del actor
        return self.versions

    async def list_instances(self, actor: object) -> list[str]:
        del actor
        return list(self.instances)

    async def instance(self, instance_id: str) -> dict[str, Any]:
        try:
            return self.instances[instance_id]
        except KeyError as exc:
            raise AccessError("not_found") from exc

    async def grants(self, actor: object, agent: str) -> list[GrantRecord]:
        del actor, agent
        if self.missing_agent:
            raise AccessError("not_found")
        return self.grant_rows


def _known_definition() -> Any:
    return next(iter(load_ability_definitions().values()))


async def test_package_groups_align_definition_and_relation() -> None:
    known = _known_definition()
    identity = known.identity
    instance_id = identity.full_name.replace(".", "-")
    known_version = AbilityVersion(
        "sha256:known",
        known.model_dump_json(),
        "implementation",
    )
    missing = known.model_copy(
        update={"identity": identity.model_copy(update={"namespace": "sample", "name": "reader"})}
    )
    missing_version = AbilityVersion(
        "sha256:missing",
        missing.model_dump_json(),
        "implementation",
    )
    broken = AbilityVersion("sha256:broken", "{}", "implementation")
    instances = {
        instance_id: {
            "id": instance_id,
            "revision": 4,
            "digest": "sha256:known",
            "definition": known.model_dump(mode="json"),
        }
    }
    grants = [
        GrantRecord(
            alias="notes",
            instance_id=instance_id,
            members=(),
            content=(),
            roots=(),
            axes={},
            revision=4,
            source="admin",
        )
    ]
    store = _RelationStore(
        [broken, missing_version, known_version],
        instances,
        grants,
    )
    view = await build_package_groups(
        None,
        store=store,
        operator=object(),
        project_id="proj",
        agent_id="agent-1",
    )
    payload = view.model_dump(mode="json")
    _reject_root(payload)
    assert "/home" not in json.dumps(payload)
    known_group = next(
        group for group in payload["groups"] if group["full_name"] == identity.full_name
    )
    known_row = next(row for row in known_group["versions"] if row["version"] == identity.version)
    assert known_row["origin"] == "builtin"
    assert known_row["project"]["committed"] is True
    assert known_row["project"]["selected"] is True
    assert known_row["project"]["instance_id"] == instance_id
    assert known_row["project"]["instance_revision"] == 4
    assert known_row["project"]["digest"] == "sha256:known"
    assert known_row["agent"]["granted"] is True
    assert known_row["agent"]["alias"] == "notes"
    for row in known_group["versions"]:
        if row is not known_row:
            assert row["agent"]["granted"] is False
    missing_group = next(
        group for group in payload["groups"] if group["full_name"] == "sample.reader"
    )
    missing_row = missing_group["versions"][0]
    assert missing_row["origin"] is None
    assert missing_row["problem"] == "definition_missing"
    assert missing_row["project"]["committed"] is True
    assert missing_row["agent"]["granted"] is False
    broken_rows = [
        row
        for group in payload["groups"]
        for row in group["versions"]
        if row["problem"] and str(row["problem"]).startswith("definition_unreadable")
    ]
    assert broken_rows
    assert broken_rows[0]["project"]["committed"] is True
    assert broken_rows[0]["project"]["digest"] == "sha256:broken"

    unassembled = _RelationStore(
        [broken, missing_version, known_version],
        instances,
        grants,
        missing_agent=True,
    )
    quiet = await build_package_groups(
        None,
        store=unassembled,
        operator=object(),
        project_id="proj",
        agent_id="ghost",
    )
    quiet_payload = quiet.model_dump(mode="json")
    assert quiet_payload["agent_id"] == "ghost"
    assert all(
        row["agent"]["granted"] is False
        for group in quiet_payload["groups"]
        for row in group["versions"]
    )
    distribution = await build_package_groups(None)
    names = [group.full_name for group in distribution.groups]
    assert "sample.reader" not in names
    assert all(
        row.project is None and row.agent is None
        for group in distribution.groups
        for row in group.versions
    )


def test_installed_available_reflects_the_real_install_root(tmp_path: Any) -> None:
    """安装根缺失、不是目录或指向链接时，目录不得声称安装侧可用。"""
    assert build_package_catalog(None).installed_available is False
    assert build_package_catalog(str(tmp_path / "missing")).installed_available is False

    readable = tmp_path / "ability"
    readable.mkdir()
    catalog = build_package_catalog(str(readable))
    assert catalog.installed_available is True
    assert all(row.origin == "builtin" for row in catalog.packages)

    as_file = tmp_path / "ability-file"
    as_file.write_text("not a directory", encoding="utf-8")
    assert build_package_catalog(str(as_file)).installed_available is False

    as_link = tmp_path / "ability-link"
    as_link.symlink_to(readable, target_is_directory=True)
    assert build_package_catalog(str(as_link)).installed_available is False


def test_installed_available_is_false_when_the_install_root_cannot_be_read(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """安装根存在但不可枚举时不可用，且不把整个目录读变成 500/503。"""
    locked = tmp_path / "locked"
    locked.mkdir()
    real_scandir = os.scandir

    def deny_scandir(path: Any) -> Any:
        if Path(path) == locked:
            raise PermissionError(13, "permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", deny_scandir)
    catalog = build_package_catalog(str(locked))
    assert catalog.installed_available is False
    assert all(row.origin == "builtin" for row in catalog.packages)
