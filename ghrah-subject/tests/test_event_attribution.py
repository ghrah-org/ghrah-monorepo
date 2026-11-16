# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Subject 侧事件归属补齐（M1）。

Core 事件只携带 ``agent_id``；Subject 转发给 Observer 前用权威映射补回
``project_id``（顶层与嵌套 ``session``）。补齐失败或缺失 ``agent_id`` 的事件被
丢弃并计数——不猜测归属。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.types import Message

from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_attribution import fill_attribution
from ghrah.subject.server.event_bus import EventBus


def _bus(resolver: Any = None) -> EventBus:
    return EventBus(ConnectionManager(), agent_project_resolver=resolver)


async def test_agent_scoped_event_is_attributed_from_resolver() -> None:
    """缺 project_id 的 Agent 作用域事件由 resolver 补齐（含嵌套 session）。"""

    async def resolve(agent_id: str) -> str | None:
        return "p1" if agent_id == "a1" else None

    bus = _bus(resolve)
    await bus.emit_core_event(
        Message(
            type="agent_response",
            payload={"agent_id": "a1", "agent_name": "planner", "session": {"session_id": "s1"}},
        )
    )

    stored = bus.event_store.replay_since(0)
    assert [e["type"] for e in stored] == ["agent_response"]
    payload = stored[0]["payload"]
    assert payload["project_id"] == "p1"
    assert payload["session"]["project_id"] == "p1"
    assert bus.dropped_unattributed_events == 0


async def test_unknown_agent_is_dropped_and_counted() -> None:
    """resolver 解析不到归属 → 丢弃 + 计数，不落库。"""

    async def resolve(agent_id: str) -> str | None:
        return None

    bus = _bus(resolve)
    await bus.emit_core_event(
        Message(type="agent_response", payload={"agent_id": "ghost", "agent_name": "ghost"})
    )

    assert bus.event_store.replay_since(0) == []
    assert bus.dropped_unattributed_events == 1


async def test_existing_project_id_is_preserved_and_resolver_skipped() -> None:
    """迁移期兼容：payload 已带 project_id 时不再调用 resolver，但仍补嵌套 session。"""
    calls: list[str] = []

    async def resolve(agent_id: str) -> str | None:
        calls.append(agent_id)
        return "other"

    bus = _bus(resolve)
    await bus.emit_core_event(
        Message(
            type="session_created",
            payload={"agent_id": "a1", "project_id": "p-existing", "session": {"session_id": "s1"}},
        )
    )

    stored = bus.event_store.replay_since(0)
    assert stored[0]["payload"]["project_id"] == "p-existing"
    assert stored[0]["payload"]["session"]["project_id"] == "p-existing"
    assert calls == []


async def test_missing_agent_id_is_dropped_even_with_project_id() -> None:
    """只有 project_id、缺 agent_id 仍属未归属：丢弃 + 计数。"""

    async def resolve(agent_id: str) -> str | None:
        return "p1"

    bus = _bus(resolve)
    await bus.emit_core_event(Message(type="action_chain_updated", payload={"project_id": "p1"}))

    assert bus.event_store.replay_since(0) == []
    assert bus.dropped_unattributed_events == 1


async def test_non_agent_scoped_event_is_forwarded_without_attribution() -> None:
    """非 Agent 作用域事件不参与归属判定，原样转发。"""

    async def resolve(agent_id: str) -> str | None:
        return "p1"

    bus = _bus(resolve)
    await bus.emit_core_event(Message(type="task_created", payload={"task_id": "t-1"}))

    stored = bus.event_store.replay_since(0)
    assert stored[0]["type"] == "task_created"
    assert stored[0]["payload"] == {"task_id": "t-1"}


def test_fill_attribution_keeps_non_empty_values() -> None:
    """已有非空归属的字段不被覆盖（Subject 自己的域事件不受影响）。"""
    payload: dict[str, Any] = {"project_id": "p1", "session": {"project_id": "p2"}}
    fill_attribution(payload, "p9")
    assert payload["project_id"] == "p1"
    assert payload["session"]["project_id"] == "p2"
