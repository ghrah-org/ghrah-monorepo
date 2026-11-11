# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 资源 REST：创建、修改、无模板启动拒绝与删除。"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from ghrah.ability.access import AccessError
from test_project_manager import _default_locator, _make_manager, _workspace_input

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.store import ProjectStore
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.agent_control import build_agent_control_router


class _AbilityStore:
    async def agent_revision(self, actor: Any, agent: str) -> int:
        del actor, agent
        raise AccessError("not_found")


class _Handle:
    def __init__(self) -> None:
        self.store = _AbilityStore()
        self.operator = object()


class _OfflineHandle:
    """未连接的集群句柄：运行态按离线处理，删除时不尝试终止。"""

    def __init__(self) -> None:
        self.project_id = ""

    @property
    def is_connected(self) -> bool:
        return False


class _OfflineCluster:
    def __init__(self) -> None:
        self._handle = _OfflineHandle()

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> _OfflineHandle:
        del cluster_id, project_root_locator
        self._handle.project_id = project_id
        return self._handle

    def get_handle(self, cluster_id: str) -> _OfflineHandle:
        del cluster_id
        return self._handle

    def has_cluster(self, cluster_id: str) -> bool:
        del cluster_id
        return False

    async def shutdown_cluster(self, cluster_id: str) -> None:
        del cluster_id

    async def stop(self) -> None:
        return None


class _Access:
    async def acquire(self, project_id: str, project_root_locator: str) -> _Handle:
        del project_id, project_root_locator
        return _Handle()

    async def agent_tool_snapshot(self, project_id: str, agent_id: str) -> None:
        del project_id, agent_id
        return None


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
) -> tuple[int, dict[str, Any], str]:
    def run() -> tuple[int, dict[str, Any], str]:
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                location = response.headers.get("Location", "")
                return response.status, json.loads(response.read() or b"{}"), location
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}"), ""

    return await asyncio.to_thread(run)


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    opened = ProjectStore(tmp_path / "projects.db")
    await opened.start()
    try:
        yield opened
    finally:
        await opened.stop()


async def test_agent_resource_create_patch_start_and_delete(
    store: ProjectStore, tmp_path: Path
) -> None:
    manager = _make_manager(
        store,
        default_locator=_default_locator(tmp_path),
        cluster_transport=_OfflineCluster(),  # type: ignore[arg-type]
    )
    created = await manager.handle_command(
        "project_create",
        {
            "name": "P1",
            "writable_workspaces": _workspace_input(tmp_path),
        },
    )
    assert created["success"], created.get("error")
    project = created["data"]["project"]
    project_id = project["project_id"]
    cluster_id = project["cluster_ids"][0]
    config = SubjectConfig(ability_source="builtin", observer_http_api_enabled=True)
    app = FastAPI()
    app.include_router(
        build_agent_control_router(
            config=config,
            project_store=store,
            projects=manager,
            access=_Access(),
        )
    )
    register_ability_control_error_handlers(app)
    server, task, port = await _serve(app)
    base = f"http://127.0.0.1:{port}/api/v1/projects/{project_id}/agents"
    try:
        status, view, location = await _request(
            base,
            method="POST",
            body={
                "expected_revision": project["version"],
                "name": "reviewer",
                "cluster_id": cluster_id,
                "system_prompt": "hello",
                "desired_state": "stopped",
            },
        )
        assert status == 201, view
        assert location.endswith(f"/agents/{view['specification']['agent_id']}")
        assert view["specification"]["system_prompt"] == "hello"
        assert view["specification"]["manifest_ref"] == ""
        assert view["ability"]["state"] == "unassembled"
        agent_id = view["specification"]["agent_id"]
        version = view["specification"]["project_version"]

        listed, page, _ = await _request(base)
        assert listed == 200
        assert "system_prompt" not in page["agents"][0]["specification"]
        assert page["agents"][0]["specification"]["prompt"]["size_bytes"] == len(b"hello")

        patched, saved, _ = await _request(
            f"{base}/{agent_id}",
            method="PATCH",
            body={"expected_revision": version, "system_prompt": "next"},
        )
        assert patched == 200, saved
        assert saved["effective"] == "next_start"
        assert saved["specification"]["system_prompt"] == "next"
        assert saved["spec_overrides_template"] is False

        stale, conflict, _ = await _request(
            f"{base}/{agent_id}",
            method="PATCH",
            body={"expected_revision": version, "system_prompt": "stale"},
        )
        assert stale == 409, conflict
        assert conflict["error"]["code"] == "conflict"
        assert conflict["error"]["detail"]["view"]["specification"]["system_prompt"] == "next"

        running, rejected, _ = await _request(
            f"{base}/{agent_id}/desired-state",
            method="PUT",
            body={
                "expected_revision": saved["specification"]["project_version"],
                "state": "running",
            },
        )
        assert running == 409, rejected
        assert rejected["error"]["code"] == "agent_manifest_required"
        assert rejected["error"]["detail"]["kept"] is True

        removed, gone, _ = await _request(
            f"{base}/{agent_id}?expected_revision={saved['specification']['project_version']}",
            method="DELETE",
        )
        assert removed == 200, gone
        assert gone["format"] == "ghrah.agent-control.deleted.v1"
        empty, page, _ = await _request(base)
        assert empty == 200
        assert page["agents"] == []

        missing, rejected, _ = await _request(
            base,
            method="POST",
            body={
                "expected_revision": gone["project_version"],
                "name": "runner",
                "cluster_id": cluster_id,
                "desired_state": "running",
            },
        )
        assert missing == 409, rejected
        assert rejected["error"]["code"] == "agent_manifest_required"
        assert rejected["error"]["detail"]["kept"] is False
        empty_again, page, _ = await _request(base)
        assert empty_again == 200
        assert page["agents"] == []
    finally:
        server.should_exit = True
        await task
