"""新增进程内接入的显式启用、失败回收与结构化请求响应。"""

import asyncio

import pytest
from ghrah.core.runtime import CoreRuntime, RuntimeConfig

from ghrah.subject.runtime_connection import (
    RuntimeClient,
    RuntimeConnection,
    RuntimeConnectionConfig,
)


async def test_disabled_connection_has_no_side_effects(tmp_path):
    runtime = CoreRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    connection = RuntimeConnection(RuntimeConnectionConfig(), runtime)
    await connection.start()
    await connection.stop()
    assert runtime.status().phase == "booting"
    assert connection.client is None
    assert not list(tmp_path.iterdir())


async def test_connection_waits_ready_and_is_idempotent(tmp_path):
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref="scope",
            database_path=str(tmp_path / "db"),
        )
    )
    await asyncio.gather(connection.start(), connection.start())
    try:
        assert (await connection.client.status()).ready
    finally:
        await connection.stop()
    assert (await connection.client.status()).phase == "stopped"


async def test_init_failure_cleans_up_without_legacy_fallback(tmp_path):
    config = RuntimeConfig(
        scope_ref="scope", database_path=str(tmp_path / "db"), ability_source="invalid"
    )
    runtime = CoreRuntime(config)
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="scope"), runtime
    )
    with pytest.raises(RuntimeError, match="failed"):
        await connection.start()
    assert runtime.status().phase == "failed"
    replacement = CoreRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    await replacement.init()
    await replacement.stop()


async def test_ready_polling_is_bounded_without_events(tmp_path):
    runtime = CoreRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    client = RuntimeClient(runtime, scope_ref="scope")
    with pytest.raises(TimeoutError):
        await client.wait_ready(timeout=0.01)
    assert not list(tmp_path.iterdir())


async def test_cancellation_while_waiting_releases_owned_runtime(tmp_path):
    entered = asyncio.Event()

    class SlowRuntime(CoreRuntime):
        async def _initialize(self):
            await super()._initialize()
            entered.set()
            await asyncio.Event().wait()

    runtime = SlowRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    connection = RuntimeConnection(
        RuntimeConnectionConfig(enabled=True, scope_ref="scope"), runtime
    )
    task = asyncio.create_task(connection.start())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replacement = CoreRuntime(RuntimeConfig(scope_ref="scope", database_path=str(tmp_path / "db")))
    await replacement.init()
    await replacement.stop()


def test_new_routes_do_not_appear_in_old_observer_app():
    from ghrah.subject.server.config import ObserverServerConfig
    from ghrah.subject.units.websocket_observer_endpoint import _create_observer_app

    app = _create_observer_app(ObserverServerConfig(), None, None)
    paths = {route.path for route in app.routes}
    assert "/ws" in paths
    assert "/api/runtime/v1/status" not in paths


async def test_inprocess_rest_contract_status_and_business(tmp_path):
    connection = RuntimeConnection(
        RuntimeConnectionConfig(
            enabled=True,
            scope_ref="scope",
            database_path=str(tmp_path / "db"),
            enabled_commands=("list_agents",),
        )
    )
    await connection.start()
    try:
        response = await connection.client.request("GET", "/v1/status")
        assert response.status == 200 and response.body["ready"]
        meta = await connection.client.get("/v1/meta")
        assert meta.body["capabilities"] == [
            {"name": "runtime.lifecycle", "version": 1},
            {"name": "runtime.commands", "version": 1},
        ]
        listed = await connection.client.request(
            "POST",
            "/v1/commands",
            headers={},
            body={"command": "list_agents", "payload": {"project_id": "scope"}},
        )
        assert listed.status == 200
        assert listed.headers["Content-Type"] == "application/json"
        assert listed.body == {"success": True, "data": {"agents": []}}
        assert (await connection.client.get("/v1/clusters")).status == 404
    finally:
        await connection.stop()
    assert (await connection.client.status()).phase == "stopped"
