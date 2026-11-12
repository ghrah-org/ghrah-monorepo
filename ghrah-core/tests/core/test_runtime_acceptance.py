"""按初始化契约验收真实存储、就绪门禁和业务执行后的停止行为。"""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace

import aiosqlite
import pytest
from ghrah.protocol.runtime import RuntimePrincipal

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.core.unit import CoreUnit

PRINCIPAL = RuntimePrincipal(scope_ref="scope")


def config(tmp_path, **kwargs):
    return RuntimeConfig(
        enabled_commands=("list_agents",),
        scope_ref="scope",
        database_path=str(tmp_path / "db"),
        **kwargs,
    )


async def request(runtime, method="POST", path="/v1/commands"):
    return await runtime.dispatch(
        method,
        path,
        principal=PRINCIPAL,
        body={"command": "list_agents", "payload": {"project_id": "scope"}},
    )


async def test_booting_rejects_writes_without_waiting_for_init(tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()

    class SlowRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            entered.set()
            await finish.wait()

    runtime = SlowRuntime(config(tmp_path))
    initialization = asyncio.create_task(runtime.init())
    await entered.wait()
    try:
        manager = runtime._unit.supervisor.message_manager
        assert manager._dispatcher is None
        response = await asyncio.wait_for(request(runtime), timeout=0.5)
        assert response.status == 503 and response.body["code"] == "UNAVAILABLE"
        assert (await request(runtime, "GET", "/v1/status")).body["phase"] == "booting"
        finish.set()
        await initialization
        assert runtime.status().ready and manager._dispatcher is not None
        assert (await request(runtime)).body["data"]["agents"] == []
    finally:
        finish.set()
        await initialization
        await runtime.stop()


async def test_stop_rejects_already_queued_writes(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    await runtime._lock.acquire()
    queued = asyncio.create_task(request(runtime))
    await asyncio.sleep(0)
    stopping = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    try:
        status = await request(runtime, "GET", "/v1/status")
        assert status.body["phase"] == "stopping" and not status.body["ready"]
        assert (await asyncio.wait_for(request(runtime), timeout=0.5)).status == 503
    finally:
        runtime._lock.release()
    assert (await queued).status == 503
    await stopping
    restarted = CoreRuntime(config(tmp_path))
    try:
        await restarted.init()
        assert (await request(restarted)).body["data"]["agents"] == []
    finally:
        await restarted.stop()


async def test_stop_during_initialization_never_announces_ready(tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    phases = []

    class SlowRuntime(CoreRuntime):
        def _phase(self, phase, error=None):
            phases.append(phase)
            super()._phase(phase, error)

        async def _initialize(self):
            await super()._initialize()
            entered.set()
            await finish.wait()

    runtime = SlowRuntime(config(tmp_path))
    initialization = asyncio.create_task(runtime.init())
    await entered.wait()
    stopping = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    finish.set()
    await asyncio.gather(initialization, stopping)
    assert "ready" not in phases and runtime.status().phase == "stopped"
    assert not runtime._resources._scopes


async def test_cleanup_failure_does_not_hide_initialization_error(tmp_path):
    class BrokenRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            stop = self._unit.stop

            async def broken_stop():
                await stop()
                raise RuntimeError("secondary cleanup failure")

            self._unit.stop = broken_stop
            raise ValueError("primary initialization failure")

    runtime = BrokenRuntime(config(tmp_path))
    with pytest.raises(ValueError, match="primary initialization failure") as error:
        await runtime.init()
    assert "secondary cleanup failure" in runtime.status().error
    assert "primary initialization failure" in runtime.status().error
    assert error.value.__notes__
    assert not runtime._resources._scopes
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    await replacement.stop()


async def test_catalog_load_failure_releases_initialized_resources(tmp_path, monkeypatch):
    from ghrah.ability.loading import ability_catalog

    def unreadable_catalog():
        raise OSError("builtin catalog unreadable")

    runtime = CoreRuntime(config(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(ability_catalog, "load_all_builtin_manifests", unreadable_catalog)
        with pytest.raises(OSError, match="catalog unreadable"):
            await runtime.init()
    assert runtime.status().phase == "failed" and not runtime._resources._scopes
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    await replacement.stop()


async def test_runtime_recovers_crashed_delivery_without_starting_agents(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    await runtime.stop()
    runtime = CoreRuntime(config(tmp_path))
    script = """
import asyncio, os, sys
from ghrah.communication.message_manager import MessageManager, MessageKind
async def crash(message):
    os._exit(23)
async def main():
    async with MessageManager(sys.argv[1], project_id='scope', agent_ids=('a','b','c')) as manager:
        manager.bind('b', crash)
        await manager.send(sender_id='a', recipient_ids=('b','c'), content='durable',
                           kind=MessageKind.REQUEST, idempotency_key='crash')
        await manager.dispatch_pending()
asyncio.run(main())
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    crashed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, runtime.config.database_path],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert crashed.returncode == 23, crashed.stderr.decode()
    await runtime.init()
    try:
        assert runtime.status().ready
        assert await runtime._unit.supervisor.list_agents() == []
        async with aiosqlite.connect(runtime.config.database_path) as db:
            rows = await (
                await db.execute(
                    "SELECT recipient_id, state, attempts FROM mm_deliveries ORDER BY recipient_id"
                )
            ).fetchall()
        assert rows == [("b", "interrupted", 1), ("c", "pending", 0)]
        assert not runtime._unit.supervisor.message_manager._workers
    finally:
        await runtime.stop()


@pytest.mark.parametrize("finish_work", [True, False])
async def test_stop_with_real_agent_delivery(
    tmp_path, monkeypatch, finish_work, runtime_unit_config
):
    entered, finish = asyncio.Event(), asyncio.Event()

    class ControlledLLM:
        model = "runtime-acceptance"

        def configure_tools(self, tools):
            pass

        def apply_model_overrides(self, overrides):
            pass

        async def generate(self, messages, tools=None):
            entered.set()
            await finish.wait()
            return LLMResponse(content_blocks=[TextBlock(text="persisted reply")])

    llm = ControlledLLM()
    monkeypatch.setattr(
        "ghrah.core.runtime.CoreUnit",
        lambda settings: CoreUnit(replace(settings, llm_factory=lambda _: llm)),
    )
    runtime = CoreRuntime(
        replace(
            config(tmp_path, stop_timeout=1 if finish_work else 0.05),
            enabled_commands=("spawn_agent", "list_agents"),
        ),
        unit_config=runtime_unit_config,
    )
    await runtime.init()
    unit = runtime._unit
    spawned = await unit.handle_command(
        "spawn_agent",
        {
            "project_id": "scope",
            "config": {"name": "worker"},
            "manifest_ref": "tests.agent",
        },
        None,
    )
    assert spawned["success"], spawned
    sending = asyncio.create_task(unit.supervisor.send("worker", "work", timeout=1))
    await asyncio.wait_for(entered.wait(), timeout=1)
    stopping = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    assert (await request(runtime)).status == 503
    if finish_work:
        finish.set()
        assert await sending == "persisted reply"
    await asyncio.wait_for(stopping, timeout=2)
    sending.cancel()
    await asyncio.gather(sending, return_exceptions=True)
    assert runtime.status().phase == "stopped" and not runtime._resources._scopes
    async with aiosqlite.connect(runtime.config.database_path) as db:
        rows = await (
            await db.execute("SELECT state FROM mm_deliveries WHERE attempts>0")
        ).fetchall()
    assert rows == [("completed" if finish_work else "interrupted",)]
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    assert await replacement._unit.supervisor.list_agents() == []
    await replacement.stop()


async def test_delivery_recovery_failure_is_not_hidden_by_dispatch_loop(tmp_path, monkeypatch):
    from ghrah.communication.message_manager import MessageManager

    async def broken_recovery(self):
        raise OSError("delivery projection unavailable")

    runtime = CoreRuntime(config(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(MessageManager, "recover_room_projections", broken_recovery)
        with pytest.raises(OSError, match="projection unavailable"):
            await runtime.init()
    assert runtime.status().phase == "failed"
    assert "projection unavailable" in runtime.status().error
    assert not runtime._resources._scopes
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    await replacement.stop()


async def test_stop_failure_retains_diagnosis_and_releases_remaining_resources(tmp_path):
    runtime = CoreRuntime(config(tmp_path))
    await runtime.init()
    stop = runtime._unit.stop

    async def broken_stop():
        await stop()
        raise OSError("agent shutdown failed")

    runtime._unit.stop = broken_stop
    with pytest.raises(OSError, match="agent shutdown failed"):
        await runtime.stop()
    assert runtime.status().phase == "failed"
    assert "agent shutdown failed" in runtime.status().error
    assert not runtime._resources._scopes
    assert (await request(runtime)).status == 503
    replacement = CoreRuntime(config(tmp_path))
    await replacement.init()
    await replacement.stop()
