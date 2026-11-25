# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""SQLite Room 批读的队列位置、只读快照和查询预算。"""

import sqlite3
from contextlib import asynccontextmanager

from ghrah.protocol.views.observer_room import RoomFactsQuery, RoomMessageReference

from ghrah.communication import MessageManager
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.persistence.sqlite_read import SqliteReadDomain
from ghrah.core.wiring import build_core_port


async def test_transcript_reads_full_queue_position_with_fixed_sql_budget(tmp_path, monkeypatch):
    path = tmp_path / "core.db"
    async with SqliteBackend(path) as backend:
        session = ContextManager("worker", persistence=backend)
        await session.persist()
    manager = MessageManager(
        path,
        adopt_legacy_chain=True,
        project_id="p",
        agent_ids=("worker",),
        session_routing=True,
    )
    await manager.open(start_dispatcher=False)
    try:
        messages = [
            await manager.send_external(
                sender="operator",
                recipient_ids=["worker"],
                content=f"input {index}",
                idempotency_key=str(index),
                metadata={"room_id": "room" if index else "another-room"},
            )
            for index in range(51)
        ]
    finally:
        await manager.close()

    original = SqliteReadDomain.message_snapshot
    statements = []

    @asynccontextmanager
    async def traced(store, **kwargs):
        async with original(store, **kwargs) as (db, has_messages):
            assert db is not None and db.in_transaction
            await db.set_trace_callback(statements.append)
            yield db, has_messages

    monkeypatch.setattr(SqliteReadDomain, "message_snapshot", traced)
    refs = [
        RoomMessageReference(message_id=message.message_id, author="operator")
        for message in messages[1:]
    ]
    port = build_core_port(scope_id="p", db_path=str(path))
    try:
        one = await port.transcript.facts(
            RoomFactsQuery(room_id="room", messages=refs[-1:], include_turns=True)
        )
        query_count = len(statements)
        statements.clear()
        page = await port.transcript.facts(
            RoomFactsQuery(room_id="room", messages=refs, include_turns=True)
        )
        assert len(statements) == query_count <= 8
        assert all(s.lstrip().upper().startswith(("SELECT", "PRAGMA", "WITH")) for s in statements)
        assert len(page.messages) == len(page.deliveries) == 50
        assert page.deliveries[-1] == one.deliveries[0]
        assert [d.queue_position for d in page.deliveries] == list(range(2, 52))
        assert all(d.queue_reason == "pending" for d in page.deliveries)
        assert port._registry.message_manager() is None
    finally:
        await port.aclose()


async def test_transcript_downgrades_legacy_deliveries_without_consumed_node(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE mm_meta (singleton INTEGER, project_id TEXT, version INTEGER);
            INSERT INTO mm_meta VALUES (1,'p',5);
            CREATE TABLE mm_messages (message_id TEXT, sender_id TEXT, content TEXT, metadata TEXT);
            INSERT INTO mm_messages VALUES ('m','human:operator','old','{"room_id":"room"}');
            CREATE TABLE mm_deliveries (
                seq INTEGER, message_id TEXT, delivery_id TEXT, recipient_id TEXT,
                state TEXT, parked INTEGER, turn_id TEXT, target_session_id TEXT
            );
            INSERT INTO mm_deliveries VALUES (1,'m','d','worker','pending',0,NULL,'s');
            CREATE TABLE mm_delivery_results (delivery_id TEXT);
        """)
    port = build_core_port(scope_id="p", db_path=str(path))
    try:
        facts = await port.transcript.facts(
            RoomFactsQuery(
                room_id="room",
                messages=[RoomMessageReference(message_id="m", author="operator")],
                include_turns=True,
            )
        )
        assert facts.messages[0].content == "old"
        assert facts.deliveries[0].delivery_id == "d"
        assert facts.deliveries[0].display_state == "queued"
        assert facts.deliveries[0].injection_step is None
    finally:
        await port.aclose()
