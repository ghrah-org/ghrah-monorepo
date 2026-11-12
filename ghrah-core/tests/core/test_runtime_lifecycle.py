"""新增 Runtime 生命周期；旧 CoreUnit 行为不变。"""

import asyncio

import pytest
from ghrah.protocol.runtime import RuntimePrincipal, RuntimeSystemStatus

from ghrah.core.runtime import CoreRuntime, RuntimeConfig
from ghrah.core.unit import CoreUnit, CoreUnitConfig


def make_runtime(tmp_path, **kwargs):
    return CoreRuntime(
        RuntimeConfig(
            scope_ref="scope",
            database_path=str(tmp_path / "core.sqlite"),
            **kwargs,
        )
    )


async def test_old_unit_retains_original_lifecycle():
    unit = CoreUnit(CoreUnitConfig())
    await unit.start()  # 旧 start 仍是空实现，不新增 ready 门禁。
    result = await unit.handle_command("unknown", {}, None)
    assert result == {"success": False, "error": "Unknown command: unknown"}
    await unit.stop()


async def test_independent_init_is_idempotent_and_does_not_spawn(tmp_path):
    runtime = make_runtime(tmp_path)
    assert not (tmp_path / "core.sqlite").exists()
    assert not runtime.status().ready
    await asyncio.gather(runtime.init(), runtime.init())
    try:
        assert runtime.status().ready
        assert await runtime._unit.supervisor.list_agents() == []
        unit = runtime._unit
        await runtime.init()
        assert runtime._unit is unit
    finally:
        await runtime.stop()
    assert runtime.status().phase == "stopped"
    await runtime.stop()
    with pytest.raises(RuntimeError, match="cannot restart"):
        await runtime.init()


async def test_failed_assembly_releases_database_owner(tmp_path):
    runtime = make_runtime(tmp_path, ability_source="invalid")
    with pytest.raises(ValueError):
        await runtime.init()
    assert runtime.status().phase == "failed"
    response = await runtime.dispatch(
        "GET", "/v1/status", principal=RuntimePrincipal(scope_ref="scope")
    )
    assert RuntimeSystemStatus.model_validate(response.body).error is not None
    replacement = make_runtime(tmp_path)
    await replacement.init()
    await replacement.stop()


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_release_partial_resources(tmp_path, cancel):
    entered = asyncio.Event()

    class SlowRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            entered.set()
            await asyncio.Event().wait()

    runtime = SlowRuntime(
        RuntimeConfig(
            scope_ref="scope",
            database_path=str(tmp_path / "core.sqlite"),
            init_timeout=10 if cancel else 0.05,
        )
    )
    task = asyncio.create_task(runtime.init())
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert runtime.status().phase == "failed"
    replacement = make_runtime(tmp_path)
    await replacement.init()
    await replacement.stop()


async def test_second_runtime_cannot_take_active_scope(tmp_path):
    one, two = make_runtime(tmp_path), make_runtime(tmp_path)
    await one.init()
    try:
        with pytest.raises(RuntimeError, match="active manager"):
            await two.init()
        assert one.status().ready
        assert two.status().phase == "failed"
    finally:
        await one.stop()


async def test_dispatch_contract_scope_and_gate(tmp_path):
    runtime = make_runtime(tmp_path, enabled_commands=("list_agents",))
    principal = RuntimePrincipal(scope_ref="scope")
    response = await runtime.dispatch("GET", "/v1/status", principal=principal)
    status = RuntimeSystemStatus.model_validate(response.body)
    assert status.phase == "booting" and not status.ready
    assert (await runtime.dispatch("POST", "/v1/commands", principal=principal)).status == 503
    assert (
        await runtime.dispatch("GET", "/v1/status", principal=RuntimePrincipal(scope_ref="other"))
    ).status == 403
    method = await runtime.dispatch("POST", "/v1/status", principal=principal)
    assert method.status == 405 and method.headers["Allow"] == "GET"
    assert method.body["type"] == "about:blank"
    assert "code" not in method.body
    assert "details" not in method.body
    denied = await runtime.dispatch(
        "GET", "/v1/status", principal=RuntimePrincipal(scope_ref="other")
    )
    assert denied.body["code"] == "ACCESS_DENIED"
    assert "details" not in denied.body
    await runtime.init()
    try:
        command_method = await runtime.dispatch("GET", "/v1/commands", principal=principal)
        assert command_method.status == 405
        assert command_method.headers["Allow"] == "POST"
        assert "code" not in command_method.body
        meta = await runtime.dispatch("GET", "/v1/meta", principal=principal)
        assert meta.body["capabilities"] == [
            {"name": "runtime.lifecycle", "version": 1},
            {"name": "runtime.commands", "version": 1},
        ]
        ready = await runtime.dispatch("GET", "/v1/status", principal=principal)
        assert ready.body["ready"] and ready.body["boot_id"] == status.boot_id
    finally:
        await runtime.stop()


async def test_cancelled_stop_still_releases_resources(tmp_path):
    runtime = make_runtime(tmp_path)
    await runtime.init()
    entered, finish = asyncio.Event(), asyncio.Event()
    cleanup = runtime._cleanup

    async def slow_cleanup():
        entered.set()
        await finish.wait()
        await cleanup()

    runtime._cleanup = slow_cleanup
    stopping = asyncio.create_task(runtime.stop())
    await entered.wait()
    stopping.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert runtime.status().phase == "stopped"
    replacement = make_runtime(tmp_path)
    await replacement.init()
    await replacement.stop()
