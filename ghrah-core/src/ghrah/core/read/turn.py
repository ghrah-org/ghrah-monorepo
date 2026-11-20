# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本作用域轮次及审批的只读投影。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from ghrah.protocol.views.core import (
    HitlRequestListView,
    HitlRequestView,
    NodeView,
    TurnListView,
    TurnView,
)
from ghrah.protocol.views.observer_room import TurnCardView, TurnDetailView, TurnStatsView

from ghrah.context.persistence import deserialize_node, serialize_node
from ghrah.context.persistence.sqlite_backend import SqliteBackend

_TOKEN_FIELDS = {
    "token_input": "input_tokens",
    "token_output": "output_tokens",
    "token_total": "total_tokens",
    "token_cache_read": "cache_read_tokens",
    "token_cache_write": "cache_write_tokens",
}


def _elapsed_ms(start: str | None, end: str | datetime | None) -> int | None:
    if start is None or end is None:
        return None
    try:
        opened = datetime.fromisoformat(start)
        closed = datetime.fromisoformat(end) if isinstance(end, str) else end
        return max(0, int((closed - opened).total_seconds() * 1000))
    except TypeError, ValueError:
        return None


def _stats(
    turn: TurnView,
    nodes: list[NodeView],
    requests: list[aiosqlite.Row],
    now: datetime,
) -> TurnStatsView:
    totals = dict.fromkeys(_TOKEN_FIELDS, 0)
    steps = 0
    for node in nodes:
        metadata = node.metadata
        if metadata.get("node_kind") in ("compact", "incomplete"):
            continue
        steps += 1
        usage = metadata.get("token_usage")
        if isinstance(usage, dict):
            for field, key in _TOKEN_FIELDS.items():
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    totals[field] += value
    waiting_ms = sum(
        _elapsed_ms(row["created_at"], now if row["status"] == "pending" else row["resolved_at"])
        or 0
        for row in requests
    )
    return TurnStatsView(
        steps=steps,
        node_count=len(nodes),
        cache_hit_rate=(
            totals["token_cache_read"] / totals["token_input"] if totals["token_input"] else None
        ),
        duration_ms=_elapsed_ms(
            turn.opened_at, now if turn.status in {"open", "waiting_hitl"} else turn.closed_at
        ),
        waiting_ms=waiting_ms,
        **totals,
    )


async def _opener(
    db: aiosqlite.Connection, delivery_id: str | None
) -> tuple[str | None, str | None]:
    if delivery_id is None:
        return None, None
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE name IN ('mm_deliveries', 'mm_messages')"
    )
    names = {row[0] for row in tables}
    if "mm_deliveries" not in names:
        return None, None
    async with db.execute(
        "SELECT message_id FROM mm_deliveries WHERE delivery_id=?", (delivery_id,)
    ) as cursor:
        opener = await cursor.fetchone()
    if opener is None:
        return None, None
    message_id = str(opener["message_id"])
    if "mm_messages" not in names:
        return message_id, None
    columns = await db.execute_fetchall("PRAGMA table_info(mm_messages)")
    if "origin_node_id" not in {row[1] for row in columns}:
        return message_id, None
    async with db.execute(
        "SELECT n.turn_id FROM mm_messages m JOIN nodes n ON n.id=m.origin_node_id "
        "WHERE m.message_id=? AND n.agent_name=m.sender_id",
        (message_id,),
    ) as cursor:
        parent = await cursor.fetchone()
    return message_id, parent["turn_id"] if parent is not None else None


def _hitl_view(row: aiosqlite.Row) -> HitlRequestView:
    data = {key: row[key] for key in HitlRequestView.model_fields}
    for field in ("tool_call", "options", "resolution"):
        data[field] = json.loads(data[field])
    return HitlRequestView.model_validate(data)


def _turn_view(row: aiosqlite.Row) -> TurnView:
    data = dict(row)
    metadata = json.loads(data.pop("metadata"))
    for key in ("abort_reason", "incomplete_node_id", "recovery_branch_id"):
        data[key] = metadata.get(key)
    return TurnView.model_validate(data)


class TurnReadStore:
    """按 Agent 查询轮次和待审批项，每次查询使用只读连接。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[SqliteBackend | None]:
        if not self._db_path.is_file():
            yield None
            return
        async with SqliteBackend(self._db_path, read_only=True) as backend:
            yield backend

    async def turn(self, *, agent_id: str, turn_id: str) -> TurnView | None:
        """读取所属 Agent 的单个轮次。"""
        async with self._read() as backend:
            row = await backend.load_turn(agent_id, turn_id) if backend else None
            return _turn_view(row) if row is not None else None

    async def turns(
        self, *, agent_id: str, session_id: str | None, status: str | None, limit: int
    ) -> TurnListView:
        """列出最近轮次。"""
        async with self._read() as backend:
            rows = await backend.list_turns(agent_id, session_id, status, limit) if backend else []
            return TurnListView(agent_id=agent_id, items=[_turn_view(row) for row in rows])

    async def detail(self, *, agent_id: str, turn_id: str) -> TurnDetailView | None:
        """读取同一快照内属于指定 Agent 的轮次、节点与统计。"""
        async with self._read() as backend:
            if backend is None:
                return None
            async with backend.read_snapshot() as db:
                row = await backend.load_turn(agent_id, turn_id)
                if row is None:
                    return None
                turn = _turn_view(row)
                node_rows = await db.execute_fetchall(
                    "SELECT * FROM nodes WHERE turn_id=? AND agent_name=? AND session_id=? "
                    "ORDER BY timestamp, id",
                    (turn_id, agent_id, turn.session_id),
                )
                nodes = [
                    NodeView.model_validate(
                        serialize_node(deserialize_node(backend._row_to_dict(node_row)))
                    )
                    for node_row in node_rows
                ]
                waits = await db.execute_fetchall(
                    "SELECT created_at, resolved_at, status FROM hitl_requests "
                    "WHERE turn_id=? AND agent_name=?",
                    (turn_id, agent_id),
                )
                index = await db.execute_fetchall(
                    "SELECT COUNT(*) FROM turns WHERE agent_name=? AND session_id=? "
                    "AND (opened_at, turn_id) <= (?, ?)",
                    (agent_id, turn.session_id, turn.opened_at, turn_id),
                )
                opener_message_id, parent_turn_id = await _opener(db, turn.opener_delivery_id)
                return TurnDetailView(
                    turn=TurnCardView(
                        **turn.model_dump(),
                        turn_index=index[0][0],
                        opener_message_id=opener_message_id,
                        parent_turn_id=parent_turn_id,
                        stats=_stats(turn, nodes, list(waits), datetime.now(UTC)),
                    ),
                    nodes=nodes,
                )

    async def hitl_request(self, *, agent_id: str, promise_id: str) -> HitlRequestView | None:
        """读取所属 Agent 的审批请求及终态。"""
        async with self._read() as backend:
            row = await backend.load_hitl_request(agent_id, promise_id) if backend else None
            return _hitl_view(row) if row is not None else None

    async def pending_hitl(self, *, agent_id: str, session_id: str | None) -> HitlRequestListView:
        """列出尚待人工处理的请求。"""
        async with self._read() as backend:
            rows = await backend.list_pending_hitl(agent_id, session_id) if backend else []
            return HitlRequestListView(agent_id=agent_id, items=[_hitl_view(row) for row in rows])
