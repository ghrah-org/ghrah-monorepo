"""Subject 显式连接到真实 Core 资源入口，保持旧命令客户端独立。"""

import asyncio
import json
from pathlib import Path

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig

from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig


class EchoLLM:
    model = "subject-resource-client"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="subject resource reply")])


async def test_public_client_resource_chain_with_owned_lifecycle(tmp_path):
    runtime = CoreRuntime(
        RuntimeConfig(
            scope_ref="scope", database_path=str(tmp_path / "core.db"), enabled_agent_api=True
        ),
        llm_factory=lambda _: EchoLLM(),
    )
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="scope"), runtime
    )
    try:
        await connection.start()
        client = connection.client
        assert (await client.status()).ready
        fixture = (
            Path(__file__).resolve().parents[2]
            / "ghrah-protocol/contracts/runtime/fixtures/agent.create.json"
        )
        created = await client.create_agent(
            json.loads(fixture.read_text()), idempotency_key="subject-create"
        )
        assert created.status == 201, created.body
        agent, etag = created.body["agent_id"], created.headers["ETag"]
        assert (await client.get_agent(agent)).body["state"] == "stopped"
        assert len((await client.list_agent_resources()).body["items"]) == 1
        assert (await client.set_agent_state(agent, "running", etag=etag)).body[
            "recovered"
        ] == "initialized"
        accepted = await client.submit_message(agent, "work", idempotency_key="subject-message")
        assert accepted.status == 202
        delivery = await client.get_delivery(accepted.body["delivery_id"], wait=5)
        assert delivery.body["state"] == "completed"
        timeline = await client.get_timeline(agent, limit=1)
        assert timeline.status == 200 and len(timeline.body["items"]) == 1
        assert "messages_delta" not in timeline.body["items"][0]
        node = await client.get_node(delivery.body["last_node_id"])
        assert node.status == 200
        replies = [
            block["text"]
            for message in node.body["payload"]["messages_delta"]
            if message["role"] == "ai"
            for block in message["content_blocks"]
            if block["type"] == "text"
        ]
        assert "subject resource reply" in replies
        assert "delivery_context" not in json.dumps(node.body)
        assert (
            await client.get_node(node.body["node_id"], etag=node.headers["ETag"])
        ).status == 304
        assert (await client.get_node(node.body["node_id"], schema_version=2)).status == 406
        assert (await client.set_agent_state(agent, "stopped", etag=etag)).body["agent"][
            "state"
        ] == "stopped"
    finally:
        await connection.stop()
    assert (await client.status()).phase == "stopped"
    assert (await client.get_agent(agent)).status == 503


async def test_connection_passes_explicit_resource_switch_to_owned_core(tmp_path):
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref="scope",
            database_path=str(tmp_path / "core.db"),
            enabled_agent_api=True,
            config_quota=123456,
        )
    )
    try:
        await connection.start()
        response = await connection.client.get("/v1/meta")
        assert {"name": "runtime.agents.conversation", "version": 1} in response.body[
            "capabilities"
        ]
        assert connection._runtime.config.config_quota == 123456
    finally:
        await connection.stop()


async def test_stop_cancels_waiting_delivery_and_returns_database_owner(tmp_path):
    entered = asyncio.Event()

    class WaitingLLM(EchoLLM):
        async def generate(self, messages, tools=None):
            entered.set()
            await asyncio.Event().wait()

    config = RuntimeConfig(
        scope_ref="scope",
        database_path=str(tmp_path / "core.db"),
        enabled_agent_api=True,
        stop_timeout=0.05,
    )
    runtime = CoreRuntime(config, llm_factory=lambda _: WaitingLLM())
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="scope"), runtime
    )
    await connection.start()
    client = connection.client
    fixture = (
        Path(__file__).resolve().parents[2]
        / "ghrah-protocol/contracts/runtime/fixtures/agent.create.json"
    )
    created = await client.create_agent(json.loads(fixture.read_text()), idempotency_key="create")
    await client.set_agent_state(created.body["agent_id"], "running", etag=created.headers["ETag"])
    accepted = await client.submit_message(
        created.body["agent_id"], "work", idempotency_key="message"
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    waiting = asyncio.create_task(client.get_delivery(accepted.body["delivery_id"], wait=30))
    await asyncio.sleep(0)
    await asyncio.wait_for(connection.stop(), timeout=5)
    assert (await waiting).status == 503
    replacement = CoreRuntime(config, llm_factory=lambda _: EchoLLM())
    await replacement.init()
    try:
        delivery = await replacement.dispatch(
            "GET", accepted.headers["Location"], principal=client._principal
        )
        assert delivery.body["state"] == "interrupted"
        assert await replacement._unit.supervisor.list_agents() == []
    finally:
        await replacement.stop()
