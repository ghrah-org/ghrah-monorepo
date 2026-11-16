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
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.room.models import make_room_record
from ghrah.subject.room.store import RoomStore
from ghrah.subject.runtime.ouroboros_bridge import bridge_command, mount_unit
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    DESIRED_STATE_STORE,
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


# ─── 写面与生命周期 ───


async def _add_room(project: dict[str, Any]) -> str:
    """在项目 Root 的 rooms 库里布置一条房间记录（供 delete 门禁用例）。"""
    paths = ProjectPaths.from_locator(project["project_root_locator"])
    store = RoomStore(paths.room_db_path)
    await store.start()
    try:
        room = make_room_record(project_id=project["project_id"], name="room-1")
        await store.upsert(room)
        return room.room_id
    finally:
        await store.stop()


async def test_create_project_returns_201_with_location(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        async with _client(app) as client:
            response = await client.post("/api/v1/projects", json={"name": "created-via-rest"})
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "created-via-rest"
        assert body["version"] == 1
        assert body["workspaces"] == []
        assert response.headers["location"] == f"/api/v1/projects/{body['project_id']}"
        async with _client(app) as client:
            detail = await client.get(f"/api/v1/projects/{body['project_id']}")
        assert detail.status_code == 200


async def test_create_rejects_two_default_workspaces(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        first = tmp_path / "ws-a"
        second = tmp_path / "ws-b"
        first.mkdir()
        second.mkdir()
        body = {
            "name": "bad-workspaces",
            "writable_workspaces": [
                {"locator": str(first), "name": "a", "default_for_agents": True},
                {"locator": str(second), "name": "b", "default_for_agents": True},
            ],
        }
        async with _client(app) as client:
            response = await client.post("/api/v1/projects", json=body)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


async def test_patch_updates_and_bumps_version(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "patch-me")
        async with _client(app) as client:
            response = await client.patch(
                f"/api/v1/projects/{project['project_id']}",
                json={
                    "name": "renamed",
                    "description": "updated",
                    "expected_version": project["version"],
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["name"] == "renamed"
            assert body["description"] == "updated"
            assert body["version"] == project["version"] + 1
            detail = await client.get(f"/api/v1/projects/{project['project_id']}")
        assert detail.json()["name"] == "renamed"


async def test_patch_requires_change_and_version(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "patch-validation")
        async with _client(app) as client:
            empty = await client.patch(
                f"/api/v1/projects/{project['project_id']}",
                json={"expected_version": project["version"]},
            )
            no_version = await client.patch(
                f"/api/v1/projects/{project['project_id']}",
                json={"name": "renamed"},
            )
        assert empty.status_code == 400
        assert empty.json()["error"]["code"] == "invalid_request"
        assert no_version.status_code == 400


async def test_patch_stale_version_returns_conflict_detail(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "patch-conflict")
        async with _client(app) as client:
            response = await client.patch(
                f"/api/v1/projects/{project['project_id']}",
                json={"name": "stale", "expected_version": project["version"] + 5},
            )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["detail"]["current_version"] == project["version"]


async def test_patch_unknown_and_archived_projects(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        archived = await _archive_project(ctx, await _create_project(ctx, "patch-archived"))
        async with _client(app) as client:
            unknown = await client.patch(
                "/api/v1/projects/missing", json={"name": "x", "expected_version": 1}
            )
            archived_response = await client.patch(
                f"/api/v1/projects/{archived['project_id']}",
                json={"name": "x", "expected_version": archived["version"]},
            )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "not_found"
        assert archived_response.status_code == 409
        assert archived_response.json()["error"]["code"] == "resource_archived"


async def test_archive_and_restore_are_idempotent(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "lifecycle")
        async with _client(app) as client:
            archived = await client.post(
                f"/api/v1/projects/{project['project_id']}/archive",
                json={"expected_version": project["version"]},
            )
            assert archived.status_code == 200
            archived_body = archived.json()
            assert archived_body["archived_at"] is not None
            assert archived_body["status"] == "stopped"

            again = await client.post(
                f"/api/v1/projects/{project['project_id']}/archive",
                json={"expected_version": archived_body["version"]},
            )
            assert again.status_code == 200
            assert again.json()["version"] == archived_body["version"]

            restored = await client.post(
                f"/api/v1/projects/{project['project_id']}/restore",
                json={"expected_version": archived_body["version"]},
            )
            assert restored.status_code == 200
            restored_body = restored.json()
            assert restored_body["archived_at"] is None
            assert restored_body["status"] == "stopped"


async def test_archive_stale_version_returns_conflict(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "archive-conflict")
        async with _client(app) as client:
            response = await client.post(
                f"/api/v1/projects/{project['project_id']}/archive",
                json={"expected_version": project["version"] + 1},
            )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["detail"]["current_version"] == project["version"]


async def test_pause_resume_stop_chain(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "transitions")
        project_id = project["project_id"]
        async with _client(app) as client:
            paused = await client.post(f"/api/v1/projects/{project_id}/pause", json={})
            assert paused.status_code == 200
            assert paused.json()["status"] == "paused"
            resumed = await client.post(f"/api/v1/projects/{project_id}/resume", json={})
            assert resumed.status_code == 200
            assert resumed.json()["status"] == "active"
            stopped = await client.post(f"/api/v1/projects/{project_id}/stop", json={})
            assert stopped.status_code == 200
            assert stopped.json()["status"] == "stopped"


async def test_pause_stale_version_and_illegal_transition(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "transition-errors")
        project_id = project["project_id"]
        async with _client(app) as client:
            stale = await client.post(
                f"/api/v1/projects/{project_id}/pause",
                json={"expected_version": project["version"] + 3},
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["detail"]["current_version"] == project["version"]

            await client.post(f"/api/v1/projects/{project_id}/stop", json={})
            illegal = await client.post(f"/api/v1/projects/{project_id}/stop", json={})
            assert illegal.status_code == 409
            assert illegal.json()["error"]["code"] == "illegal_transition"


async def test_delete_with_rooms_requires_cascade(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "rooms-project")
        await _add_room(project)
        async with _client(app) as client:
            response = await client.delete(
                f"/api/v1/projects/{project['project_id']}",
                params={"expected_version": project["version"]},
            )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "project_has_rooms"
        assert error["detail"]["room_count"] == 1


async def test_delete_cascade_removes_project_and_rooms(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "cascade-project")
        await _add_room(project)
        await _add_room(project)
        async with _client(app) as client:
            response = await client.delete(
                f"/api/v1/projects/{project['project_id']}",
                params={"expected_version": project["version"], "cascade_rooms": "true"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["deleted"] is True
            assert body["storage_purged"] is True
            assert body["rooms_deleted"] == 2
            gone = await client.get(f"/api/v1/projects/{project['project_id']}")
            assert gone.status_code == 404
            listing = await client.get("/api/v1/projects")
        assert listing.json()["count"] == 0


async def test_delete_requires_version_and_matches_revision(tmp_path: Path) -> None:
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "delete-version")
        async with _client(app) as client:
            missing = await client.delete(f"/api/v1/projects/{project['project_id']}")
            stale = await client.delete(
                f"/api/v1/projects/{project['project_id']}",
                params={"expected_version": project["version"] + 2},
            )
        assert missing.status_code == 400
        assert stale.status_code == 409
        assert stale.json()["error"]["detail"]["current_version"] == project["version"]


async def test_write_disabled_returns_503(tmp_path: Path) -> None:
    async with _boot(tmp_path, enabled=False) as (ctx, app, _):
        async with _client(app) as client:
            created = await client.post("/api/v1/projects", json={"name": "blocked"})
        assert created.status_code == 503
        assert created.json()["error"]["code"] == "capability_unavailable"


async def test_write_routes_keep_desired_state_and_events(tmp_path: Path) -> None:
    """REST 写经命令总线：desired-state 投影与 project_* 事件仍触发。"""
    async with _boot(tmp_path) as (ctx, app, _):
        events: list[str] = []
        ctx.on("event/project_created", lambda payload: events.append("created"))
        ctx.on("event/project_updated", lambda payload: events.append("updated"))
        ctx.on("event/project_archived", lambda payload: events.append("archived"))

        async with _client(app) as client:
            created = await client.post("/api/v1/projects", json={"name": "wired"})
            assert created.status_code == 201
            project_id = created.json()["project_id"]
            updated = await client.patch(
                f"/api/v1/projects/{project_id}",
                json={"name": "wired-2", "expected_version": created.json()["version"]},
            )
            assert updated.status_code == 200
            # 归档前：desired-state 派生缓存已含新项目（投影仍走 ProjectUnit）
            desired_store = ctx.get(DESIRED_STATE_STORE.name)
            active_record = await desired_store.load("default")
            assert active_record is not None
            assert project_id in {item.project_id for item in active_record.projects}

            archived = await client.post(
                f"/api/v1/projects/{project_id}/archive",
                json={"expected_version": updated.json()["version"]},
            )
            assert archived.status_code == 200

        assert events == ["created", "updated", "archived"]
        # 归档后活跃列表为空 → 快照为空（归档项目不进入 reconcile 期望状态）
        archived_record = await desired_store.load("default")
        assert archived_record is not None
        assert archived_record.projects == []


async def test_transition_payload_strips_ws_envelope(tmp_path: Path) -> None:
    """WS 路径的 request_id/session_id 不得触发 ProjectTransitionPayload 的 extra 校验。"""
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "envelope")
        result = await bridge_command(
            ctx,
            "project_pause",
            {
                "project_id": project["project_id"],
                "request_id": "req-1",
                "session_id": "sess-1",
            },
        )
        assert result["success"] is True, result
        assert result["data"]["project"]["status"] == "paused"


async def test_delete_guard_reports_room_count_at_command_level(tmp_path: Path) -> None:
    """``project_has_rooms`` 在命令回执里携带 error_detail=房间数（WS 兼容、REST 消费）。"""
    async with _boot(tmp_path) as (ctx, app, _):
        project = await _create_project(ctx, "room-guard")
        await _add_room(project)
        result = await bridge_command(
            ctx,
            "project_delete",
            {"project_id": project["project_id"], "expected_version": project["version"]},
        )
        assert result["success"] is False
        assert result["error"] == "project_has_rooms"
        assert result["error_detail"] == "1"
