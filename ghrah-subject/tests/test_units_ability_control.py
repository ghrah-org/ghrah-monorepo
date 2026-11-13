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
from ghrah.core.read.ability_catalog import (
    CatalogError,
    build_package_catalog,
    build_package_groups,
)
from ghrah.core.wiring import build_core_port
from ghrah.protocol.ability_control import (
    AbilityMcpProbeView,
    AbilityMcpServerInfoView,
    AbilityMcpToolView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    GrantAbilityRequest,
)
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode
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
    store_path: str | None = None


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

    async def enable_tool(self, *, agent_id: str, change: Any) -> AbilityTreeChangeResult:
        view = await self.view(None, agent_id)
        if self.error is not None:
            raise ApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="abilities.enable_tool",
                    detail="conflict",
                    details={"revision": 6, "view": view.model_dump(mode="json")},
                )
            )
        return AbilityTreeChangeResult(revision=5, view=view)

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


@pytest.fixture
async def make_app():
    ports = []

    def build(config: SubjectConfig, handle: _Handle, bus: _Bus, probe: Any = None) -> FastAPI:
        # 关系库由测试布置事实，HTTP 通过独立的真实 Core 端口读取。
        port = build_core_port(
            resolve_db_path=lambda _: handle.store_path or "",
            install_root=config.ability.install_root,
        )
        ports.append(port)

        class RoutedPort:
            @property
            def abilities(self):
                if probe is None:
                    return port.abilities

                async def run_probe(*, namespace, name, version, request):
                    try:
                        return await probe(
                            config.ability.install_root,
                            namespace,
                            name,
                            version,
                            request,
                            db_path=config.agentconf_db_path,
                        )
                    except CatalogError as exc:
                        code = (
                            ProblemCode.FAILED_PRECONDITION
                            if exc.code == "invalid_change"
                            else ProblemCode.NOT_FOUND
                        )
                        raise ApiError(
                            Problem.make(
                                code,
                                capability="abilities.package_mcp_probe",
                                detail=exc.message,
                                details={"reason": "definition_not_mcp"}
                                if exc.code == "invalid_change"
                                else {},
                            )
                        ) from exc

                return SimpleNamespace(package_mcp_probe=run_probe)

            def project(self, project_id):
                if isinstance(handle.store, _FakeStore):
                    return SimpleNamespace(abilities=handle.store)
                return port.project(project_id)

        app = FastAPI()
        app.include_router(
            build_ability_control_router(
                config=config,
                project_store=_ProjectStore(handle.project_id, "/tmp/project"),
                core_port=RoutedPort(),
                event_bus=bus,
            )
        )
        register_ability_control_error_handlers(app)
        return app

    yield build
    for port in ports:
        await port.aclose()


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
        ctx.provide(CORE_PORT_SERVICE.name, object())
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


async def test_admin_and_tree_rest_behaviour(tmp_path: Any, make_app) -> None:
    store, operator = await _open_store(tmp_path)
    try:
        config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
        bus = _Bus()
        app = make_app(config, _Handle("proj", store, operator, str(tmp_path / "ability.db")), bus)
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
            app = make_app(
                disabled, _Handle("proj", store, operator, str(tmp_path / "ability.db")), _Bus()
            )
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


async def test_write_publishes_tree_event_and_maps_conflict(tmp_path: Any, make_app) -> None:
    del tmp_path
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    bus = _Bus()
    store = _FakeStore()
    app = make_app(config, _Handle("proj", store, object()), bus)  # type: ignore[arg-type]
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

        conflict_app = make_app(
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


async def test_package_catalog_is_readable_and_writes_stay_closed(tmp_path: Any, make_app) -> None:
    store, operator = await _open_store(tmp_path)
    try:
        config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
        app = make_app(
            config, _Handle("proj", store, operator, str(tmp_path / "ability.db")), _Bus()
        )
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
            closed_app = make_app(
                closed_config,
                _Handle("proj", store, operator, str(tmp_path / "ability.db")),
                _Bus(),
            )
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
            disabled_app = make_app(
                disabled, _Handle("proj", store, operator, str(tmp_path / "ability.db")), _Bus()
            )
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


async def test_package_version_profile_route_returns_member_details(make_app) -> None:
    """画像端点：内置版本可读、成员明细齐全、错误按 400/404 分类、无绝对路径。"""
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    app = make_app(config, _Handle("proj", None, None), _Bus())
    server, task, port = await _serve(app)
    base = f"http://127.0.0.1:{port}"
    try:
        status, profile = await _request(f"{base}/api/v1/ability-packages/ghrah/fs/versions/0.1.0")
        assert status == 200
        assert profile["format"] == "ghrah.ability-control.package-version.v1"
        assert profile["origin"] == "builtin"
        assert profile["definition_origin"] == "source"
        assert len(profile["operations"]) == 7
        assert profile["verify"] is None and profile["mcp"] is None
        entries = [item["entry"] for item in profile["operations"] if item["entry"] is not None]
        assert entries and all(item["path"].startswith("operations/") for item in entries)
        _reject_root(profile)
        assert "/home/" not in json.dumps(profile)

        missing_status, missing = await _request(
            f"{base}/api/v1/ability-packages/ghrah/fs/versions/9.9.9"
        )
        assert missing_status == 404
        assert missing["error"]["code"] == "not_found"
        bad_status, bad = await _request(f"{base}/api/v1/ability-packages/Ghrah/fs/versions/0.1.0")
        assert bad_status == 400
        assert bad["error"]["code"] == "invalid_request"
        bad_version_status, _bad_version = await _request(
            f"{base}/api/v1/ability-packages/ghrah/fs/versions/latest"
        )
        assert bad_version_status == 400
    finally:
        server.should_exit = True
        await task


async def test_package_mcp_probe_route_maps_gate_result_and_catalog_errors(
    monkeypatch: pytest.MonkeyPatch, make_app
) -> None:
    """探测端点：注入缝可用、非 MCP 409 带 reason、未找到 404、请求越界 400、门控 503。"""
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    calls: list[tuple[Any, ...]] = []

    async def stub_probe(
        install_root: Any,
        namespace: str,
        name: str,
        version: str,
        request: Any,
        *,
        db_path: Any = None,
        connector: Any = None,
    ) -> AbilityMcpProbeView:
        calls.append((install_root, namespace, name, version, request, db_path))
        if name == "fs":
            raise CatalogError("invalid_change", "definition_not_mcp")
        if version == "9.9.9":
            raise CatalogError("not_found", "missing")
        return AbilityMcpProbeView(
            namespace=namespace,
            name=name,
            version=version,
            server="fetch",
            ok=True,
            phase="list_tools",
            elapsed_ms=3,
            protocol_version="2025-06-18",
            server_info=AbilityMcpServerInfoView(name="remote", version="1.0.0"),
            tools=[AbilityMcpToolView(name="echo", input_schema={"type": "object"})],
            tool_count=1,
            resource_count=0,
        )

    monkeypatch.setattr("ghrah.subject.units.ability_control._mcp_probe_ready", lambda: True)
    app = make_app(config, _Handle("proj", None, None), _Bus(), probe=stub_probe)
    server, task, port = await _serve(app)
    base = f"http://127.0.0.1:{port}"
    probe_path = "/api/v1/ability-packages/{ns}/{name}/versions/{version}/mcp/probe"
    try:
        status, payload = await _request(
            f"{base}/api/v1/abilities/status",
        )
        assert status == 200
        assert payload["features"]["mcp_probe"] is True

        rejected_status, rejected = await _request(
            f"{base}" + probe_path.format(ns="ghrah", name="fs", version="0.1.0"),
            method="POST",
            body={"timeout_ms": 5000},
        )
        assert rejected_status == 409
        assert rejected["error"]["code"] == "invalid_change"
        assert rejected["error"]["detail"]["reason"] == "definition_not_mcp"

        missing_status, missing = await _request(
            f"{base}" + probe_path.format(ns="mcp", name="fetch", version="9.9.9"),
            method="POST",
            body={"timeout_ms": 5000},
        )
        assert missing_status == 404
        assert missing["error"]["code"] == "not_found"

        ok_status, ok_payload = await _request(
            f"{base}" + probe_path.format(ns="mcp", name="fetch", version="1.0.0"),
            method="POST",
            body={"timeout_ms": 5000, "include_resources": True},
        )
        assert ok_status == 200
        assert ok_payload["ok"] is True
        assert ok_payload["tool_count"] == 1
        assert ok_payload["server"] == "fetch"
        assert calls[-1][4].include_resources is True
        assert calls[-1][5] is None  # db_path 由 config.agentconf_db_path 提供（此处未配置）

        invalid_status, invalid = await _request(
            f"{base}" + probe_path.format(ns="mcp", name="fetch", version="1.0.0"),
            method="POST",
            body={"timeout_ms": 999},
        )
        assert invalid_status == 400
        assert invalid["error"]["code"] == "invalid_request"
    finally:
        server.should_exit = True
        await task

    monkeypatch.setattr(
        "ghrah.subject.units.ability_control._mcp_probe_ready",
        lambda: False,
    )
    gated = make_app(config, _Handle("proj", None, None), _Bus())
    server, task, port = await _serve(gated)
    try:
        gated_status, gated_payload = await _request(
            f"http://127.0.0.1:{port}" + probe_path.format(ns="mcp", name="fetch", version="1.0.0"),
            method="POST",
            body={"timeout_ms": 5000},
        )
        assert gated_status == 503
        assert gated_payload["error"]["code"] == "capability_unavailable"
    finally:
        server.should_exit = True
        await task
