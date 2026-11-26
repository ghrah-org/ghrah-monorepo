# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 管理面 REST：DELETE 的失败映射与删除回执契约。

观察端按 ``error.code`` 分支（``agent_busy`` / ``not_found`` / ``conflict``），
这里锁住 ``project_remove_agent`` 的稳定码到 HTTP 的映射。命令层行为（忙碌
守卫、Room 清理）由 ``test_project_remove_agent_offline.py`` 覆盖。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI

from ghrah.subject.project.models import AgentRecord, ProjectRecord, make_project_record
from ghrah.subject.units.ability_control import register_ability_control_error_handlers
from ghrah.subject.units.agent_control import build_agent_control_router

PROJECT_ID = "project-1"
AGENT_ID = "a" * 32


class _ProjectStore:
    """最小 ProjectStore：只实现路由用到的 ``get``。"""

    def __init__(self, record: ProjectRecord) -> None:
        self.record = record

    async def get(self, project_id: str) -> ProjectRecord | None:
        return self.record if project_id == self.record.project_id else None


class _Projects:
    """最小 ProjectManager：只实现 DELETE 路由用到的 ``handle_command``。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((command, payload))
        return self.result


def _record() -> ProjectRecord:
    agent = AgentRecord(
        name="requirement",
        agent_id=AGENT_ID,
        desired_state="running",
        runtime_status="running",
    )
    return make_project_record(
        name="demo",
        project_root_locator="file:///tmp/demo",
    ).model_copy(update={"project_id": PROJECT_ID, "agents": [agent]})


def _app(store: _ProjectStore, projects: _Projects) -> FastAPI:
    """装配只含 agent_control 路由的最小应用；回环请求免 token。"""
    config = type(
        "_Config",
        (),
        {
            "observer_http_api_enabled": True,
            "observer_http_api_token": "",
            "ability_source": "builtin",
        },
    )()
    app = FastAPI()
    app.include_router(
        build_agent_control_router(
            config=config,  # type: ignore[arg-type]
            project_store=store,
            projects=projects,
            core_ports=None,
        )
    )
    register_ability_control_error_handlers(app)
    return app


async def _delete(app: FastAPI, *, expected_revision: int = 1) -> tuple[int, dict[str, Any]]:
    """不经网络，直接把一个 DELETE 请求喂给 ASGI 应用。"""
    path = f"/api/v1/projects/{PROJECT_ID}/agents/{AGENT_ID}"
    query = urlencode({"expected_revision": expected_revision})
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "DELETE",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    return status, json.loads(body or b"{}")


async def test_delete_agent_maps_busy_command_error_to_409() -> None:
    """命令层回 ``agent_busy`` → HTTP 409，且 ``error.code`` 原样透出。"""
    record = _record()
    store = _ProjectStore(record)
    projects = _Projects(
        {
            "success": False,
            "data": None,
            "error": "agent_busy",
            "error_detail": "agent requirement is processing messages",
        }
    )

    status, payload = await _delete(_app(store, projects))

    assert status == 409, payload
    assert payload["error"]["code"] == "agent_busy"
    assert "processing messages" in payload["error"]["message"]
    assert projects.commands == [
        (
            "project_remove_agent",
            {
                "project_id": PROJECT_ID,
                "agent_id": AGENT_ID,
                "agent_name": "requirement",
                "expected_version": 1,
            },
        )
    ]


async def test_delete_agent_returns_project_version_on_success() -> None:
    """删除成功回执带项目版本，供页面下一次条件写使用。"""
    record = _record()
    store = _ProjectStore(record)
    projects = _Projects(
        {
            "success": True,
            "data": {
                "project": record.to_wire(),
                "agent_name": "requirement",
                "agent_id": AGENT_ID,
            },
            "error": None,
        }
    )

    status, payload = await _delete(_app(store, projects), expected_revision=7)

    assert status == 200, payload
    assert payload["format"] == "ghrah.agent-control.deleted.v1"
    assert payload["project_id"] == PROJECT_ID
    assert payload["agent_id"] == AGENT_ID
    assert payload["project_version"] == record.version
    assert projects.commands[0][1]["expected_version"] == 7


@pytest.mark.parametrize("missing", ["project", "agent"])
async def test_delete_agent_maps_missing_target_to_404(missing: str) -> None:
    """项目或 Agent 不存在时回 404，前端据此提示「已被删除」。"""
    record = _record()
    if missing == "project":
        store = _ProjectStore(record.model_copy(update={"project_id": "other"}))
    else:
        store = _ProjectStore(record.model_copy(update={"agents": []}))

    status, payload = await _delete(_app(store, _Projects({"success": True, "data": {}})))

    assert status == 404, payload
    assert payload["error"]["code"] == "not_found"
