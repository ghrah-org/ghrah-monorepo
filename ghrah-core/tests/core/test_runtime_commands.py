"""生命周期入口薄适配既有业务命令，不建立第二份 Agent 事实。"""

import asyncio

import aiosqlite
import pytest
from ghrah.protocol.runtime import RuntimePrincipal

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig

COMMANDS = ("spawn_agent", "list_agents", "send_message", "terminate_agent")
PRINCIPAL = RuntimePrincipal(scope_ref="scope")
SPAWN = {
    "project_id": "scope",
    "cluster_id": "scope",
    "config": {"name": "worker", "agent_id": "11111111111111111111111111111111"},
    "manifest_ref": "tests.agent",
}
MESSAGE = {
    "project_id": "scope",
    "agent_id": "11111111111111111111111111111111",
    "target": "worker",
    "content": "work",
}


class EchoLLM:
    model = "runtime-command-test"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="business reply")])


def make_runtime(tmp_path, *, unit_config=None, **kwargs):
    return CoreRuntime(
        RuntimeConfig(
            scope_ref="scope",
            database_path=str(tmp_path / "core.db"),
            ability_source="builtin",
            enabled_commands=COMMANDS,
            **kwargs,
        ),
        llm_factory=lambda _: EchoLLM(),
        unit_config=unit_config,
    )


async def command(runtime, name, payload):
    return await runtime.dispatch(
        "POST",
        "/v1/commands",
        principal=PRINCIPAL,
        body={"command": name, "payload": payload},
    )


async def test_default_init_has_no_resource_management_or_execution(tmp_path):
    runtime = CoreRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    await runtime.init()
    try:
        assert await runtime._unit.supervisor.list_agents() == []
        meta = await runtime.dispatch("GET", "/v1/meta", principal=PRINCIPAL)
        assert meta.body["capabilities"] == [{"name": "runtime.lifecycle", "version": 1}]
        assert (await command(runtime, "list_agents", {"project_id": "scope"})).status == 404
        async with aiosqlite.connect(runtime.config.database_path) as db:
            rows = await (
                await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'runtime_%'"
                )
            ).fetchall()
        assert rows == []
    finally:
        await runtime.stop()


async def test_commands_reuse_existing_execution_and_restore_only_when_explicit(
    tmp_path, runtime_unit_config
):
    runtime = make_runtime(tmp_path, unit_config=runtime_unit_config)
    assert (await command(runtime, "spawn_agent", SPAWN)).status == 503
    await runtime.init()
    first = await command(runtime, "spawn_agent", SPAWN)
    assert first.status == 200 and first.body["success"], first.body
    assert first.body["data"]["recovery_mode"] == "initialized"
    listed = await command(runtime, "list_agents", {"project_id": "scope"})
    assert [a["agent_id"] for a in listed.body["data"]["agents"]] == [
        "11111111111111111111111111111111"
    ]
    sent = await command(runtime, "send_message", MESSAGE)
    assert sent.body == {"success": True, "data": {"content": "business reply"}}
    await runtime.stop()
    assert (await command(runtime, "send_message", MESSAGE)).status == 503
    restarted = make_runtime(tmp_path, unit_config=runtime_unit_config)
    await restarted.init()
    try:
        listed = await command(restarted, "list_agents", {"project_id": "scope"})
        assert listed.body["data"]["agents"] == []
        restored = await command(restarted, "spawn_agent", SPAWN)
        assert restored.body["data"]["recovery_mode"] == "restored"
        assert restored.body["data"]["incarnation_id"] != first.body["data"]["incarnation_id"]
        stopped = await command(
            restarted,
            "terminate_agent",
            {
                "project_id": "scope",
                "agent_id": "11111111111111111111111111111111",
                "name": "worker",
            },
        )
        assert stopped.body["success"] and stopped.body["data"]["terminated"]
    finally:
        await restarted.stop()


async def test_allowlist_validation_and_scope_gate(tmp_path):
    runtime = CoreRuntime(
        RuntimeConfig(
            scope_ref="scope", database_path=str(tmp_path / "db"), enabled_commands=("list_agents",)
        )
    )
    await runtime.init()
    try:
        assert (await command(runtime, "spawn_agent", SPAWN)).status == 403
        assert (await command(runtime, "list_agents", {"project_id": "other"})).status == 403
        assert (await command(runtime, "list_agents", {})).status == 422
        assert (await command(runtime, "health_check", {})).status == 422
        denied = await runtime.dispatch(
            "POST",
            "/v1/commands",
            principal=RuntimePrincipal(scope_ref="other"),
            body={"command": "list_agents", "payload": {"project_id": "scope"}},
        )
        assert denied.status == 403
    finally:
        await runtime.stop()


async def test_unknown_command_allowlist_fails_before_opening_storage(tmp_path):
    runtime = CoreRuntime(
        RuntimeConfig(
            scope_ref="scope", database_path=str(tmp_path / "db"), enabled_commands=("unknown",)
        )
    )
    with pytest.raises(ValueError):
        await runtime.init()
    assert runtime.status().phase == "failed" and not (tmp_path / "db").exists()


@pytest.mark.parametrize("finish_work", [True, False])
async def test_command_stop_waits_without_blocking_status(
    tmp_path, finish_work, runtime_unit_config
):
    entered, finish = asyncio.Event(), asyncio.Event()

    class ControlledLLM(EchoLLM):
        async def generate(self, messages, tools=None):
            entered.set()
            await finish.wait()
            return LLMResponse(content_blocks=[TextBlock(text="finished")])

    runtime = CoreRuntime(
        RuntimeConfig(
            scope_ref="scope",
            database_path=str(tmp_path / "db"),
            ability_source="builtin",
            enabled_commands=COMMANDS,
            stop_timeout=1 if finish_work else 0.05,
        ),
        llm_factory=lambda _: ControlledLLM(),
        unit_config=runtime_unit_config,
    )
    await runtime.init()
    assert (await command(runtime, "spawn_agent", SPAWN)).body["success"]
    calling = asyncio.create_task(command(runtime, "send_message", MESSAGE))
    await asyncio.wait_for(entered.wait(), timeout=1)
    stopping = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    state = await asyncio.wait_for(runtime.dispatch("GET", "/v1/status", principal=PRINCIPAL), 0.1)
    assert state.body["phase"] == "stopping"
    assert (await command(runtime, "list_agents", {"project_id": "scope"})).status == 503
    if finish_work:
        finish.set()
    await asyncio.wait_for(stopping, 2)
    result = await calling
    assert result.status == (200 if finish_work else 503)
    async with aiosqlite.connect(runtime.config.database_path) as db:
        rows = await (
            await db.execute("SELECT state FROM mm_deliveries WHERE attempts>0")
        ).fetchall()
    assert rows == [("completed" if finish_work else "interrupted",)]
    assert runtime.status().phase == "stopped" and not runtime._command_tasks
