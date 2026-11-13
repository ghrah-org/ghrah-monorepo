"""Runtime HTTP 宿主的最小透传语义回归。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.protocol.runtime import RuntimeDispatchResponse, RuntimeSystemStatus

from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig
from ghrah.subject.runtime_http import create_runtime_app


@dataclass
class FakeRuntimeClient:
    """记录宿主实际传给 Runtime 的请求。"""

    calls: list[dict[str, Any]] = field(default_factory=list)
    ready: bool = True

    async def status(self) -> RuntimeSystemStatus:
        return RuntimeSystemStatus(phase="ready" if self.ready else "booting", ready=self.ready)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> RuntimeDispatchResponse:
        self.calls.append({"method": method, "path": path, "headers": headers, "body": body})
        if method == "POST":
            return RuntimeDispatchResponse(
                201,
                {"ETag": '"rev-1"', "Location": "/v1/agents/agt_test"},
                {"agent_id": "agt_test"},
            )
        if method == "DELETE":
            return RuntimeDispatchResponse(204, {}, None)
        return RuntimeDispatchResponse(200, {"ETag": '"node-1"'}, {"value": path})


@dataclass
class FakeConnection:
    client: FakeRuntimeClient

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class EchoModel:
    model = "runtime-http-contract"

    def configure_tools(self, tools: Any) -> None:
        return None

    def apply_model_overrides(self, overrides: Any) -> None:
        return None

    async def generate(self, messages: Any, tools: Any = None) -> LLMResponse:
        return LLMResponse(content_blocks=[TextBlock(text="runtime http contract reply")])


def client_for(runtime_client: FakeRuntimeClient, *, token: str = "") -> TestClient:
    connection = FakeConnection(runtime_client)
    app = create_runtime_app(
        connection, allowed_origins=("http://localhost:5174",), api_token=token
    )
    return TestClient(app)


def test_runtime_http_requires_bearer_token() -> None:
    runtime_client = FakeRuntimeClient()
    client = client_for(runtime_client, token="secret-token")

    response = client.get("/v1/agents")

    assert response.status_code == 401
    assert response.json()["code"] == "ACCESS_DENIED"
    assert runtime_client.calls == []


def test_runtime_http_rejects_invalid_json_and_non_object_json() -> None:
    runtime_client = FakeRuntimeClient()
    client = client_for(runtime_client)

    invalid = client.post(
        "/v1/agents",
        content=b"{",
        headers={"Content-Type": "application/json", "Idempotency-Key": "create"},
    )
    array = client.post("/v1/agents", json=["invalid"])

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INVALID_ARGUMENT"
    assert array.status_code == 422
    assert array.json()["code"] == "INVALID_ARGUMENT"
    assert runtime_client.calls == []


def test_runtime_http_forwards_contract_headers_query_and_201_response() -> None:
    runtime_client = FakeRuntimeClient()
    client = client_for(runtime_client)

    response = client.post(
        "/v1/agents?trace=true",
        json={"external_ref": "worker"},
        headers={"Idempotency-Key": "create"},
    )

    assert response.status_code == 201
    assert response.headers["etag"] == '"rev-1"'
    assert response.headers["location"] == "/v1/agents/agt_test"
    assert runtime_client.calls == [
        {
            "method": "POST",
            "path": "/v1/agents?trace=true",
            "headers": {"idempotency-key": "create"},
            "body": {"external_ref": "worker"},
        }
    ]


def test_runtime_http_forwards_conditional_header_and_preserves_204() -> None:
    runtime_client = FakeRuntimeClient()
    client = client_for(runtime_client)

    deleted = client.delete("/v1/agents/agt_test", headers={"If-Match": '"rev-1"'})
    read = client.get(
        "/v1/nodes/node_test?schema_version=1",
        headers={"If-None-Match": '"node-0"'},
    )

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert runtime_client.calls[0]["headers"] == {"if-match": '"rev-1"'}
    assert read.status_code == 200
    assert read.headers["etag"] == '"node-1"'
    assert runtime_client.calls[1]["headers"] == {"if-none-match": '"node-0"'}


def test_runtime_http_runs_real_core_contract_chain_and_preserves_restart_semantics(tmp_path):
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "ghrah-protocol/contracts/runtime/fixtures/agent.create.json"
    )
    fixture = json.loads(fixture_path.read_text())
    fixture["external_ref"] = "runtime-http-e2e/agent"
    config = RuntimeConfig(
        scope_ref="runtime-http-e2e",
        database_path=str(tmp_path / "core.db"),
        enabled_agent_api=True,
    )
    runtime = CoreRuntime(config, llm_factory=lambda _: EchoModel())
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="runtime-http-e2e"), runtime
    )
    app = create_runtime_app(connection, allowed_origins=("http://localhost:5174",))

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

        created = client.post(
            "/v1/agents", json=fixture, headers={"Idempotency-Key": "http-contract-create"}
        )
        assert created.status_code == 201, created.text
        agent_id = created.json()["agent_id"]
        etag = created.headers["etag"]
        assert (
            client.post(
                "/v1/agents", json=fixture, headers={"Idempotency-Key": "http-contract-create"}
            ).headers["idempotency-replayed"]
            == "true"
        )

        stale = client.put(
            f"/v1/agents/{agent_id}/state",
            json={"state": "running"},
            headers={"If-Match": '"rev-0"'},
        )
        assert stale.status_code == 412

        started = client.put(
            f"/v1/agents/{agent_id}/state",
            json={"state": "running"},
            headers={"If-Match": etag},
        )
        assert started.status_code == 200, started.text

        abilities = client.get(f"/v1/agents/{agent_id}/abilities")
        assert abilities.status_code == 200, abilities.text
        ability_etag = abilities.headers["etag"]
        assert (
            client.delete(
                f"/v1/agents/{agent_id}/abilities/grants/none",
                headers={"If-Match": '"ability-0"'},
            ).status_code
            == 412
        )
        tool = abilities.json()["bound_tools"][0]
        unbound = client.delete(
            f"/v1/agents/{agent_id}/abilities/tools/{tool['name']}",
            headers={"If-Match": ability_etag},
        )
        assert unbound.status_code == 200, unbound.text
        assert all(item["name"] != tool["name"] for item in unbound.json()["bound_tools"])
        rebound = client.put(
            f"/v1/agents/{agent_id}/abilities/tools/{tool['name']}",
            json={
                "alias": tool["alias"],
                "member": tool["member"],
                "tool_name": tool["name"],
            },
            headers={"If-Match": unbound.headers["etag"]},
        )
        assert rebound.status_code == 200, rebound.text
        assert any(item["name"] == tool["name"] for item in rebound.json()["bound_tools"])

        accepted = client.post(
            f"/v1/agents/{agent_id}/messages",
            json={"kind": "user", "text": "work", "metadata": {}},
            headers={"Idempotency-Key": "http-contract-message"},
        )
        assert accepted.status_code == 202, accepted.text
        delivery_path = accepted.headers["location"]
        delivered = client.get(delivery_path + "?wait=5")
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["state"] == "completed"

        timeline = client.get(f"/v1/agents/{agent_id}/timeline?limit=100")
        assert timeline.status_code == 200, timeline.text
        node_id = next(
            item["node_id"]
            for item in timeline.json()["items"]
            if item["summary"]["text"] == "runtime http contract reply"
        )
        node = client.get(f"/v1/nodes/{node_id}?schema_version=1")
        assert node.status_code == 200, node.text
        assert "runtime http contract reply" in node.text
        assert (
            client.get(
                f"/v1/nodes/{node_id}?schema_version=1",
                headers={"If-None-Match": node.headers["etag"]},
            ).status_code
            == 304
        )

        patched_fixture = json.loads(json.dumps(fixture))
        patched_fixture["config"]["prompt"]["system_prompt"] = "runtime http patched"
        assert (
            client.patch(
                f"/v1/agents/{agent_id}",
                json={"config": patched_fixture["config"]},
                headers={"If-Match": etag},
            ).status_code
            == 409
        )
        stopped = client.put(
            f"/v1/agents/{agent_id}/state",
            json={"state": "stopped"},
            headers={"If-Match": etag},
        )
        assert stopped.status_code == 200, stopped.text
        patched = client.patch(
            f"/v1/agents/{agent_id}",
            json={"config": patched_fixture["config"]},
            headers={"If-Match": etag},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["config_revision"] == 2

    replacement = CoreRuntime(config, llm_factory=lambda _: EchoModel())
    replacement_connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="runtime-http-e2e"), replacement
    )
    replacement_app = create_runtime_app(
        replacement_connection, allowed_origins=("http://localhost:5174",)
    )
    with TestClient(replacement_app) as client:
        restarted = client.get(f"/v1/agents/{agent_id}")
        assert restarted.status_code == 200, restarted.text
        assert restarted.json()["state"] == "stopped"
        assert restarted.json()["incarnation_id"] is None
        assert restarted.json()["config_revision"] == 2
        assert len(client.get(f"/v1/agents/{agent_id}/timeline?limit=100").json()["items"]) > 0
        deleted = client.delete(
            f"/v1/agents/{agent_id}", headers={"If-Match": restarted.headers["etag"]}
        )
        assert deleted.status_code == 204
        assert client.get(f"/v1/agents/{agent_id}").status_code == 404
