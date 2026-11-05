# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from typing import Any

from ghrah.protocol.types import EventType
from ouroboros import Context  # type: ignore[import-untyped]
from ouroboros_testutil import wait_active

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import mount_unit
from ghrah.subject.runtime.service_keys import (
    OBSERVER_ENDPOINT,
    OBSERVER_EVENT_BUS,
)
from ghrah.subject.server.config import ObserverServerConfig
from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus
from ghrah.subject.server.router import ObserverRouter
from ghrah.subject.units.websocket_observer_endpoint import (
    WebSocketObserverEndpointUnit,
    _EngineDispatchAdapter,
)


async def test_observer_endpoint_composes_existing_server_components() -> None:
    config = SubjectConfig()
    unit = WebSocketObserverEndpointUnit(
        config,
        observer_config=ObserverServerConfig(ws_path="/observer"),
    )

    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)

        assert isinstance(unit.connection_manager, ConnectionManager)
        assert isinstance(unit.observer_event_bus, EventBus)
        assert isinstance(unit.router, ObserverRouter)
        assert ctx.get(OBSERVER_ENDPOINT.name) is unit
        assert ctx.get(OBSERVER_EVENT_BUS.name) is not unit.observer_event_bus
        assert any(getattr(route, "path", None) == "/observer" for route in unit.app.routes)


async def test_unified_events_forward_to_observer_event_bus() -> None:
    config = SubjectConfig()
    unit = WebSocketObserverEndpointUnit(config)

    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)

        # 事件统一：core 域（core:{type}）与 subject 域（event/{type}）双前缀直发
        # Agent 作用域事件必须携带非空 project_id/agent_id，否则被转发守卫丢弃
        ctx.emit(
            "core:agent_spawned",
            {"name": "agent-a", "project_id": "p1", "agent_id": "a1"},
        )
        ctx.emit("event/task_created", {"task_id": "t-1"})
        ctx.emit(
            f"core:{EventType.HITL_REQUEST.value}",
            {
                "promise_id": "promise-1",
                "agent_name": "agent-a",
                "project_id": "p1",
                "agent_id": "a1",
                "ability_name": "write_file",
                "tool_args": {"file_path": "notes.md"},
            },
        )

        # emit 将 async forward 回调调度为后台任务，轮询至三条事件落库
        events: list[dict[str, Any]] = []
        for _ in range(200):
            events = unit.observer_event_bus.event_store.replay_since(0)
            if len(events) >= 3:
                break
            await asyncio.sleep(0.01)

        assert events[0]["type"] == "agent_spawned"
        assert events[0]["payload"] == {
            "name": "agent-a",
            "agent_name": "agent-a",
            "project_id": "p1",
            "agent_id": "a1",
        }
        assert events[1]["type"] == "task_created"
        assert events[1]["payload"]["task_id"] == "t-1"
        assert events[2]["type"] == EventType.HITL_REQUEST.value
        assert events[2]["payload"]["promise_id"] == "promise-1"
        assert events[2]["payload"]["agent_name"] == "agent-a"


async def test_observer_event_bus_service_adapter_publishes_wire_event() -> None:
    config = SubjectConfig()
    unit = WebSocketObserverEndpointUnit(config)

    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)

        adapter = ctx.get(OBSERVER_EVENT_BUS.name)

        count = await adapter.publish(
            "agent_terminated",
            {"name": "agent-a", "project_id": "p1", "agent_id": "a1"},
        )

        assert count == 0
        events = unit.observer_event_bus.event_store.replay_since(0)
        assert events[-1]["type"] == "agent_terminated"
        assert events[-1]["payload"] == {
            "name": "agent-a",
            "agent_name": "agent-a",
            "project_id": "p1",
            "agent_id": "a1",
        }


async def test_unattributed_agent_scoped_events_are_dropped_and_counted() -> None:
    """Agent 作用域事件缺失 project_id/agent_id → 丢弃 + 计数，不落库。"""
    config = SubjectConfig()
    unit = WebSocketObserverEndpointUnit(config)

    async with Context() as ctx:
        fiber = ctx.plugin(mount_unit(unit))
        await wait_active(fiber)

        # 无任何归属
        ctx.emit("core:agent_terminated", {"name": "agent-a"})
        # 只有 project_id，缺 agent_id
        ctx.emit("core:action_chain_updated", {"project_id": "p1"})
        # 非 Agent 作用域事件不受影响
        ctx.emit("event/task_created", {"task_id": "t-1"})

        events: list[dict[str, Any]] = []
        for _ in range(200):
            events = unit.observer_event_bus.event_store.replay_since(0)
            if events:
                break
            await asyncio.sleep(0.01)

        # 只剩非 Agent 作用域事件；被丢弃的事件永不落库
        assert [e["type"] for e in events] == ["task_created"]
        assert unit.observer_event_bus.dropped_unattributed_events == 2


async def test_observer_preserves_project_agent_scope_for_subject_router() -> None:
    class ContextStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def serial(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((topic, payload))
            return {"success": True, "data": {"routed": True}, "error": None}

    ctx = ContextStub()
    adapter = _EngineDispatchAdapter(ctx)
    result = await adapter.dispatch_observer_command(
        "send_message",
        {
            "project_id": "p1",
            "agent_id": "b" * 32,
            "target": "planner",
            "content": "resume",
        },
    )

    assert result["success"] is True
    assert ctx.calls == [
        (
            "command/send_message",
            {
                "project_id": "p1",
                "agent_id": "b" * 32,
                "target": "planner",
                "content": "resume",
            },
        )
    ]
