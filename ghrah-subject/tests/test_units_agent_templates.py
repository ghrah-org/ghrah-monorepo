# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 模板 REST unit：七条路由、错误码、鉴权与列表过滤。

路由行为用 uvicorn 在**同一事件循环**里起服务（store 的 ``asyncio.Lock``
绑定循环）；鉴权矩阵用 ``TestClient`` + 假 service（403 在触达 service 之前
就返回，避免跨循环使用 aiosqlite 连接）。
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active
from starlette.requests import Request

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.template_store import AgentTemplateRecord, AgentTemplateService, TemplateStore
from ghrah.subject.units.ability_control import (
    _authorized,
    register_ability_control_error_handlers,
)
from ghrah.subject.units.agent_template_control import (
    AgentTemplateControlUnit,
    build_agent_template_control_router,
)
from ghrah.subject.units.websocket_observer_endpoint import (
    WebSocketObserverEndpointUnit,
)

TOKEN = "secret"


def _record(template_id: str = "tpl-1") -> AgentTemplateRecord:
    return AgentTemplateRecord(
        template_id=template_id,
        revision=1,
        format="ghrah.agent-templates.v1",
        metadata={"namespace": "writing", "name": "weekly_report", "title": "周报"},
        created_at="2026-11-17T00:00:00Z",
        updated_at="2026-11-17T00:00:00Z",
    )


class _FakeService:
    """鉴权测试用最小 service：只回答列表与详情。"""

    def __init__(self, record: AgentTemplateRecord | None = None) -> None:
        self.record = record

    async def list(self, **_: Any) -> list[AgentTemplateRecord]:
        return [self.record] if self.record is not None else []

    async def get(self, template_id: str) -> AgentTemplateRecord:
        del template_id
        if self.record is None:  # pragma: no cover - 鉴权测试总会给记录
            raise AssertionError("record missing")
        return self.record


def _app(config: SubjectConfig, service: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(build_agent_template_control_router(config=config, service=service))
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
    return server, task, server.servers[0].sockets[0].getsockname()[1]


async def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    def run() -> tuple[int, dict[str, Any], dict[str, str]]:
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
                return (
                    response.status,
                    json.loads(response.read() or b"{}"),
                    {key.lower(): value for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                json.loads(error.read() or b"{}"),
                {key.lower(): value for key, value in error.headers.items()},
            )

    return await asyncio.to_thread(run)


@pytest.fixture
async def running(tmp_path: Path) -> AsyncIterator[tuple[str, TemplateStore, AgentTemplateService]]:
    store = TemplateStore(tmp_path / "subject.db")
    await store.start()
    service = AgentTemplateService(store)
    config = SubjectConfig(observer_http_api_enabled=True)
    server, task, port = await _serve(_app(config, service))
    try:
        yield f"http://127.0.0.1:{port}", store, service
    finally:
        server.should_exit = True
        await task
        await store.stop()


# ─── 单元装配 ───


async def test_unit_mounts_templates_router_on_observer_app(tmp_path: Path) -> None:
    config = SubjectConfig(
        observer_http_api_enabled=True,
        db_path=str(tmp_path / "subject.db"),
    )
    async with Context() as ctx:
        endpoint = WebSocketObserverEndpointUnit(config)
        fiber = ctx.plugin(mount_unit(endpoint))
        await wait_active(fiber)
        unit = AgentTemplateControlUnit(config)
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)
        paths = {route.path for route in unit.router.routes}
        assert {
            "/api/v1/agent-templates",
            "/api/v1/agent-templates/{template_id}",
            "/api/v1/agent-templates/{template_id}/revisions",
            "/api/v1/agent-templates/{template_id}/archive",
            "/api/v1/agent-templates/{template_id}/references",
        } <= paths
        # store 挂在同一个 db_path 上（独立连接）
        assert unit.store is not None
        assert config.persistence.db_path == str(tmp_path / "subject.db")
        assert any(
            getattr(route, "original_router", None) is unit.router for route in endpoint.app.routes
        )


# ─── 七条路由 ───


async def test_create_detail_list_and_defaults(running: Any) -> None:
    base, _, _ = running
    status, created, headers = await _request(
        f"{base}/api/v1/agent-templates",
        method="POST",
        body={"metadata": {"namespace": "writing", "name": "weekly_report", "title": "周报"}},
    )
    assert status == 201
    template_id = created["template_id"]
    assert headers["location"] == f"/api/v1/agent-templates/{template_id}"
    assert created["format"] == "ghrah.agent-templates.v1"
    assert created["revision"] == 1
    assert created["archived_at"] is None
    assert created["reference_count"] == 0
    # 规范化：省略的组补默认值（max_iterations=-1 / 空成员 / 空路径）
    assert created["context_policy"]["max_iterations"] == -1
    assert created["context_policy"]["communication_timeout"] == 300
    assert created["model"] == {"endpoint_name": None, "model_overrides": None}
    assert created["prompt"] == {"system_prompt": ""}
    assert created["ability_request"]["packages"] == []
    assert created["ability_request"]["surface"] == {
        "mode": "auto",
        "static": [],
        "max_static": None,
    }
    assert created["permission_request"]["require_approval_by_default"] is True
    assert created["path_request"] == []

    status, detail, _ = await _request(f"{base}/api/v1/agent-templates/{template_id}")
    assert status == 200
    assert detail == created

    status, listing, _ = await _request(f"{base}/api/v1/agent-templates")
    assert status == 200
    assert listing["format"] == "ghrah.agent-templates.list.v1"
    assert [item["template_id"] for item in listing["templates"]] == [template_id]
    assert listing["templates"][0]["format"] == "ghrah.agent-templates.summary.v1"
    assert "context_policy" not in listing["templates"][0]

    status, revisions, _ = await _request(f"{base}/api/v1/agent-templates/{template_id}/revisions")
    assert status == 200
    assert revisions["format"] == "ghrah.agent-templates.revisions.v1"
    assert [item["revision"] for item in revisions["revisions"]] == [1]
    assert revisions["revisions"][0]["is_latest"] is True
    assert revisions["revisions"][0]["reference_count"] == 0

    status, references, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}/references"
    )
    assert status == 200
    assert references == {
        "format": "ghrah.agent-templates.references.v1",
        "template_id": template_id,
        "agents": [],
    }


async def test_update_archive_and_cas(running: Any) -> None:
    base, _, _ = running
    _, created, _ = await _request(
        f"{base}/api/v1/agent-templates",
        method="POST",
        body={
            "metadata": {"namespace": "writing", "name": "weekly_report"},
            "prompt": {"system_prompt": "旧提示词"},
            "ability_request": {
                "packages": [{"package": "writing.beta", "origin": "builtin"}],
                "mcp_refs": [],
            },
        },
    )
    template_id = created["template_id"]

    # metadata 按子字段 patch；组整组替换
    status, updated, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}",
        method="PUT",
        body={
            "expected_revision": 1,
            "title": "周报助手",
            "tags": ["weekly", "writing"],
            "prompt": {"system_prompt": "先列事实，再给结论。"},
        },
    )
    assert status == 200
    assert updated["revision"] == 2
    assert updated["metadata"]["title"] == "周报助手"
    assert updated["metadata"]["tags"] == ["weekly", "writing"]
    assert updated["metadata"]["namespace"] == "writing"
    assert updated["prompt"]["system_prompt"] == "先列事实，再给结论。"
    # 未提供的组保持不变（组级替换，不是整体覆盖）
    assert [item["package"] for item in updated["ability_request"]["packages"]] == ["writing.beta"]

    # CAS：过期 revision → 409，detail 带 current_revision
    status, conflict, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}",
        method="PUT",
        body={"expected_revision": 1, "title": "过期"},
    )
    assert status == 409
    assert conflict["error"]["code"] == "template_revision_conflict"
    assert conflict["error"]["detail"]["current_revision"] == 2

    # 归档：只写 archived_at，不改 revision
    status, archived, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}/archive",
        method="POST",
        body={"expected_revision": 2},
    )
    assert status == 200
    assert archived["archived_at"] is not None
    assert archived["revision"] == 2

    # 重复归档幂等
    status, again, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}/archive",
        method="POST",
        body={"expected_revision": 2},
    )
    assert status == 200
    assert again["archived_at"] == archived["archived_at"]

    # 归档后：详情仍可读，默认列表排除
    assert (await _request(f"{base}/api/v1/agent-templates/{template_id}"))[0] == 200
    status, listing, _ = await _request(f"{base}/api/v1/agent-templates")
    assert listing["templates"] == []
    status, listing, _ = await _request(f"{base}/api/v1/agent-templates?include_archived=true")
    assert [item["template_id"] for item in listing["templates"]] == [template_id]


async def test_list_filters(running: Any) -> None:
    base, _, _ = running
    bodies = [
        {"metadata": {"namespace": "writing", "name": "alpha", "tags": ["ops"]}},
        {
            "metadata": {
                "namespace": "writing",
                "name": "beta",
                "tags": ["writing"],
                "deprecated": True,
            }
        },
        {"metadata": {"namespace": "ops", "name": "gamma"}},
    ]
    created = []
    for body in bodies:
        status, view, _ = await _request(f"{base}/api/v1/agent-templates", method="POST", body=body)
        assert status == 201
        created.append(view["template_id"])

    async def names(query: str) -> list[str]:
        status, listing, _ = await _request(f"{base}/api/v1/agent-templates{query}")
        assert status == 200
        return [item["metadata"]["name"] for item in listing["templates"]]

    assert await names("") == ["alpha", "beta", "gamma"]
    assert await names("?namespace=writing") == ["alpha", "beta"]
    assert await names("?namespace=writing&name=alpha") == ["alpha"]
    assert await names("?tag=ops") == ["alpha"]
    assert await names("?deprecated=true") == ["beta"]
    assert await names("?deprecated=false") == ["alpha", "gamma"]


async def test_error_codes(running: Any) -> None:
    base, _, _ = running
    status, view, _ = await _request(
        f"{base}/api/v1/agent-templates",
        method="POST",
        body={"metadata": {"namespace": "writing", "name": "weekly_report"}},
    )
    assert status == 201
    template_id = view["template_id"]

    # 身份冲突
    status, conflict, _ = await _request(
        f"{base}/api/v1/agent-templates",
        method="POST",
        body={"metadata": {"namespace": "writing", "name": "weekly_report"}},
    )
    assert status == 409
    assert conflict["error"]["code"] == "template_identity_exists"

    # 不存在
    for path, method, body in (
        ("/api/v1/agent-templates/missing", "GET", None),
        ("/api/v1/agent-templates/missing/revisions", "GET", None),
        ("/api/v1/agent-templates/missing/references", "GET", None),
        ("/api/v1/agent-templates/missing", "PUT", {"expected_revision": 1, "title": "x"}),
        ("/api/v1/agent-templates/missing/archive", "POST", {"expected_revision": 1}),
    ):
        status, error, _ = await _request(f"{base}{path}", method=method, body=body)
        assert status == 404
        assert error["error"]["code"] == "template_not_found"

    # 请求体形状
    for body in (
        {"metadata": {"namespace": "Bad", "name": "weekly_report"}},
        {"metadata": {"namespace": "writing", "name": "weekly-report"}},
        {"metadata": {"namespace": "writing"}},
        {"metadata": {"namespace": "writing", "name": "weekly_report"}, "unknown": True},
        {
            "metadata": {"namespace": "writing", "name": "weekly_report"},
            "ability_request": {
                "packages": [
                    {
                        "package": "writing.beta",
                        "origin": "builtin",
                        "whole_container": True,
                        "operations": [{"key": "draft"}],
                    }
                ]
            },
        },
        {
            "metadata": {"namespace": "writing", "name": "weekly_report"},
            "path_request": [
                {
                    "scope": "package",
                    "workspace_role": "default",
                    "subpath": ".",
                    "access": "read",
                    "deny": False,
                }
            ],
        },
    ):
        status, error, _ = await _request(
            f"{base}/api/v1/agent-templates", method="POST", body=body
        )
        assert status == 400, body
        assert error["error"]["code"] == "invalid_request"
        assert error["error"]["detail"]["errors"]

    # PUT 至少一个可写字段
    status, error, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}",
        method="PUT",
        body={"expected_revision": 1},
    )
    assert status == 400
    assert error["error"]["code"] == "invalid_request"

    # PUT 不允许改身份
    status, error, _ = await _request(
        f"{base}/api/v1/agent-templates/{template_id}",
        method="PUT",
        body={
            "expected_revision": 1,
            "metadata": {"namespace": "writing", "name": "other"},
        },
    )
    assert status == 400
    assert error["error"]["code"] == "invalid_request"

    # 非对象请求体
    def post_raw() -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            f"{base}/api/v1/agent-templates",
            method="POST",
            data=b"[]",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    status, error = await asyncio.to_thread(post_raw)
    assert status == 400
    assert error["error"]["code"] == "invalid_request"


async def test_disabled_switch_returns_capability_unavailable(tmp_path: Path) -> None:
    store = TemplateStore(tmp_path / "subject.db")
    await store.start()
    app = _app(SubjectConfig(observer_http_api_enabled=False), AgentTemplateService(store))
    server, task, port = await _serve(app)
    try:
        status, error, _ = await _request(f"http://127.0.0.1:{port}/api/v1/agent-templates")
        assert status == 503
        assert error["error"]["code"] == "capability_unavailable"
    finally:
        server.should_exit = True
        await task
        await store.stop()


# ─── 鉴权矩阵 ───


def test_authorization_matrix() -> None:
    config = SubjectConfig(observer_http_api_token=TOKEN, observer_http_api_enabled=True)
    assert _authorized(_request_with_host("127.0.0.1"), config) is True
    assert _authorized(_request_with_host("testclient"), config) is True
    assert _authorized(_request_with_host("10.0.0.5"), config) is False
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": "Bearer wrong"}), config)
        is False
    )
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": f"Bearer {TOKEN}"}), config)
        is True
    )
    no_token = SubjectConfig(observer_http_api_token="")
    assert (
        _authorized(_request_with_host("10.0.0.5", {"Authorization": "Bearer x"}), no_token)
        is False
    )


def test_rest_auth_for_non_loopback_clients() -> None:
    service = _FakeService(_record())
    config = SubjectConfig(observer_http_api_token=TOKEN, observer_http_api_enabled=True)
    app = _app(config, service)

    # 回环（TestClient 默认 host=testclient）免 token
    loopback = TestClient(app)
    assert loopback.get("/api/v1/agent-templates").status_code == 200

    remote = TestClient(app, client=("10.9.8.7", 4321))
    denied = remote.get("/api/v1/agent-templates")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "access_denied"
    assert (
        remote.get("/api/v1/agent-templates", headers={"Authorization": "Bearer wrong"}).status_code
        == 403
    )
    allowed = remote.get("/api/v1/agent-templates", headers={"Authorization": f"Bearer {TOKEN}"})
    assert allowed.status_code == 200
    assert allowed.json()["templates"][0]["template_id"] == "tpl-1"
    detail = remote.get(
        "/api/v1/agent-templates/tpl-1", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert detail.status_code == 200
    assert detail.json()["metadata"]["name"] == "weekly_report"
