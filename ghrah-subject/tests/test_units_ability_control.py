# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""能力管理面 unit：路由装配、状态、认证与 REST 端点行为。"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.protocol.ability_tree import AbilityTreeView, BindToolRequest, GrantAbilityRequest
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active
from starlette.requests import Request

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.runtime.service_keys import (
    ABILITY_ACCESS,
    OBSERVER_EVENT_BUS,
    PROJECT_STORE,
)
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
    access = _Access(handle, runtime)
    app = FastAPI()
    app.include_router(
        build_ability_control_router(
            config=config,
            project_store=_ProjectStore("proj", "/tmp/proj"),
            access=access,  # type: ignore[arg-type]
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
        ctx.provide(ABILITY_ACCESS.name, _Access(_Handle("proj", None, None)))
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
        access=_Access(_Handle("proj", None, None)),  # type: ignore[arg-type]
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
        access=_Access(_Handle("proj", None, None)),  # type: ignore[arg-type]
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
            finally:
                closed_server.should_exit = True
                await closed_task
        finally:
            server.should_exit = True
            await task
    finally:
        await store.close()
