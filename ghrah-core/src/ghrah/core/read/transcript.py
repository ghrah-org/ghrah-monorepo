# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从运行时存储域批量读取 Room 日志引用的权威事实。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import aiosqlite
from ghrah.protocol.views.observer_room import (
    MessageFactsView,
    RoomFactsQuery,
    RoomNodeTurnView,
    TranscriptDeliveryView,
    TranscriptFactsView,
)

from ghrah.communication.delivery_queue import (
    QUEUED_SQL,
    blocker_joins,
    blocker_reason_sql,
    delivery_projection,
)
from ghrah.context.persistence.sqlite_read import SqliteReadDomain
from ghrah.core.read.turn import turn_cards


def _object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError, TypeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _room_messages(
    db: aiosqlite.Connection,
    query: RoomFactsQuery,
) -> dict[str, tuple[MessageFactsView, str | None]]:
    authors = defaultdict(set)
    for ref in query.messages:
        authors[ref.message_id].update((ref.author, f"human:{ref.author}"))
    if not authors:
        return {}
    columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(mm_messages)")}
    if not columns:
        return {}
    origin = "origin_node_id" if "origin_node_id" in columns else "NULL AS origin_node_id"
    rows = await db.execute_fetchall(
        f"SELECT message_id, sender_id, content, metadata, {origin} FROM mm_messages "
        "WHERE message_id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(authors)),),
    )
    messages = {}
    for row in rows:
        message_id, sender, content, raw_meta, node_id = row
        metadata = _object(raw_meta)
        if sender not in authors[message_id] or not query.matches_message(metadata):
            continue
        room_data = metadata.get("room_data")
        blocks = metadata.get("content_blocks")
        targets = metadata.get("targets")
        message = MessageFactsView(
            message_id=message_id,
            sender_id=sender,
            content=content,
            content_blocks=blocks
            if isinstance(blocks, list) and blocks and all(isinstance(b, dict) for b in blocks)
            else None,
            targets=[target for target in targets if isinstance(target, str)]
            if isinstance(targets, list)
            else [],
            room_data=room_data if isinstance(room_data, dict) else {},
            room_has_message=bool(metadata.get("room_has_message", True)),
        )
        messages[message_id] = (message, node_id)
    return messages


async def _room_deliveries(
    db: aiosqlite.Connection,
    message_ids: list[str],
    *,
    with_turns: bool,
    with_results: bool,
) -> list[dict[str, Any]]:
    if not message_ids:
        return []
    ids = json.dumps(message_ids)
    columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(mm_deliveries)")}
    if not columns:
        return []
    queue_columns = {"parked", "turn_id", "target_session_id", "consumed_node_id"}
    if not with_results or not queue_columns <= columns:
        rows = await db.execute_fetchall(
            "SELECT * FROM mm_deliveries "
            "WHERE message_id IN (SELECT value FROM json_each(?)) ORDER BY seq",
            (ids,),
        )
        return [delivery_projection(row, position=0, blocker=None) for row in rows]

    reason = blocker_reason_sql(with_turns)
    rows = await db.execute_fetchall(
        "WITH page AS ("
        " SELECT d.*, r.delivery_id IS NOT NULL AS result_ready,"
        " CASE WHEN d.state='pending' AND d.turn_id IS NULL THEN ("
        "   SELECT COUNT(*) FROM mm_deliveries q WHERE q.recipient_id=d.recipient_id"
        f"   AND q.target_session_id IS d.target_session_id AND q.seq<=d.seq AND {QUEUED_SQL}"
        " ) ELSE 0 END AS position,"
        " CASE WHEN d.state='pending' AND d.turn_id IS NULL THEN ("
        "   SELECT prior.seq FROM mm_deliveries prior "
        + blocker_joins(with_turns)
        + " WHERE prior.recipient_id=d.recipient_id"
        "   AND prior.target_session_id IS d.target_session_id"
        "   AND prior.seq<d.seq AND prior.parked=0"
        f"   AND ({reason}) IS NOT NULL ORDER BY prior.seq LIMIT 1"
        " ) END AS blocker_seq"
        " FROM mm_deliveries d LEFT JOIN mm_delivery_results r ON r.delivery_id=d.delivery_id"
        " WHERE d.message_id IN (SELECT value FROM json_each(?))"
        ") SELECT page.*, prior.delivery_id AS blocker_id,"
        f" {reason} AS queue_block_reason FROM page"
        " LEFT JOIN mm_deliveries prior ON prior.seq=page.blocker_seq "
        + blocker_joins(with_turns)
        + " ORDER BY page.seq",
        (ids,),
    )
    return [
        delivery_projection(
            row,
            position=row["position"],
            blocker=(
                {"delivery_id": row["blocker_id"], "queue_block_reason": row["queue_block_reason"]}
                if row["blocker_id"] is not None
                else None
            ),
        )
        for row in rows
    ]


def _matches_legacy_node(row: aiosqlite.Row, query: RoomFactsQuery) -> bool:
    try:
        messages = json.loads(row["messages_delta"] or "[]")
    except ValueError, TypeError:
        messages = []
    return query.matches_legacy_node(_object(row["metadata"]), messages)


async def _node_turns(
    db: aiosqlite.Connection,
    query: RoomFactsQuery,
    messages: dict[str, tuple[MessageFactsView, str | None]],
) -> list[RoomNodeTurnView]:
    legacy = {(ref.node_id, ref.agent_id) for ref in query.nodes}
    origins = set()
    for message, node_id in messages.values():
        if node_id is not None:
            origins.add((node_id, message.sender_id))
        elif message.room_data.get("via") == "chain_filter":
            if isinstance(node_id := message.room_data.get("node_id"), str):
                legacy.add((node_id, message.sender_id))

    ids = {node_id for node_id, _ in legacy | origins}
    if not ids:
        return []
    rows = await db.execute_fetchall(
        "SELECT n.id, n.agent_name, n.turn_id, n.metadata, n.messages_delta FROM nodes n "
        "JOIN turns t ON t.turn_id=n.turn_id AND t.agent_name=n.agent_name "
        "AND t.session_id=n.session_id WHERE n.id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(ids)),),
    )
    return [
        RoomNodeTurnView(node_id=row["id"], agent_id=row["agent_name"], turn_id=row["turn_id"])
        for row in rows
        if (key := (row["id"], row["agent_name"])) in origins
        or (key in legacy and _matches_legacy_node(row, query))
    ]


async def _active_in_room(db: aiosqlite.Connection, query: RoomFactsQuery) -> set[str]:
    if not query.active_agent_ids:
        return set()
    rows = await db.execute_fetchall(
        "SELECT t.turn_id, m.metadata FROM turns t "
        "JOIN mm_deliveries d ON d.delivery_id=t.opener_delivery_id "
        "AND d.recipient_id=t.agent_name "
        "JOIN mm_messages m ON m.message_id=d.message_id "
        "WHERE t.status IN ('open', 'waiting_hitl') "
        "AND t.agent_name IN (SELECT value FROM json_each(?))",
        (json.dumps(query.active_agent_ids),),
    )
    return {row[0] for row in rows if query.matches_message(_object(row[1]))}


async def _turn_rows(
    db: aiosqlite.Connection,
    turn_ids: set[str],
    deliveries: list[dict[str, Any]],
) -> tuple[list[aiosqlite.Row], dict[str, list[dict[str, Any]]]]:
    if not turn_ids:
        return [], {}
    ids = json.dumps(sorted(turn_ids))
    rows = await db.execute_fetchall(
        "SELECT * FROM turns WHERE turn_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY opened_at, turn_id",
        (ids,),
    )
    stats = defaultdict(list)
    steps = defaultdict(int)
    injection_steps = {}
    consumed = {
        d.get("consumed_node_id") for d in deliveries if d.get("delivery_policy") == "inject"
    }
    for row in await db.execute_fetchall(
        "SELECT n.id, n.turn_id, n.metadata FROM nodes n "
        "JOIN turns t ON t.turn_id=n.turn_id AND t.agent_name=n.agent_name "
        "AND t.session_id=n.session_id WHERE n.turn_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY n.timestamp, n.id",
        (ids,),
    ):
        metadata = _object(row["metadata"])
        turn_id = row["turn_id"]
        stats[turn_id].append(metadata)
        if metadata.get("node_kind") not in ("compact", "incomplete"):
            steps[turn_id] += 1
            if row["id"] in consumed:
                injection_steps[turn_id, row["id"]] = steps[turn_id]
    for delivery in deliveries:
        if delivery.get("delivery_policy") == "inject":
            delivery["injection_step"] = injection_steps.get(
                (delivery.get("turn_id"), delivery.get("consumed_node_id"))
            )
    return list(rows), dict(stats)


class TranscriptReadStore:
    """读取经作者及 Room 归属校验的消息和轮次。"""

    def __init__(self, domain: SqliteReadDomain) -> None:
        self._domain = domain

    async def facts(self, query: RoomFactsQuery) -> TranscriptFactsView:
        """在同一快照中读取一页引用，查询次数不随引用数量增长。"""
        if not (query.messages or query.nodes or query.active_agent_ids):
            return TranscriptFactsView()
        async with self._domain.message_snapshot() as (db, has_messages):
            if db is None:
                return TranscriptFactsView()
            tables = {
                row[0]
                for row in await db.execute_fetchall(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('nodes','turns','hitl_requests','mm_deliveries','mm_messages',"
                    "'mm_delivery_results')"
                )
            }
            with_turns = {"turns", "nodes", "hitl_requests"} <= tables and any(
                row[1] == "turn_id" for row in await db.execute_fetchall("PRAGMA table_info(nodes)")
            )
            messages = await _room_messages(db, query) if has_messages else {}
            deliveries = await _room_deliveries(
                db,
                list(messages),
                with_turns=with_turns,
                with_results="mm_delivery_results" in tables,
            )
            node_turns, cards = [], []
            if with_turns and query.include_turns:
                node_turns = await _node_turns(db, query, messages)
                by_node = {(node.node_id, node.agent_id): node.turn_id for node in node_turns}
                for message, origin in messages.values():
                    node_id = origin or message.room_data.get("node_id")
                    if isinstance(node_id, str):
                        message.turn_id = by_node.get((node_id, message.sender_id))
                turn_ids = {node.turn_id for node in node_turns}
                turn_ids.update(d["turn_id"] for d in deliveries if d.get("turn_id"))
                if has_messages and {"mm_deliveries", "mm_messages"} <= tables:
                    turn_ids.update(await _active_in_room(db, query))
                rows, metadata = await _turn_rows(db, turn_ids, deliveries)
                cards = await turn_cards(db, rows, metadata)
            return TranscriptFactsView(
                messages=[message for message, _ in messages.values()],
                deliveries=[
                    TranscriptDeliveryView.model_validate(
                        {key: d[key] for key in TranscriptDeliveryView.model_fields if key in d}
                    )
                    for d in deliveries
                ],
                node_turns=node_turns,
                turns=cards,
            )
