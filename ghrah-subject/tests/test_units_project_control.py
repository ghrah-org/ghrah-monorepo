# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 管理 REST 读面：路由、门控、鉴权与「与 WS 同形」验收。

测试基建说明：``ProjectUnit`` 的 SQLite 连接与生命周期锁都在当前事件循环
创建，因此 HTTP 走同一循环内的 ASGI transport（``httpx2``），不用
``fastapi.testclient.TestClient``（它会另起线程/事件循环，live ctx 跨循环有风险）。

覆盖：
- GET /api/v1/projects：默认只列活跃、archived=true 只列归档、status 过滤；
- GET /api/v1/projects/{id}：详情与 WS ``project_get`` 同形、归档可读、404；
- 门控（observer_http_api_enabled=false → 503）与鉴权（非回环无 Bearer → 401）；
- unit 挂载：``ProjectControlUnit`` 在 observer app 上注册两条读路由。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import RecoveryConfig, SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry
from ghrah.subject.runtime.ouroboros_bridge import bridge_command, mount_unit
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    OBSERVER_ENDPOINT,
    PROJECT_MANAGER,
)
from ghrah.subject.unit.base import RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units import mount_builtin_units
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.project import ProjectUnit
from ghrah.subject.units.project_control import (
    ProjectControlUnit,
    build_project_control_router,
)
from ghrah.subject.units.websocket_observer_endpoint import (
    WebSocketObserverEndpointUnit,
)


class _FakeCoreUnit(SubjectUnit):
    """假 CoreUnit：只返回成功回执。"""

    def __init__(self, cluster_id: str) -> None:
        self._meta = UnitMeta(
            name=f"fake-core-{cluster_id}",
            routes=RouteSpec(commands=frozenset()),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: Any
    ) -> dict[str, Any]:
        return {"success": True, "data": {}}


def _fake_registry() -> CoreClusterRegistry:
    return CoreClusterRegistry(
        unit_factory=lambda cluster_id, project_id, locator: _FakeCoreUnit(cluster_id)
    )


def _config(tmp_path: Path, *, enabled: bool = True) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        observer_http_api_enabled=enabled,
        recovery_slice=RecoveryConfig(enabled=False, reconcile_on_start=False),
    )


@asynccontextmanager
async def _boot(
    tmp_path: Path, *, enabled: bool = True
) -> AsyncIterator[tuple[Context, FastAPI, SubjectConfig]]:
    """挂载 coexistence + ProjectUnit + observer endpoint，并构造读面 app。"""
    config = _config(tmp_path, enabled=enabled)
    async with Context() as ctx:
        registry = _fake_registry()
        registry.bind(ctx)
        ctx.provide(CORE_CLUSTER_REGISTRY.name, registry)

        await mount_builtin_units(ctx, config, profile="coexistence")
        for unit in (WebSocketObserverEndpointUnit(config), ProjectUnit(config)):
            fiber = ctx.plugin(mount_unit(unit))
            await wait_active(fiber)

        app = FastAPI()
        register_ability_control_error_handlers(app)
        app.include_router(build_project_control_router(config=config, ctx=ctx))
        try:
            yield ctx, app, config
        finally:
            await registry.stop()


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create_project(ctx: Context, name: str) -> dict[str, Any]:
    result = await bridge_command(
        ctx,
        "project_create",
        {"name": name, "writable_workspaces": []},
    )
    assert result["success"], result
    return result["data"]["project"]


async def _archive_project(ctx: Context, project: dict[str, Any]) -> dict[str, Any]:
    result = await bridge_command(
        ctx,
        "project_archive",
        {"project_id": project["project_id"], "expected_version": project["version"]},
    )
    assert result["success"], result
    return result["data"]["project"]


async def _list_ws(ctx: Context, payload: dict[str, Any]) -> dict[str, Any]:
    result = await bridge_command(ctx, "project_list", payload)
    assert result["success"], result
    return result["data"]


# ─── unit 挂载 ───


async def test_unit_mounts_read_routes_onto_observer_app(tmp_path: Path) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        registry = _fake_registry()
        registry.bind(ctx)
        ctx.provide(CORE_CLUSTER_REGISTRY.name, registry)
        await mount_builtin_units(ctx, config, profile="coexistence")

        endpoint = WebSocketObserverEndpointUnit(config)
        project = ProjectUnit(config)
        control = ProjectControlUnit(config)
        for unit in (endpoint, project, control):
            fiber = ctx.plugin(mount_unit(unit))
            await wait_active(fiber)
        try:
            assert control.meta.routes.commands == frozenset()
            assert control.meta.requires == frozenset({OBSERVER_ENDPOINT, PROJECT_MANAGER})
            paths = {route.path for route in control.router.routes}
            assert "/api/v1/projects" in paths
            assert "/api/v1/projects/{project_id}" in paths
            # 路由已挂到 observer app 上
            assert any(
                getattr(route, "original_router", None) is control.router
                for route in endpoint.app.routes
            )
        finally:
            await registry.stop()


# ─── 读面行为 ───


async def test_list_defaults_to_active_projects(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        first = await _create_project(ctx, "active-1")
        second = await _create_project(ctx, "active-2")
        archived = await _archive_project(ctx, await _create_project(ctx, "archived-1"))

        async with _client(app) as client:
            response = await client.get("/api/v1/projects")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        assert {item["project_id"] for item in body["projects"]} == {
            first["project_id"],
            second["project_id"],
        }
        assert all(item["archived_at"] is None for item in body["projects"])
        assert archived["project_id"] not in {item["project_id"] for item in body["projects"]}


async def test_list_archived_only(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        await _create_project(ctx, "active-1")
        archived = await _archive_project(ctx, await _create_project(ctx, "archived-1"))

        async with _client(app) as client:
            response = await client.get("/api/v1/projects", params={"archived": "true"})
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["projects"][0]["project_id"] == archived["project_id"]


async def test_list_filters_by_status(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        paused = await _create_project(ctx, "paused-1")
        await _create_project(ctx, "active-1")
        result = await bridge_command(
            ctx,
            "project_pause",
            {"project_id": paused["project_id"]},
        )
        assert result["success"], result

        async with _client(app) as client:
            paused_response = await client.get("/api/v1/projects", params={"status": "paused"})
            active_response = await client.get("/api/v1/projects", params={"status": "active"})
        assert paused_response.status_code == 200
        assert {item["project_id"] for item in paused_response.json()["projects"]} == {
            paused["project_id"]
        }
        assert active_response.status_code == 200
        assert paused["project_id"] not in {
            item["project_id"] for item in active_response.json()["projects"]
        }


async def test_detail_matches_ws_payload(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "detail-1")
        ws_result = await bridge_command(ctx, "project_get", {"project_id": project["project_id"]})
        assert ws_result["success"], ws_result

        async with _client(app) as client:
            response = await client.get(f"/api/v1/projects/{project['project_id']}")
        assert response.status_code == 200
        assert response.json() == ws_result["data"]["project"]


async def test_detail_reads_archived_project(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        archived = await _archive_project(ctx, await _create_project(ctx, "archived-1"))

        async with _client(app) as client:
            response = await client.get(f"/api/v1/projects/{archived['project_id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["archived_at"] is not None
        assert body["status"] == "stopped"


async def test_list_matches_ws_payload(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        await _create_project(ctx, "list-1")
        await _archive_project(ctx, await _create_project(ctx, "list-2"))
        payloads = (
            {"archived": False},
            {"archived": True},
            {"archived": False, "status": "active"},
        )
        for payload in payloads:
            ws_data = await _list_ws(ctx, payload)
            params = {key: str(value).lower() for key, value in payload.items()}
            async with _client(app) as client:
                response = await client.get("/api/v1/projects", params=params)
            assert response.status_code == 200
            assert response.json() == ws_data


async def test_unknown_project_returns_404(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        async with _client(app) as client:
            response = await client.get("/api/v1/projects/missing")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_disabled_switch_returns_503(tmp_path: Path) -> None:
    async with _boot(tmp_path, enabled=False) as (ctx, app, _):
        async with _client(app) as client:
            listing = await client.get("/api/v1/projects")
            detail = await client.get("/api/v1/projects/anything")
        for response in (listing, detail):
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "capability_unavailable"


async def test_non_loopback_requires_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        await _create_project(ctx, "auth-1")
        monkeypatch.setattr(
            "ghrah.subject.units.project_control._authorized",
            lambda request, config: False,
        )
        async with _client(app) as client:
            response = await client.get("/api/v1/projects")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "access_denied"
