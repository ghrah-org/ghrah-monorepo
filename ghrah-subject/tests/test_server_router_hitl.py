# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from ghrah.protocol.types import CommandType, Message

from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus
from ghrah.subject.server.router import ObserverRouter


class _FakeEngine:
    """Minimal engine stub returning a fixed dispatch result.

    The router only depends on ``engine.dispatch_observer_command``; this
    fake avoids wiring real Subject units while exercising the router's
    translation of the engine result dict into a ``command_result`` Message.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.last_call: dict[str, Any] | None = None

    async def dispatch_observer_command(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.last_call = {
            "command": command,
            "payload": payload,
            "request_id": request_id,
            "session_id": session_id,
        }
        return self._result


async def test_observer_router_hitl_response_uses_engine_dispatch() -> None:
    engine = _FakeEngine(
        {
            "success": False,
            "error": "HITL promise not found or expired: missing",
        }
    )
    connection_manager = ConnectionManager()
    event_bus = EventBus(connection_manager)
    router = ObserverRouter(connection_manager, event_bus, engine=engine)  # type: ignore[arg-type]

    result = await router.handle_command(
        Message(
            type=CommandType.HITL_RESPONSE.value,
            payload={"promise_id": "missing"},
            request_id="req-1",
        ),
        session_id="session-1",
    )

    assert result is not None
    assert result.payload.success is False
    assert result.payload.error == "HITL promise not found or expired: missing"
    assert engine.last_call == {
        "command": CommandType.HITL_RESPONSE.value,
        "payload": {"promise_id": "missing"},
        "request_id": "req-1",
        "session_id": "session-1",
    }


async def test_observer_router_translates_engine_success_to_command_result() -> None:
    engine = _FakeEngine({"success": True, "data": {"workspace_path": "ws"}})
    connection_manager = ConnectionManager()
    event_bus = EventBus(connection_manager)
    router = ObserverRouter(connection_manager, event_bus, engine=engine)  # type: ignore[arg-type]

    result = await router.handle_command(
        Message(
            type=CommandType.WORKSPACE_STATUS.value,
            payload={"agent_name": "agent-a"},
            request_id="req-2",
        ),
        session_id="session-2",
    )

    assert result is not None
    assert result.payload.success is True
    assert result.payload.data == {"workspace_path": "ws"}


async def test_observer_router_unknown_command_delegates_to_engine() -> None:
    engine = _FakeEngine({"success": False, "error": "Unknown command: frobnicate"})
    connection_manager = ConnectionManager()
    event_bus = EventBus(connection_manager)
    router = ObserverRouter(connection_manager, event_bus, engine=engine)  # type: ignore[arg-type]

    result = await router.handle_command(
        Message(type="frobnicate", payload={}, request_id="req-3"),
        session_id="session-3",
    )

    assert result is not None
    assert result.payload.success is False
    assert result.payload.error == "Unknown command: frobnicate"


async def test_observer_router_preserves_stable_error_detail() -> None:
    engine = _FakeEngine(
        {
            "success": False,
            "data": None,
            "error": "project_version_conflict",
            "error_detail": "expected version 2, got 3",
        }
    )
    connection_manager = ConnectionManager()
    event_bus = EventBus(connection_manager)
    router = ObserverRouter(connection_manager, event_bus, engine=engine)  # type: ignore[arg-type]

    result = await router.handle_command(
        Message(
            type=CommandType.PROJECT_ARCHIVE.value,
            payload={"project_id": "p1", "expected_version": 2},
            request_id="req-4",
        ),
        session_id="session-4",
    )

    assert result is not None
    assert result.payload.error == "project_version_conflict"
    assert result.payload.error_detail == "expected version 2, got 3"
