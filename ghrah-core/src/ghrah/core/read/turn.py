# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""本作用域轮次及审批的只读投影。"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

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
from ghrah.context.persistence.sqlite_read import SqliteReadDomain

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
    node_metadata: Sequence[dict[str, Any]],
    requests: list[aiosqlite.Row],
    now: datetime,
) -> TurnStatsView:
    totals = dict.fromkeys(_TOKEN_FIELDS, 0)
    steps = 0
    for metadata in node_metadata:
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
        node_count=len(node_metadata),
        cache_hit_rate=(
            totals["token_cache_read"] / totals["token_input"] if totals["token_input"] else None
        ),
        duration_ms=_elapsed_ms(
            turn.opened_at, now if turn.status in {"open", "waiting_hitl"} else turn.closed_at
        ),
        waiting_ms=waiting_ms,
        **totals,
    )


async def _openers(
    db: aiosqlite.Connection,
    delivery_ids: Iterable[str],
) -> dict[str, tuple[str, str | None]]:
    ids = list(delivery_ids)
    if not ids:
        return {}
    tables = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE name IN ('mm_deliveries', 'mm_messages')"
    )
    names = {row[0] for row in tables}
    if "mm_deliveries" not in names:
        return {}
    parent_column = "NULL"
    parent_join = ""
    if "mm_messages" in names:
        columns = await db.execute_fetchall("PRAGMA table_info(mm_messages)")
        if "origin_node_id" in {row[1] for row in columns}:
            parent_column = "n.turn_id"
            parent_join = (
                " LEFT JOIN mm_messages m ON m.message_id=d.message_id"
                " LEFT JOIN nodes n ON n.id=m.origin_node_id AND n.agent_name=m.sender_id"
            )
    rows = await db.execute_fetchall(
        f"SELECT d.delivery_id, d.message_id, {parent_column} FROM mm_deliveries d"
        f"{parent_join} WHERE d.delivery_id IN (SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    )
    return {row[0]: (row[1], row[2]) for row in rows}


async def turn_cards(
    db: aiosqlite.Connection,
    rows: Sequence[aiosqlite.Row],
    node_metadata: dict[str, list[dict[str, Any]]],
) -> list[TurnCardView]:
    """在调用方的快照中批量补全轮次卡的等待、序号和开轮信息。"""
    if not rows:
        return []
    turn_ids = json.dumps([row["turn_id"] for row in rows])
    waits = defaultdict(list)
    for row in await db.execute_fetchall(
        "SELECT h.turn_id, h.status, h.created_at, h.resolved_at FROM hitl_requests h "
        "JOIN turns t ON t.turn_id=h.turn_id AND t.agent_name=h.agent_name "
        "WHERE t.turn_id IN (SELECT value FROM json_each(?))",
        (turn_ids,),
    ):
        waits[row["turn_id"]].append(row)
    indices = dict(
        await db.execute_fetchall(
            "SELECT t.turn_id, (SELECT COUNT(*) FROM turns prior "
            "WHERE prior.agent_name=t.agent_name AND prior.session_id=t.session_id "
            "AND (prior.opened_at, prior.turn_id) <= (t.opened_at, t.turn_id)) "
            "FROM turns t WHERE t.turn_id IN (SELECT value FROM json_each(?))",
            (turn_ids,),
        )
    )
    openers = await _openers(
        db, (row["opener_delivery_id"] for row in rows if row["opener_delivery_id"])
    )
    now = datetime.now(UTC)
    cards = []
    for row in rows:
        turn = _turn_view(row)
        message_id, parent_turn_id = openers.get(turn.opener_delivery_id, (None, None))
        cards.append(
            TurnCardView(
                **turn.model_dump(),
                turn_index=indices[turn.turn_id],
                opener_message_id=message_id,
                parent_turn_id=parent_turn_id,
                stats=_stats(turn, node_metadata.get(turn.turn_id, []), waits[turn.turn_id], now),
            )
        )
    return cards


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

    def __init__(self, domain: SqliteReadDomain) -> None:
        self._domain = domain

    async def turn(self, *, agent_id: str, turn_id: str) -> TurnView | None:
        """读取所属 Agent 的单个轮次。"""
        async with self._domain.chain_snapshot() as (backend, _):
            row = await backend.load_turn(agent_id, turn_id) if backend else None
            return _turn_view(row) if row is not None else None

    async def turns(
        self, *, agent_id: str, session_id: str | None, status: str | None, limit: int
    ) -> TurnListView:
        """列出最近轮次。"""
        async with self._domain.chain_snapshot() as (backend, _):
            rows = await backend.list_turns(agent_id, session_id, status, limit) if backend else []
            return TurnListView(agent_id=agent_id, items=[_turn_view(row) for row in rows])

    async def detail(self, *, agent_id: str, turn_id: str) -> TurnDetailView | None:
        """读取同一快照内属于指定 Agent 的轮次、节点与统计。"""
        async with self._domain.chain_snapshot() as (backend, db):
            if backend is None or db is None:
                return None
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
            cards = await turn_cards(db, [row], {turn_id: [node.metadata for node in nodes]})
            return TurnDetailView(turn=cards[0], nodes=nodes)

    async def hitl_request(self, *, agent_id: str, promise_id: str) -> HitlRequestView | None:
        """读取所属 Agent 的审批请求及终态。"""
        async with self._domain.chain_snapshot() as (backend, _):
            row = await backend.load_hitl_request(agent_id, promise_id) if backend else None
            return _hitl_view(row) if row is not None else None

    async def pending_hitl(self, *, agent_id: str, session_id: str | None) -> HitlRequestListView:
        """列出尚待人工处理的请求。"""
        async with self._domain.chain_snapshot() as (backend, _):
            rows = await backend.list_pending_hitl(agent_id, session_id) if backend else []
            return HitlRequestListView(agent_id=agent_id, items=[_hitl_view(row) for row in rows])
