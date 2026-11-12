"""新增就绪入口与原业务启动路径的隔离验收。"""

import asyncio
from dataclasses import replace

import pytest
from ability_testutil import put_agent_template
from ghrah.ability.access import GrantPolicy
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.core.unit import CoreUnitConfig
from ghrah.protocol.types import SendMessagePayload, SpawnAgentPayload
from ouroboros import Context

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry, default_core_unit_factory
from ghrah.subject.manifest_store.store import ManifestStore
from ghrah.subject.runtime_connection import RuntimeConnection, RuntimeConnectionConfig


async def test_public_client_rejects_booting_and_stopped_business_requests(tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()

    class SlowRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            entered.set()
            await finish.wait()

    runtime = SlowRuntime(
        RuntimeConfig(
            enabled_commands=("list_agents",),
            scope_ref="new",
            database_path=str(tmp_path / "new.db"),
        )
    )
    connection = RuntimeConnection(RuntimeConnectionConfig(enabled=True, scope_ref="new"), runtime)
    starting = asyncio.create_task(connection.start())
    await entered.wait()
    client = connection.client
    try:
        assert not starting.done()
        assert (await client.status()).phase == "booting"
        rejected = await asyncio.wait_for(
            client.request(
                "POST",
                "/v1/commands",
                body={"command": "list_agents", "payload": {"project_id": "new"}},
            ),
            timeout=0.5,
        )
        assert rejected.status == 503
        finish.set()
        await starting
        assert (await client.status()).ready
        assert await client.list_agents() == []
        created = await client.request(
            "POST",
            "/v1/commands",
            body={"command": "list_agents", "payload": {"project_id": "new"}},
        )
        assert created.status == 200 and created.body["success"]
    finally:
        finish.set()
        await starting
        await connection.stop()
    assert (await client.status()).phase == "stopped"
    assert (await client.request("POST", "/v1/commands")).status == 503


async def test_subject_timeout_releases_owner_and_keeps_failure_diagnosis(tmp_path):
    class BlockedRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            await asyncio.Event().wait()

    config = RuntimeConfig(
        scope_ref="new",
        enabled_commands=("list_agents",),
        database_path=str(tmp_path / "new.db"),
        init_timeout=0.2,
    )
    runtime = BlockedRuntime(config)
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref="new",
            init_timeout=0.2,
            ready_timeout=10,  # 包含失败清理时间，不以旧基线的 1 秒覆盖 Core 诊断。
        ),
        runtime,
    )
    with pytest.raises(RuntimeError, match="failed"):
        await connection.start()
    assert (await connection.client.status()).phase == "failed"
    assert (await connection.client.status()).error is not None
    assert (await connection.client.request("POST", "/v1/commands")).status == 503
    replacement = CoreRuntime(replace(config, init_timeout=5))
    await replacement.init()
    await replacement.stop()


@pytest.mark.parametrize("stop_new_first", [True, False])
async def test_new_connection_and_legacy_business_have_independent_lifecycles(
    tmp_path, stop_new_first
):
    class EchoLLM:
        model = "subject-runtime-acceptance"

        def configure_tools(self, tools):
            pass

        def apply_model_overrides(self, overrides):
            pass

        async def generate(self, messages, tools=None):
            return LLMResponse(content_blocks=[TextBlock(text="legacy reply")])

    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        ability_grant_policy=GrantPolicy(mode="merge"),
    )
    templates = ManifestStore(tmp_path / "templates")
    template_ref = put_agent_template(templates)
    registry = CoreClusterRegistry(
        unit_factory=default_core_unit_factory(
            config, manifest_store=templates, llm_factory=lambda _: EchoLLM()
        )
    )
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref="new",
            enabled_commands=("list_agents",),
            database_path=str(tmp_path / "new.db"),
        )
    )
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            await connection.start()
            handle = await registry.ensure_cluster(
                "legacy-cluster", project_id="legacy", project_root_locator=str(tmp_path / "old")
            )
            spawned = await handle.spawn_agent(
                SpawnAgentPayload(
                    project_id="legacy",
                    config={"name": "worker"},
                    manifest_ref=template_ref,
                )
            )
            assert spawned["success"], spawned
            agent_id = spawned["data"]["agent_id"]
            message = SendMessagePayload(
                project_id="legacy", agent_id=agent_id, target="worker", content="work"
            )
            assert (await handle.send_message(message))["success"]
            # 旧业务运行不会被新端口伪装成新 scope 的事实。
            assert await connection.client.list_agents() == []
            if stop_new_first:
                await connection.stop()
                response = await handle.send_message(message)
                assert response["success"], response
                assert (await handle.terminate_agent(agent_id, "worker"))["success"]
            else:
                assert (await handle.terminate_agent(agent_id, "worker"))["success"]
                await registry.stop()
                assert (await connection.client.status()).ready
                response = await connection.client.request(
                    "POST",
                    "/v1/commands",
                    body={"command": "list_agents", "payload": {"project_id": "new"}},
                )
                assert response.status == 200 and response.body["success"]
        finally:
            await connection.stop()
            await registry.stop()


async def test_owned_client_waits_ready_then_calls_existing_business(tmp_path):

    class LLM:
        model = "owned-business-test"

        def configure_tools(self, tools):
            pass

        def apply_model_overrides(self, overrides):
            pass

        async def generate(self, messages, tools=None):
            return LLMResponse(content_blocks=[TextBlock(text="owned reply")])

    templates = ManifestStore(tmp_path / "templates")
    template_ref = put_agent_template(templates)
    runtime = CoreRuntime(
        RuntimeConfig(
            scope_ref="scope",
            database_path=str(tmp_path / "core.db"),
            ability_source="builtin",
            enabled_commands=("spawn_agent", "list_agents", "send_message", "terminate_agent"),
        ),
        llm_factory=lambda _: LLM(),
        unit_config=CoreUnitConfig(manifest_store=templates),
    )
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="scope"), runtime
    )
    await connection.start()
    try:
        assert (await connection.client.status()).ready
        assert (
            await connection.client.spawn_agent(
                SpawnAgentPayload(
                    project_id="scope",
                    config={"name": "worker", "agent_id": "11111111111111111111111111111111"},
                    manifest_ref=template_ref,
                )
            )
        )["success"]
        assert (
            await connection.client.send_message(
                SendMessagePayload(
                    project_id="scope",
                    agent_id="11111111111111111111111111111111",
                    target="worker",
                    content="work",
                )
            )
        )["data"]["content"] == "owned reply"
        assert (
            await connection.client.terminate_agent("11111111111111111111111111111111", "worker")
        )["success"]
        assert await connection.client.list_agents() == []
    finally:
        await connection.stop()
    assert runtime.status().phase == "stopped"
