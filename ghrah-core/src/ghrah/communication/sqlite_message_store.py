# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SQLite 消息存储、独占所有权及领取事务。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import aiosqlite

from ghrah.communication.delivery_queue import (
    QUEUED_SQL,
    blocker_joins,
    blocker_reason_sql,
    display_state,
    has_turns,
    needs_delivery_repair,
    pending_turn_input_ids,
    release_turn_inputs,
    release_unowned_inline_waits,
    settle_stranded_deliveries,
    settle_terminal_turns,
    settle_turn_deliveries,
    settle_turn_opener,
)
from ghrah.communication.storage import (
    CommittedIteration,
    DeliveryClaim,
    DeliveryClaimCancelled,
    DeliveryClaimError,
    DeliveryExecution,
    DeliveryProgress,
    DeliveryRecord,
    DeliveryRecoveryProbe,
    DeliveryRepairError,
    DeliveryState,
    FinalDeliveryResult,
    MessageKind,
    MessageRecord,
    MessageSubmission,
    OutboundPreparation,
    PreparedMessage,
    RecoveredDelivery,
    ResumeCheckpoint,
    SavedDeliveryResult,
)
from ghrah.context import recovery, turn
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.serialization import deserialize_action_results
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.persistence.sqlite_turn_store import SqliteTurnStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mm_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_id TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS mm_messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    sender_id TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('request', 'notification', 'response')),
    reply_to TEXT REFERENCES mm_messages(message_id),
    idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    accepted_at REAL NOT NULL,
    origin_node_id TEXT,
    sender_session_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    external_sender INTEGER NOT NULL DEFAULT 0,
    UNIQUE (sender_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS mm_deliveries (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL UNIQUE,
    message_id TEXT NOT NULL REFERENCES mm_messages(message_id),
    recipient_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'processing', 'completed', 'failed', 'interrupted')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    consumed_node_id TEXT,
    consumed_session_id TEXT,
    target_session_id TEXT,
    blocked_reason TEXT,
    last_node_id TEXT,
    resume_requested INTEGER NOT NULL DEFAULT 0,
    turn_id TEXT,
    delivery_policy TEXT NOT NULL DEFAULT 'queue' CHECK (delivery_policy IN ('inject','queue')),
    parked INTEGER NOT NULL DEFAULT 0,
    parked_reason TEXT,
    UNIQUE (message_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS mm_inbox ON mm_deliveries(recipient_id, seq);
CREATE INDEX IF NOT EXISTS mm_replies ON mm_messages(reply_to, seq);
CREATE TABLE IF NOT EXISTS mm_room_projections (
    message_id TEXT PRIMARY KEY REFERENCES mm_messages(message_id),
    completed INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS mm_room_projection_pending ON mm_room_projections(completed)
    WHERE completed=0;
CREATE TABLE IF NOT EXISTS mm_inline_waits (
    message_id TEXT PRIMARY KEY REFERENCES mm_messages(message_id),
    parent_delivery_id TEXT NOT NULL REFERENCES mm_deliveries(delivery_id),
    state TEXT NOT NULL CHECK(state IN ('waiting','consumed','released'))
);
CREATE INDEX IF NOT EXISTS mm_inline_waits_waiting ON mm_inline_waits(parent_delivery_id)
    WHERE state='waiting';
CREATE TABLE IF NOT EXISTS mm_delivery_results (
    delivery_id TEXT PRIMARY KEY REFERENCES mm_deliveries(delivery_id),
    content TEXT NOT NULL,
    result_node_id TEXT,
    recorded_at REAL NOT NULL,
    content_blocks TEXT
);
"""
_DELIVERY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_deliveries_turn ON mm_deliveries(turn_id)",
    "CREATE INDEX IF NOT EXISTS mm_session_order ON mm_deliveries"
    "(recipient_id,target_session_id,seq)",
    "CREATE INDEX IF NOT EXISTS mm_delivery_candidates ON mm_deliveries"
    "(recipient_id,target_session_id,seq) "
    "WHERE state='pending' AND blocked_reason IS NULL AND parked=0",
    "CREATE INDEX IF NOT EXISTS mm_delivery_unfinished ON mm_deliveries(seq,delivery_id) "
    "WHERE state!='completed'",
    "CREATE INDEX IF NOT EXISTS mm_bound_inflight ON mm_deliveries(turn_id) "
    "WHERE turn_id IS NOT NULL AND state IN ('pending','processing')",
    "CREATE INDEX IF NOT EXISTS mm_unbound_processing ON mm_deliveries(state) "
    "WHERE state='processing' AND turn_id IS NULL",
)


class SqliteDeliverySettlement:
    """在轮次宿主事务内结算投递。

    对外只暴露 :class:`~ghrah.context.turn_store.DeliverySettlement` 协议；
    同域的 :class:`SqliteTurnStore` 通过 ``_settle_in_transaction`` 等内部方法
    在已持有的事务内调用，不再另行开锁。
    """

    __slots__ = ("_store",)

    def __init__(self, store: SqliteMessageStore) -> None:
        self._store = store

    async def converge(
        self,
        *,
        turn_id: str,
        opener_delivery_id: str | None,
        opener_state: str,
        opener_error: str,
        terminal_state: str,
    ) -> int:
        async with self._store.transaction() as db:
            return await self.converge_in_transaction(
                db,
                turn_id=turn_id,
                opener_delivery_id=opener_delivery_id,
                opener_state=opener_state,
                opener_error=opener_error,
                terminal_state=terminal_state,
            )

    async def release_inputs(self, *, turn_id: str, opener_delivery_id: str | None) -> list[str]:
        async with self._store.transaction() as db:
            return await self.release_inputs_in_transaction(
                db, turn_id=turn_id, opener_delivery_id=opener_delivery_id
            )

    async def pending_inputs(self, *, turn_id: str, opener_delivery_id: str | None) -> list[str]:
        async with self._store.transaction() as db:
            return await self.pending_inputs_in_transaction(
                db, turn_id=turn_id, opener_delivery_id=opener_delivery_id
            )

    @staticmethod
    async def converge_in_transaction(
        db: aiosqlite.Connection,
        *,
        turn_id: str,
        opener_delivery_id: str | None,
        opener_state: str,
        opener_error: str,
        terminal_state: str,
    ) -> int:
        """在轮次宿主事务内收敛开轮投递并释放未消费注入。"""
        await settle_turn_opener(
            db,
            turn_id=turn_id,
            opener_delivery_id=opener_delivery_id,
            state=opener_state,
            error=opener_error,
        )
        if opener_delivery_id is not None:
            await release_unowned_inline_waits(db, parent_delivery_id=opener_delivery_id)
        released = await settle_turn_deliveries(
            db,
            turn_id=turn_id,
            opener_delivery_id=opener_delivery_id,
            terminal_state=terminal_state,
        )
        return len(released)

    @staticmethod
    async def release_inputs_in_transaction(
        db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]:
        return await release_turn_inputs(db, turn_id=turn_id, opener_delivery_id=opener_delivery_id)

    @staticmethod
    async def pending_inputs_in_transaction(
        db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]:
        return await pending_turn_input_ids(
            db, turn_id=turn_id, opener_delivery_id=opener_delivery_id
        )


class SqliteMessageStore(SqliteTurnStore):
    """管理一个 SQLite 消息存储域的写连接和原子操作。"""

    def __init__(self, db_path: str | Path, scope_id: str) -> None:
        self.db_path = Path(db_path).resolve()
        self.scope_id = scope_id
        self.lock = asyncio.Lock()
        self.connection: aiosqlite.Connection | None = None
        self._owner: sqlite3.Connection | None = None
        self._closing = False
        self._context_writer = self.context_backend()
        self._settlement = SqliteDeliverySettlement(self)

    @property
    def delivery_settlement(self) -> SqliteDeliverySettlement:
        """本存储域的投递结算能力，供轮次事务在同事务内调用。"""
        return self._settlement

    def context_backend(self, _agent_config: object = None) -> SqliteBackend:
        """为本域的 Agent 建立上下文存储，并挂载同域的轮次存储。"""
        backend = SqliteBackend(self.db_path)
        backend.turn_store = self
        return backend

    def validate_context_backend(self, backend: object) -> None:
        """复核上下文是否可以参加本域事务。"""
        if not isinstance(backend, SqliteBackend) or backend.db_path.resolve() != self.db_path:
            raise ValueError("Atomic receive requires a same-file SqliteBackend")

    async def _apply_chain_changes(self, db: aiosqlite.Connection, changes: ContextChanges) -> None:
        """在消息存储的事务内写入链增量。"""
        await self._context_writer.apply_changes_in_transaction(db, changes)

    async def open(self, *, prepare_context: bool) -> None:
        """取得独占所有权，初始化并恢复消息库。"""
        if self.connection is not None or self._owner is not None:
            raise RuntimeError("MessageManager is already open")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        owner = sqlite3.connect(str(self.db_path) + ".owner", timeout=0, isolation_level=None)
        try:
            owner.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            owner.close()
            raise RuntimeError("Message database already has an active manager") from exc
        self._owner = owner
        self._closing = False
        try:
            if prepare_context:
                async with self.context_backend() as backend:
                    await backend.initialize()
            self.connection = await aiosqlite.connect(self.db_path)
            self.connection.row_factory = aiosqlite.Row
            await self.connection.execute("PRAGMA journal_mode=WAL")
            await self.connection.execute("PRAGMA synchronous=NORMAL")
            await self.connection.execute("PRAGMA foreign_keys=ON")
            await self.connection.execute("PRAGMA busy_timeout=5000")
            await self.connection.executescript(_SCHEMA)
            async with self.transaction() as db:
                row = await (await db.execute("SELECT * FROM mm_meta")).fetchone()
                if row is None:
                    await db.execute("INSERT INTO mm_meta VALUES (1, ?, 7)", (self.scope_id,))
                elif row["project_id"] != self.scope_id or row["version"] not in (
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                ):
                    raise ValueError("Message database scope/schema mismatch")
                for table, column in (
                    ("mm_messages", "origin_node_id"),
                    ("mm_deliveries", "consumed_node_id"),
                    ("mm_deliveries", "consumed_session_id"),
                    ("mm_messages", "sender_session_id"),
                    ("mm_deliveries", "target_session_id"),
                    ("mm_deliveries", "blocked_reason"),
                    ("mm_deliveries", "last_node_id"),
                ):
                    columns = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
                    if column not in {item[1] for item in columns}:
                        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
                columns = await (await db.execute("PRAGMA table_info(mm_deliveries)")).fetchall()
                if "resume_requested" not in {item[1] for item in columns}:
                    await db.execute(
                        "ALTER TABLE mm_deliveries ADD COLUMN resume_requested "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                columns = await (await db.execute("PRAGMA table_info(mm_messages)")).fetchall()
                names = {item[1] for item in columns}
                if "metadata" not in names:
                    await db.execute(
                        "ALTER TABLE mm_messages ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                    )
                if "external_sender" not in names:
                    await db.execute(
                        "ALTER TABLE mm_messages ADD COLUMN external_sender "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                columns = await (
                    await db.execute("PRAGMA table_info(mm_delivery_results)")
                ).fetchall()
                if "content_blocks" not in {item[1] for item in columns}:
                    await db.execute(
                        "ALTER TABLE mm_delivery_results ADD COLUMN content_blocks TEXT"
                    )
                projection_columns = await (
                    await db.execute("PRAGMA table_info(mm_room_projections)")
                ).fetchall()
                if "attempts" not in {item[1] for item in projection_columns}:
                    await db.execute(
                        "ALTER TABLE mm_room_projections ADD COLUMN attempts "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                delivery_columns = await (
                    await db.execute("PRAGMA table_info(mm_deliveries)")
                ).fetchall()
                names = {column[1] for column in delivery_columns}
                for name, definition in (
                    ("turn_id", "TEXT"),
                    (
                        "delivery_policy",
                        "TEXT NOT NULL DEFAULT 'queue' "
                        "CHECK (delivery_policy IN ('inject','queue'))",
                    ),
                    ("parked", "INTEGER NOT NULL DEFAULT 0"),
                    ("parked_reason", "TEXT"),
                ):
                    if name not in names:
                        await db.execute(
                            f"ALTER TABLE mm_deliveries ADD COLUMN {name} {definition}"
                        )
                for statement in _DELIVERY_INDEXES:
                    await db.execute(statement)
                await db.execute("UPDATE mm_meta SET version=7 WHERE singleton=1")
                # 已持久终态的首条投递先按该终态收敛，再归一进程遗留的处理状态。
                await settle_terminal_turns(db)
                await db.execute(
                    "UPDATE mm_deliveries SET state='interrupted', error=? "
                    "WHERE state='processing'",
                    ("Previous process stopped before durable completion",),
                )
                with_turns = await self._has_turn_schema(db)
                preserve_aborted = (
                    " AND NOT EXISTS (SELECT 1 FROM turns t WHERE t.turn_id=mm_deliveries.turn_id "
                    "AND t.status='aborted')"
                    if with_turns
                    else ""
                )
                # 已被消费但没有落最终结果的投递：agent 已经执行过，重跑可能重复副作用。
                # 旧路径按「已消费即完成」收尾；已裁定执行丢失的检查点仍可显式续跑。
                await db.execute(
                    "UPDATE mm_deliveries SET state='completed', error=? "
                    "WHERE state='interrupted' AND consumed_node_id IS NOT NULL "
                    "AND COALESCE(error,'') NOT IN ('turn_missing','worker_lost') "
                    "AND NOT EXISTS (SELECT 1 FROM mm_delivery_results r "
                    "WHERE r.delivery_id=mm_deliveries.delivery_id)" + preserve_aborted,
                    ("recovered: consumed before durable completion",),
                )
                if with_turns:
                    await recovery.mark_crash_scenes(db)
                    await turn.normalize_open_turns(db, now=datetime.now(UTC).isoformat())
                    await settle_terminal_turns(db)
                await release_unowned_inline_waits(db)
        except BaseException:
            await self.close()
            raise

    def stop_accepting(self) -> None:
        """停止接收新事务，连接仍供中断收敛使用。"""
        self._closing = True

    async def close(self) -> None:
        """释放写连接和独占锁。"""
        self.stop_accepting()
        async with self.lock:
            try:
                if self.connection is not None:
                    connection, self.connection = self.connection, None
                    await connection.close()
            finally:
                if self._owner is not None:
                    self._owner.close()
                    self._owner = None

    def require_connection(self) -> aiosqlite.Connection:
        """为尚未迁移的 SQLite 操作借用写连接。"""
        if self.connection is None or self._closing:
            raise RuntimeError("MessageManager is not open")
        return self.connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """在本域的独占写连接上执行一次事务。"""
        async with self.lock:
            db = self.require_connection()
            try:
                await db.execute("BEGIN IMMEDIATE")
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def claim_delivery(
        self,
        agent_id: str,
        *,
        session_aware: bool,
        session_routing: bool,
        require_turns: bool,
    ) -> DeliveryClaim | None:
        """重查队首、恢复资格和会话归属，提交后返回领取结果。"""
        delivery_id = None
        try:
            async with self.transaction() as db:
                with_turns = await self.dispatch_turn_schema(db, require_turns=require_turns)
                row = await (
                    await db.execute(
                        self._next_delivery_sql(with_turns),
                        (agent_id,),
                    )
                ).fetchone()
                if (
                    row is None
                    or row["state"] != DeliveryState.PENDING.value
                    or (
                        not row["resume_requested"]
                        and (row["turn_id"] is not None or row["consumed_node_id"] is not None)
                    )
                ):
                    return None
                delivery_id = row["delivery_id"]
                record = await self._read_message(db, row["message_id"])
                target_session = row["target_session_id"]
                resume_node = None
                if row["resume_requested"]:
                    resume_node, reason = await self._resume_checkpoint(db, row)
                    if reason or not session_aware:
                        await db.execute(
                            "UPDATE mm_deliveries SET state='interrupted', "
                            "resume_requested=0, error=? "
                            "WHERE delivery_id=?",
                            (reason or "atomic_receiver_required", delivery_id),
                        )
                        await release_unowned_inline_waits(db, parent_delivery_id=delivery_id)
                        return None
                    target_session = resume_node["session_id"]
                if target_session is None and session_routing:
                    await db.execute(
                        "UPDATE mm_deliveries SET blocked_reason='legacy_session_unassigned' "
                        "WHERE delivery_id=?",
                        (delivery_id,),
                    )
                    return None
                if target_session is not None:
                    reason = None
                    if not session_aware:
                        reason = "receiver_cannot_route_session"
                    elif await self._session_status(db, agent_id, target_session) != "open":
                        reason = "target_session_unavailable"
                    if reason:
                        await db.execute(
                            "UPDATE mm_deliveries SET blocked_reason=? WHERE delivery_id=?",
                            (reason, delivery_id),
                        )
                        return None
                turn_id = None
                if target_session is not None and resume_node is None:
                    now = datetime.now(UTC).isoformat()
                    await turn.normalize_open_turns(
                        db, agent_name=agent_id, session_id=target_session, now=now
                    )
                    turn_id = uuid4().hex
                    await turn.insert_turn_placeholder(
                        db,
                        turn.ActiveTurn(turn_id, agent_id, target_session, delivery_id, "mm"),
                        opened_at=now,
                    )
                await db.execute(
                    "UPDATE mm_deliveries SET state='processing', "
                    "attempts=attempts+1, resume_requested=0, turn_id=? "
                    "WHERE delivery_id=?",
                    (turn_id, delivery_id),
                )
                return DeliveryClaim(
                    execution=DeliveryExecution(
                        delivery_id=delivery_id,
                        agent_id=agent_id,
                        attempt=row["attempts"] + 1,
                        turn_id=turn_id,
                    ),
                    message=record,
                    target_session_id=target_session,
                    resume=ResumeCheckpoint(
                        node_id=resume_node["id"],
                        session_id=resume_node["session_id"],
                        branch_id=resume_node["created_on_branch_id"],
                    )
                    if resume_node is not None
                    else None,
                )
        except asyncio.CancelledError as exc:
            if delivery_id is not None:
                raise DeliveryClaimCancelled(delivery_id) from exc
            raise
        except Exception as exc:
            if delivery_id is not None:
                raise DeliveryClaimError(delivery_id, str(exc)) from exc
            raise

    async def has_claimable_delivery(self, agent_id: str, *, with_turns: bool) -> bool:
        """只读探测是否存在可领取候选。"""
        async with (
            self.lock,
            self.require_connection().execute(
                self._next_delivery_sql(with_turns), (agent_id,)
            ) as cursor,
        ):
            row = await cursor.fetchone()
        return row is not None and row["state"] == DeliveryState.PENDING.value

    async def commit_iteration(
        self,
        execution: DeliveryExecution,
        changes: ContextChanges,
        *,
        drafts: Sequence[PreparedMessage],
        consumed_deliveries: frozenset[str],
        inline_updates: Mapping[str, bool],
    ) -> CommittedIteration:
        """原子提交节点、输入消费、出站消息及同步等待变化。"""
        if changes.agent_name != execution.agent_id or len(changes.nodes) != 1:
            raise ValueError("Atomic iteration must contain one node owned by the recipient")
        node = changes.nodes[0]
        async with self.transaction() as db:
            delivery = await (
                await db.execute(
                    "SELECT * FROM mm_deliveries WHERE delivery_id=?", (execution.delivery_id,)
                )
            ).fetchone()
            if delivery is None:
                raise KeyError(execution.delivery_id)
            self._require_execution(delivery, execution)
            if execution.turn_id:
                await turn.require_active(db, execution.turn_id)
            if delivery["state"] != DeliveryState.PROCESSING.value:
                raise RuntimeError("Delivery is no longer processing")
            if delivery["target_session_id"] is not None:
                if node.session_id != delivery["target_session_id"]:
                    raise RuntimeError("Atomic node does not match frozen delivery session")
                await self._validate_session(db, execution.agent_id, node.session_id)
            head = await (
                await db.execute(
                    "SELECT head_node_id FROM branches WHERE branch_id=? AND session_id=?",
                    (node.created_on_branch_id, node.session_id),
                )
            ).fetchone()
            branch = changes.branches[0] if len(changes.branches) == 1 else None
            new_fork = False
            if head is None and branch is not None and branch.fork_point_node_id == node.parent_id:
                parent = await (
                    await db.execute(
                        "SELECT 1 FROM branches WHERE branch_id=? AND session_id=?",
                        (branch.parent_branch_id, node.session_id),
                    )
                ).fetchone()
                new_fork = parent is not None
            if not new_fork and (head is None or head[0] != node.parent_id):
                raise RuntimeError("Context branch head changed before atomic commit")
            compact = node.metadata.get("node_kind") == "compact"
            if compact and (node.messages_delta or node.ability_names != ["compact"] or drafts):
                raise ValueError(
                    "Compact commit must not consume input or publish outbound messages"
                )
            if compact and consumed_deliveries:
                raise ValueError("Compact commit must not consume injected input")
            input_ids = {message.metadata.get("delivery_id") for message in node.messages_delta}
            if consumed_deliveries:
                if not delivery["turn_id"] or consumed_deliveries - input_ids:
                    raise RuntimeError("Injected input is no longer consumable")
                cursor = await db.execute(
                    "UPDATE mm_deliveries SET consumed_node_id=?, "
                    "consumed_session_id=?, last_node_id=? "
                    "WHERE delivery_id IN (SELECT value FROM json_each(?)) "
                    "AND state='pending' AND consumed_node_id IS NULL AND turn_id=? "
                    "AND recipient_id=? AND target_session_id=? AND delivery_policy='inject' "
                    "AND COALESCE(blocked_reason,'')='' AND parked=0",
                    (
                        node.id,
                        node.session_id,
                        node.id,
                        json.dumps(list(consumed_deliveries)),
                        delivery["turn_id"],
                        execution.agent_id,
                        node.session_id,
                    ),
                )
                if cursor.rowcount != len(consumed_deliveries):
                    raise RuntimeError("Injected input is no longer consumable")
            if delivery["turn_id"] and node.turn_id != delivery["turn_id"]:
                raise RuntimeError("Atomic node does not match the delivery turn")
            await self._context_writer.apply_changes_in_transaction(db, changes)
            if delivery["consumed_node_id"] is None and not compact:
                if not any(
                    message.metadata.get("delivery_id") == execution.delivery_id
                    for message in node.messages_delta
                ):
                    raise RuntimeError("First atomic iteration must include the input delivery")
                await db.execute(
                    "UPDATE mm_deliveries SET consumed_node_id=?, consumed_session_id=? "
                    "WHERE delivery_id=?",
                    (node.id, node.session_id, execution.delivery_id),
                )
            if delivery["turn_id"]:
                await turn.backfill_start(db, node)
            for draft in drafts:
                await self.insert_message(
                    db,
                    draft.sender_id,
                    draft.recipient_ids,
                    draft.content,
                    draft.kind,
                    draft.idempotency_key,
                    None,
                    draft.message_id,
                    node.id,
                    draft.sender_session_id,
                    dict(draft.target_sessions),
                    draft.metadata,
                )
                if draft.metadata.get("inline_reply"):
                    await db.execute(
                        "INSERT OR IGNORE INTO mm_inline_waits VALUES (?, ?, 'waiting')",
                        (draft.message_id, execution.delivery_id),
                    )
            for request_id, consumed in inline_updates.items():
                cursor = await db.execute(
                    "UPDATE mm_inline_waits SET state=? "
                    "WHERE message_id=? AND parent_delivery_id=? AND state='waiting'",
                    ("consumed" if consumed else "released", request_id, execution.delivery_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Inline wait is no longer reserved by this execution")
                if consumed:
                    await db.execute(
                        "UPDATE mm_deliveries SET state='completed', consumed_node_id=?, "
                        "consumed_session_id=?, last_node_id=? WHERE message_id IN "
                        "(SELECT message_id FROM mm_messages WHERE reply_to=?) AND recipient_id=? "
                        "AND state='pending' AND turn_id IS NULL AND consumed_node_id IS NULL",
                        (node.id, node.session_id, node.id, request_id, execution.agent_id),
                    )
            await db.execute(
                "UPDATE mm_deliveries SET last_node_id=? WHERE delivery_id=?",
                (node.id, execution.delivery_id),
            )
        return CommittedIteration(
            node_id=node.id, input_consumed=delivery["consumed_node_id"] is not None or not compact
        )

    async def save_final_result(
        self, execution: DeliveryExecution, result: FinalDeliveryResult
    ) -> SavedDeliveryResult:
        """确认执行归属，并在第一段事务保存结果与轮次终态。"""
        delivery_id = execution.delivery_id
        node_id = result.node_id
        if node_id is None:
            # 原子接收可能已经完成两段持久收尾，回调返回时只需确认其结果。
            async with (
                self.lock,
                self.require_connection().execute(
                    "SELECT d.recipient_id, d.attempts, d.turn_id, r.content FROM mm_deliveries d "
                    "JOIN mm_delivery_results r ON r.delivery_id=d.delivery_id "
                    "WHERE d.delivery_id=? AND d.state='completed'",
                    (delivery_id,),
                ) as cursor,
            ):
                completed = await cursor.fetchone()
            if completed is not None:
                self._require_execution(completed, execution)
                if completed["content"] != result.content:
                    raise ValueError("Final result conflicts with the durable result")
                return SavedDeliveryResult(needs_completion=False)
        # 最终结果一旦落库，后续操作只重试事务，不再调用模型或工具。
        async with self.transaction() as db:
            delivery = await (
                await db.execute("SELECT * FROM mm_deliveries WHERE delivery_id=?", (delivery_id,))
            ).fetchone()
            if delivery is None:
                raise KeyError(delivery_id)
            self._require_execution(delivery, execution)
            existing = await (
                await db.execute(
                    "SELECT * FROM mm_delivery_results WHERE delivery_id=?", (delivery_id,)
                )
            ).fetchone()
            if existing is not None:
                if existing["content"] != result.content or (
                    node_id is not None and existing["result_node_id"] != node_id
                ):
                    raise ValueError("Final result conflicts with the durable result")
            else:
                if delivery["turn_id"]:
                    await turn.require_active(db, delivery["turn_id"])
                if delivery["state"] != DeliveryState.PROCESSING.value:
                    raise RuntimeError("Only processing deliveries may record a final result")
                if delivery["consumed_node_id"] is not None and node_id is None:
                    raise ValueError("Atomic final result requires a context node reference")
                if node_id is not None:
                    node = await (
                        await db.execute(
                            "SELECT agent_name, session_id FROM nodes WHERE id=?", (node_id,)
                        )
                    ).fetchone()
                    if node is None or (node[0], node[1]) != (
                        delivery["recipient_id"],
                        delivery["consumed_session_id"],
                    ):
                        raise ValueError(
                            "Final result node does not belong to the consumed session"
                        )
                await db.execute(
                    "INSERT INTO mm_delivery_results "
                    "(delivery_id, content, result_node_id, recorded_at, content_blocks) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        delivery_id,
                        result.content,
                        node_id,
                        time.time(),
                        json.dumps(result.content_blocks)
                        if result.content_blocks is not None
                        else None,
                    ),
                )
            closed = None
            if delivery["turn_id"] and existing is None:
                closed = await turn.close_turn(
                    db,
                    turn_id=delivery["turn_id"],
                    status="closed",
                    closing_kind="completed",
                    end_node_id=node_id or delivery["last_node_id"],
                    closed_at=datetime.now(UTC).isoformat(),
                )
                await settle_turn_deliveries(
                    db,
                    turn_id=delivery["turn_id"],
                    opener_delivery_id=delivery_id,
                    terminal_state="completed",
                )
        return SavedDeliveryResult(
            needs_completion=True,
            turn_closure=turn.ordinary_closure(turn.turn_record(closed) if closed else None),
        )

    async def finish_delivery(self, delivery_id: str) -> str | None:
        """仅凭持久结果写回复并完成投递，返回需要通知的原消息 ID。"""
        async with self.transaction() as db:
            row = await (
                await db.execute(
                    "SELECT d.state, d.message_id, d.recipient_id, d.target_session_id, "
                    "d.consumed_session_id, d.turn_id, d.last_node_id, "
                    "r.content, r.result_node_id, r.content_blocks "
                    "FROM mm_deliveries d JOIN mm_delivery_results r "
                    "ON r.delivery_id=d.delivery_id WHERE d.delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("No durable final result to complete")
            if row["state"] == DeliveryState.COMPLETED.value:
                return None
            if row["turn_id"] and await has_turns(db):
                closed = await turn.close_turn(
                    db,
                    turn_id=row["turn_id"],
                    status="closed",
                    closing_kind="completed",
                    end_node_id=row["result_node_id"] or row["last_node_id"],
                    closed_at=datetime.now(UTC).isoformat(),
                )
                if closed is not None:
                    await settle_turn_deliveries(
                        db,
                        turn_id=row["turn_id"],
                        opener_delivery_id=delivery_id,
                        terminal_state="completed",
                    )
            message = await self._read_message(db, row["message_id"])
            if message.kind == MessageKind.REQUEST:
                reply_id = await self.insert_message(
                    db,
                    row["recipient_id"],
                    (message.sender_id,),
                    row["content"],
                    MessageKind.RESPONSE,
                    f"response:{delivery_id}",
                    message.message_id,
                    origin_node_id=row["result_node_id"],
                    sender_session_id=row["target_session_id"] or row["consumed_session_id"],
                    target_sessions=(
                        {message.sender_id: message.sender_session_id}
                        if message.sender_session_id is not None
                        else None
                    ),
                    metadata={
                        **(
                            {"content_blocks": json.loads(row["content_blocks"])}
                            if row["content_blocks"]
                            else {}
                        ),
                        **{
                            k: v
                            for k, v in message.metadata.items()
                            if k in ("room_id", "room_ids")
                        },
                        **(
                            {"room_projection": True}
                            if message.metadata.get("room_id") or message.metadata.get("room_ids")
                            else {}
                        ),
                    },
                )
                if message.external_sender or message.metadata.get("return_reply"):
                    await db.execute(
                        "UPDATE mm_deliveries SET state='completed' WHERE message_id=?", (reply_id,)
                    )
            await db.execute(
                "UPDATE mm_deliveries SET state='completed', error=NULL WHERE delivery_id=?",
                (delivery_id,),
            )
            await release_unowned_inline_waits(db, parent_delivery_id=delivery_id)
        return row["message_id"]

    async def unfinished_completions(self) -> tuple[str, ...]:
        """列出有持久结果但尚待收尾的投递。"""
        async with self.lock:
            rows = await (
                await self.require_connection().execute(
                    "SELECT d.delivery_id FROM mm_deliveries d "
                    "CROSS JOIN mm_delivery_results r ON d.delivery_id=r.delivery_id "
                    "WHERE d.state != 'completed' ORDER BY d.seq"
                )
            ).fetchall()
        return tuple(row[0] for row in rows)

    @staticmethod
    def _require_execution(delivery: aiosqlite.Row, execution: DeliveryExecution) -> None:
        if (
            delivery["recipient_id"] != execution.agent_id
            or delivery["attempts"] != execution.attempt
            or delivery["turn_id"] != execution.turn_id
        ):
            raise turn.TurnRevokedError("Delivery execution no longer owns the claim")

    async def probe_delivery_recovery(
        self, workers: Collection[str], *, require_turns: bool, check_repair: bool
    ) -> DeliveryRecoveryProbe:
        """只读探测。空闲队列无需进入恢复写事务。"""
        async with self.lock:
            db = self.require_connection()
            with_turns = await self.dispatch_turn_schema(db, require_turns=require_turns)
            needs_repair = check_repair and await needs_delivery_repair(
                db, with_turns=with_turns, workers=workers
            )
        return DeliveryRecoveryProbe(with_turns, needs_repair)

    async def repair_deliveries(
        self, workers: Collection[str], *, require_turns: bool
    ) -> tuple[RecoveredDelivery, ...]:
        """在同一写事务复核无主投递、轮次终态和回复预约。"""
        try:
            async with self.transaction() as db:
                with_turns = await self.dispatch_turn_schema(db, require_turns=require_turns)
                await settle_terminal_turns(db)
                settled = await settle_stranded_deliveries(
                    db,
                    with_turns=with_turns,
                    workers=workers,
                    now=datetime.now(UTC).isoformat(),
                )
                await release_unowned_inline_waits(db)
        except sqlite3.Error as exc:
            raise DeliveryRepairError(str(exc)) from exc
        return tuple(
            RecoveredDelivery(row["message_id"], row["recipient_id"], closure)
            for row, closure in settled
        )

    async def record_failure(
        self,
        delivery_id: str,
        state: DeliveryState,
        error: str,
        *,
        session_unavailable: bool = False,
        execution: DeliveryExecution | None = None,
    ) -> RecoveredDelivery | None:
        """保存执行失败或中断。关闭期间保留写连接供 worker 收敛。"""
        async with self.lock:
            db = self.connection
            if db is None:
                return None
            try:
                await db.execute("BEGIN IMMEDIATE")
                delivery = await (
                    await db.execute(
                        "SELECT turn_id, last_node_id, consumed_node_id, message_id, "
                        "recipient_id, attempts FROM mm_deliveries WHERE delivery_id=?",
                        (delivery_id,),
                    )
                ).fetchone()
                if delivery is not None and execution is not None:
                    self._require_execution(delivery, execution)
                if delivery is not None and delivery["turn_id"]:
                    async with db.execute(
                        "SELECT status, opener_delivery_id FROM turns WHERE turn_id=?",
                        (delivery["turn_id"],),
                    ) as cursor:
                        stored = await cursor.fetchone()
                    if stored is not None and stored["status"] == "aborted":
                        await SqliteDeliverySettlement.converge_in_transaction(
                            db,
                            turn_id=delivery["turn_id"],
                            opener_delivery_id=stored["opener_delivery_id"],
                            opener_state="interrupted",
                            opener_error="aborted_by_user",
                            terminal_state="interrupted",
                        )
                        await db.commit()
                        return RecoveredDelivery(
                            delivery["message_id"], delivery["recipient_id"], None
                        )
                blocked = (
                    session_unavailable
                    and delivery is not None
                    and delivery["consumed_node_id"] is None
                )
                if blocked:
                    await db.execute(
                        "UPDATE mm_deliveries SET state='pending', turn_id=NULL, "
                        "blocked_reason='target_session_unavailable', error=?, "
                        "resume_requested=0 WHERE delivery_id=?",
                        (error, delivery_id),
                    )
                else:
                    # 领取事务回滚后投递仍为 pending；必须在这里终结，
                    # 否则调度器会反复领取同一行形成热循环。
                    await db.execute(
                        "UPDATE mm_deliveries SET state=?, error=?, resume_requested=0 "
                        "WHERE delivery_id=? AND (state IN ('processing', 'pending') OR "
                        "(state IN ('failed', 'interrupted') AND EXISTS "
                        "(SELECT 1 FROM mm_delivery_results r "
                        "WHERE r.delivery_id=mm_deliveries.delivery_id)))",
                        (state.value, error, delivery_id),
                    )
                closed = None
                if delivery is not None and delivery["turn_id"]:
                    # session 不可用不是业务失败：即使输入已消费（投递按既有
                    # 语义记 failed），轮次也收敛为 interrupted，避免终态语义混淆。
                    failed = (
                        state == DeliveryState.FAILED and not blocked and not session_unavailable
                    )
                    closed = await turn.close_turn(
                        db,
                        turn_id=delivery["turn_id"],
                        status="failed" if failed else "interrupted",
                        closing_kind="error" if failed else "cancelled",
                        end_node_id=delivery["last_node_id"],
                        closed_at=datetime.now(UTC).isoformat(),
                    )
                    await settle_turn_deliveries(
                        db,
                        turn_id=delivery["turn_id"],
                        opener_delivery_id=delivery_id,
                        terminal_state="failed" if failed else "interrupted",
                    )
                await release_unowned_inline_waits(db, parent_delivery_id=delivery_id)
                await db.commit()
                if delivery is None:
                    return None
                return RecoveredDelivery(
                    delivery["message_id"],
                    delivery["recipient_id"],
                    turn.ordinary_closure(turn.turn_record(closed) if closed else None),
                )
            except BaseException:
                await db.rollback()
                raise

    async def cancel_delivery(self, delivery_id: str) -> str:
        """取消尚未领取的排队投递，保留显式重试入口。"""
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE mm_deliveries SET state='interrupted', error='cancelled_by_user', "
                "blocked_reason=NULL WHERE delivery_id=? AND state='pending' AND turn_id IS NULL "
                "AND consumed_node_id IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM mm_delivery_results r "
                "WHERE r.delivery_id=mm_deliveries.delivery_id) RETURNING message_id",
                (delivery_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("delivery_not_cancelable")
        return row["message_id"]

    async def retry_delivery(self, delivery_id: str) -> None:
        """显式重试失败或中断投递；调用方须确认重复副作用风险。"""
        async with self.transaction() as db:
            cursor = await db.execute(
                "UPDATE mm_deliveries SET state='pending', error=NULL, turn_id=NULL "
                "WHERE delivery_id=? AND state IN ('failed', 'interrupted') "
                "AND consumed_node_id IS NULL "
                "AND blocked_reason IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM mm_delivery_results r "
                "WHERE r.delivery_id=mm_deliveries.delivery_id)",
                (delivery_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Delivery is missing or not retryable")

    async def delivery_progress(self, delivery_id: str) -> DeliveryProgress:
        """查询消费与最后提交节点，说明当前是否可申请续跑。"""
        async with self.lock:
            db = self.require_connection()
            row = await (
                await db.execute(
                    "SELECT d.*, r.delivery_id AS result_id, r.result_node_id "
                    "FROM mm_deliveries d LEFT JOIN mm_delivery_results r "
                    "ON r.delivery_id=d.delivery_id WHERE d.delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if row is None:
                raise KeyError(delivery_id)
            checkpoint, reason = await self._resume_checkpoint(db, row)
        delivery = self.delivery_record(row)
        if row["result_id"] is not None:
            reason = "final_result_already_saved"
        elif row["blocked_reason"]:
            reason = row["blocked_reason"]
        elif row["state"] not in ("failed", "interrupted"):
            reason = "delivery_not_interrupted"
        return DeliveryProgress(
            delivery,
            checkpoint["created_on_branch_id"] if checkpoint else None,
            checkpoint["iteration"] if checkpoint else None,
            reason,
        )

    async def request_resume(
        self,
        delivery_id: str,
        *,
        expected_last_node_id: str,
        recipient_busy: Callable[[str], bool],
    ) -> None:
        """复核持久检查点与回复归属，并在同一事务预约显式续跑。"""
        async with self.transaction() as db:
            row = await (
                await db.execute("SELECT * FROM mm_deliveries WHERE delivery_id=?", (delivery_id,))
            ).fetchone()
            if row is None:
                raise KeyError(delivery_id)
            if row["state"] not in ("failed", "interrupted") or row["blocked_reason"]:
                raise ValueError("Delivery is not an unblocked interrupted execution")
            if row["last_node_id"] != expected_last_node_id:
                raise ValueError("Expected checkpoint does not match delivery progress")
            result = await (
                await db.execute(
                    "SELECT 1 FROM mm_delivery_results WHERE delivery_id=?", (delivery_id,)
                )
            ).fetchone()
            if result is not None:
                raise ValueError("Final result already saved; recover completion instead")
            checkpoint, reason = await self._resume_checkpoint(db, row)
            if reason:
                raise ValueError(f"Cannot resume: {reason}")
            assert checkpoint is not None
            if recipient_busy(row["recipient_id"]):
                raise ValueError("Recipient still has an active worker")
            if request_ids := self._checkpoint_inline_requests(checkpoint):
                # 与续跑申请同事务认领回复，普通邮箱领取也必须通过写事务。
                await db.execute(
                    "UPDATE mm_inline_waits SET state='waiting' WHERE parent_delivery_id=? "
                    "AND state='released' AND message_id IN (SELECT value FROM json_each(?))",
                    (delivery_id, json.dumps(request_ids)),
                )
            await db.execute(
                "UPDATE mm_deliveries SET state='pending', resume_requested=1, error=NULL "
                "WHERE delivery_id=?",
                (delivery_id,),
            )

    async def unblock_delivery(self, delivery_id: str) -> None:
        """目标会话恢复开放后解除阻塞，保留原归属，不自动改投其他会话。"""
        async with self.transaction() as db:
            row = await (
                await db.execute("SELECT * FROM mm_deliveries WHERE delivery_id=?", (delivery_id,))
            ).fetchone()
            if row is None or not row["blocked_reason"] or row["consumed_node_id"] is not None:
                raise ValueError("Delivery is not a resumable blocked input")
            await self._validate_session(db, row["recipient_id"], row["target_session_id"])
            await db.execute(
                "UPDATE mm_deliveries SET state='pending', blocked_reason=NULL, error=NULL "
                "WHERE delivery_id=?",
                (delivery_id,),
            )

    async def prepare_outbound(self, submission: MessageSubmission) -> OutboundPreparation:
        """只读解析会话路由并校验已有受理，返回可用于迭代提交的草稿。"""
        async with self.lock:
            db = self.require_connection()
            source, routes, existing_id = await self._resolve_sessions(
                db,
                submission.sender_id,
                submission.recipient_ids,
                submission.idempotency_key,
                submission.sender_session_id,
                submission.target_sessions,
                session_routing=submission.session_routing,
            )
            if existing_id is not None:
                existing = await self._read_message(db, existing_id)
                if existing.metadata != dict(submission.metadata or {}):
                    raise ValueError("Idempotency key conflicts with accepted metadata")
                if (existing.recipient_ids, existing.content, existing.kind) != (
                    submission.recipient_ids,
                    submission.content,
                    submission.kind,
                ):
                    raise ValueError("Idempotency key conflicts with an accepted payload")
        return OutboundPreparation(
            draft=PreparedMessage(
                existing_id or uuid4().hex,
                submission.sender_id,
                submission.recipient_ids,
                submission.content,
                submission.kind,
                submission.idempotency_key,
                source,
                tuple(sorted(routes.items())),
                dict(submission.metadata or {}),
            ),
            already_accepted=existing_id is not None,
        )

    async def accept_message(self, submission: MessageSubmission) -> MessageRecord:
        """在同一事务冻结路由、受理消息并绑定符合条件的输入。"""
        sender_id = submission.sender_id
        targets = submission.recipient_ids
        idempotency_key = submission.idempotency_key
        target_sessions = submission.target_sessions
        delivery_policy = submission.delivery_policy
        await_reply = submission.await_reply
        async with self.transaction() as db:
            if submission.external_sender:
                source_session = None
                existing = await (
                    await db.execute(
                        "SELECT message_id FROM mm_messages "
                        "WHERE sender_id=? AND idempotency_key=?",
                        (sender_id, idempotency_key),
                    )
                ).fetchone()
                routes = {}
                if existing:
                    rows = await (
                        await db.execute(
                            "SELECT recipient_id, target_session_id FROM mm_deliveries "
                            "WHERE message_id=?",
                            (existing[0],),
                        )
                    ).fetchall()
                    routes = {row[0]: row[1] for row in rows}
                else:
                    for target in targets:
                        session = (target_sessions or {}).get(
                            target
                        ) or await self._default_session(db, target)
                        await self._validate_session(
                            db,
                            target,
                            session,
                            allow_unavailable=delivery_policy == "inject" and not await_reply,
                        )
                        routes[target] = session
            else:
                source_session, routes, _ = await self._resolve_sessions(
                    db,
                    sender_id,
                    targets,
                    idempotency_key,
                    submission.sender_session_id,
                    target_sessions,
                    session_routing=submission.session_routing,
                    allow_unavailable=delivery_policy == "inject" and not await_reply,
                )
            message_id = await self.insert_message(
                db,
                sender_id,
                targets,
                submission.content,
                submission.kind,
                idempotency_key,
                None,
                sender_session_id=source_session,
                target_sessions=routes,
                metadata=submission.metadata,
                external_sender=submission.external_sender,
                delivery_policy=delivery_policy,
                await_reply=await_reply,
            )
            accepted = await self._read_message(db, message_id)
            with_turns = delivery_policy == "inject" and await has_turns(db)
            binding = (
                "d.delivery_policy='inject' AND t.turn_id IS NOT NULL "
                "AND t.opener_delivery_id IS NOT d.delivery_id"
                if with_turns
                else "0"
            )
            rows = await db.execute_fetchall(
                "SELECT d.*, r.delivery_id AS result_id, r.result_node_id, "
                "r.delivery_id IS NOT NULL AS result_ready, "
                "CASE WHEN d.state='pending' AND d.turn_id IS NULL THEN ("
                "SELECT COUNT(*) FROM mm_deliveries q WHERE q.recipient_id=d.recipient_id "
                "AND q.target_session_id IS d.target_session_id AND q.seq<=d.seq "
                f"AND {QUEUED_SQL}) ELSE 0 END AS queue_position, "
                f"({binding}) AS policy_applied "
                "FROM mm_deliveries d LEFT JOIN mm_delivery_results r "
                "ON r.delivery_id=d.delivery_id "
                + ("LEFT JOIN turns t ON t.turn_id=d.turn_id " if with_turns else "")
                + "WHERE d.message_id=? ORDER BY d.seq",
                (message_id,),
            )
            snapshots = tuple(
                replace(
                    self.delivery_record(row),
                    display_state=display_state(dict(row)),
                    queue_position=row["queue_position"],
                    policy_applied=bool(row["policy_applied"]),
                )
                for row in rows
            )
            accepted = replace(accepted, deliveries=snapshots)
        return accepted

    async def _default_session(self, db: aiosqlite.Connection, agent_id: str) -> str:
        exists = await (
            await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chain_meta'")
        ).fetchone()
        row = (
            await (
                await db.execute(
                    "SELECT active_session_id FROM chain_meta WHERE agent_name=?", (agent_id,)
                )
            ).fetchone()
            if exists
            else None
        )
        if row is None:
            raise ValueError(f"Persisted default session required for agent: {agent_id}")
        return str(row[0])

    async def _resolve_sessions(
        self,
        db: aiosqlite.Connection,
        sender: str,
        targets: tuple[str, ...],
        key: str,
        sender_session: str | None,
        target_sessions: Mapping[str, str] | None,
        *,
        session_routing: bool,
        allow_unavailable: bool = False,
    ) -> tuple[str | None, dict[str, str], str | None]:
        supplied = dict(target_sessions or {})
        if set(supplied) - set(targets):
            raise ValueError("target_sessions contains a non-recipient")
        existing = await (
            await db.execute(
                "SELECT message_id, sender_session_id FROM mm_messages "
                "WHERE sender_id=? AND idempotency_key=?",
                (sender, key),
            )
        ).fetchone()
        if existing is not None:
            rows = await (
                await db.execute(
                    "SELECT recipient_id, target_session_id FROM mm_deliveries WHERE message_id=?",
                    (existing[0],),
                )
            ).fetchall()
            frozen = {row[0]: row[1] for row in rows if row[1] is not None}
            if sender_session is not None and sender_session != existing[1]:
                raise ValueError("Idempotency key conflicts with sender session")
            if any(frozen.get(target) != session for target, session in supplied.items()):
                raise ValueError("Idempotency key conflicts with target session")
            return existing[1], frozen, existing[0]
        if not session_routing and sender_session is None and target_sessions is None:
            return None, {}, None
        source = sender_session or await self._default_session(db, sender)
        await self._validate_session(db, sender, source)
        routes = {}
        for target in targets:
            session = supplied.get(target) or await self._default_session(db, target)
            await self._validate_session(db, target, session, allow_unavailable=allow_unavailable)
            routes[target] = session
        return source, routes, None

    @staticmethod
    def delivery_record(row: aiosqlite.Row) -> DeliveryRecord:
        return DeliveryRecord(
            row["delivery_id"],
            row["message_id"],
            row["recipient_id"],
            DeliveryState.BLOCKED if row["blocked_reason"] else DeliveryState(row["state"]),
            row["attempts"],
            row["error"],
            row["consumed_node_id"],
            row["consumed_session_id"],
            row["result_id"] is not None,
            row["result_node_id"],
            row["target_session_id"],
            row["blocked_reason"],
            row["last_node_id"],
            bool(row["resume_requested"]),
            row["turn_id"],
            row["delivery_policy"],
            bool(row["parked"]),
            row["parked_reason"],
            row["queue_block_reason"] if "queue_block_reason" in row.keys() else None,
        )

    async def insert_message(
        self,
        db: aiosqlite.Connection,
        sender: str,
        recipients: tuple[str, ...],
        content: str,
        kind: MessageKind,
        key: str,
        reply_to: str | None,
        message_id: str | None = None,
        origin_node_id: str | None = None,
        sender_session_id: str | None = None,
        target_sessions: Mapping[str, str] | None = None,
        metadata: Mapping[str, object] | None = None,
        external_sender: bool = False,
        delivery_policy: Literal["inject", "queue"] = "queue",
        await_reply: bool = True,
    ) -> str:
        routes = dict(target_sessions or {})
        parts = [list(recipients), content, kind.value, reply_to]
        if sender_session_id is not None or routes:
            parts.extend([sender_session_id, sorted(routes.items())])
        if metadata or external_sender:
            parts.extend([dict(metadata or {}), external_sender])
        if delivery_policy != "queue" or not await_reply:
            parts.append({"delivery_policy": delivery_policy, "await_reply": await_reply})
        payload = json.dumps(parts, ensure_ascii=False)
        row = await (
            await db.execute(
                "SELECT message_id, payload FROM mm_messages "
                "WHERE sender_id=? AND idempotency_key=?",
                (sender, key),
            )
        ).fetchone()
        if row is not None:
            if row["payload"] != payload:
                raise ValueError("Idempotency key conflicts with an existing payload")
            return str(row["message_id"])
        message_id = message_id or uuid4().hex
        await db.execute(
            "INSERT INTO mm_messages "
            "(message_id, sender_id, content, kind, reply_to, "
            "idempotency_key, payload, accepted_at, origin_node_id, sender_session_id, "
            "metadata, external_sender) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                sender,
                content,
                kind.value,
                reply_to,
                key,
                payload,
                time.time(),
                origin_node_id,
                sender_session_id,
                json.dumps(dict(metadata or {}), ensure_ascii=False),
                int(external_sender),
            ),
        )
        for target in recipients:
            session = routes.get(target)
            blocked = None
            if session is not None and await self._session_status(db, target, session) != "open":
                blocked = "target_session_unavailable"
            delivery_id = uuid4().hex
            await db.execute(
                "INSERT INTO mm_deliveries "
                "(delivery_id, message_id, recipient_id, state, target_session_id, blocked_reason, "
                "delivery_policy) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                (delivery_id, message_id, target, session, blocked, delivery_policy),
            )
            if (
                delivery_policy == "inject"
                and not await_reply
                and kind == MessageKind.REQUEST
                and reply_to is None
                and (metadata or {}).get("message_type")
                not in ("tool_result", "result", "error", "broadcast")
                and not (metadata or {}).get("inline_reply")
                and not (metadata or {}).get("return_reply")
                and not (metadata or {}).get("execute_ability")
                and blocked is None
                and session is not None
                and await has_turns(db)
            ):
                await db.execute(
                    "UPDATE mm_deliveries SET turn_id=(SELECT turn_id FROM turns "
                    "WHERE agent_name=? AND session_id=? AND status IN ('open','waiting_hitl')) "
                    "WHERE delivery_id=? AND state='pending' AND turn_id IS NULL "
                    "AND consumed_node_id IS NULL AND blocked_reason IS NULL AND parked=0",
                    (target, session, delivery_id),
                )
        if (metadata or {}).get("room_projection"):
            await db.execute(
                "INSERT OR IGNORE INTO mm_room_projections(message_id) VALUES (?)", (message_id,)
            )
        return message_id

    async def _has_turn_schema(self, db: aiosqlite.Connection) -> bool:
        """判断链库是否已完成轮次与审批迁移。"""
        rows = await (
            await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('turns', 'hitl_requests')"
            )
        ).fetchall()
        return len(rows) == 2

    async def dispatch_turn_schema(self, db: aiosqlite.Connection, *, require_turns: bool) -> bool:
        if not require_turns:
            return await has_turns(db)
        if not await self._has_turn_schema(db):
            raise RuntimeError("Durable turn schema is required for atomic delivery")
        return True

    @staticmethod
    def _next_delivery_sql(with_turns: bool = True) -> str:
        reason = blocker_reason_sql(with_turns)
        return (
            "WITH queue_heads AS (SELECT MIN(d.seq) AS seq FROM mm_deliveries d "
            "WHERE d.recipient_id=? "
            "AND d.state='pending' AND ((d.turn_id IS NULL AND d.consumed_node_id IS NULL) "
            "OR (d.resume_requested=1 AND d.consumed_node_id IS NOT NULL)) "
            "AND d.blocked_reason IS NULL AND d.parked=0 GROUP BY d.target_session_id) "
            "SELECT d.* FROM queue_heads h JOIN mm_deliveries d ON d.seq=h.seq "
            "WHERE NOT EXISTS (SELECT 1 FROM mm_messages reply JOIN mm_inline_waits w "
            "ON w.message_id=reply.reply_to WHERE reply.message_id=d.message_id "
            "AND w.state='waiting') AND NOT EXISTS (SELECT 1 FROM mm_deliveries prior "
            + blocker_joins(with_turns)
            + "WHERE prior.recipient_id=d.recipient_id AND prior.seq<d.seq "
            "AND prior.target_session_id IS d.target_session_id AND prior.parked=0 "
            f"AND ({reason}) IS NOT NULL) ORDER BY d.seq LIMIT 1"
        )

    @staticmethod
    def _checkpoint_inline_requests(checkpoint: aiosqlite.Row) -> list[str]:
        if not json.loads(checkpoint["metadata"] or "{}").get("pending_tool_replies"):
            return []
        results = deserialize_action_results(json.loads(checkpoint["action_results"])) or []
        return list(
            {
                pending["message_id"]
                for entry in results
                if (result := entry["action_result"]) is not None
                and (pending := result.data.get("await_reply")) is not None
            }
        )

    async def _resume_checkpoint(
        self, db: aiosqlite.Connection, delivery: aiosqlite.Row
    ) -> tuple[aiosqlite.Row | None, str | None]:
        if delivery["consumed_node_id"] is None:
            return None, "input_not_consumed"
        if delivery["last_node_id"] is None:
            return None, "last_checkpoint_unknown"
        node = await (
            await db.execute(
                "SELECT n.*, b.head_node_id, b.lifecycle AS branch_lifecycle, "
                "s.lifecycle AS session_lifecycle, s.active_branch_id "
                "FROM nodes n JOIN branches b ON b.branch_id=n.created_on_branch_id "
                "AND b.session_id=n.session_id JOIN sessions s ON s.session_id=n.session_id "
                "WHERE n.id=?",
                (delivery["last_node_id"],),
            )
        ).fetchone()
        if node is None or (node["agent_name"], node["session_id"]) != (
            delivery["recipient_id"],
            delivery["consumed_session_id"],
        ):
            return node, "checkpoint_missing_or_foreign"
        if node["session_lifecycle"] != "open" or node["branch_lifecycle"] != "open":
            return node, "session_or_branch_closed"
        if (
            node["head_node_id"] != node["id"]
            or node["active_branch_id"] != node["created_on_branch_id"]
        ):
            return node, "session_head_changed"
        if request_ids := self._checkpoint_inline_requests(node):
            async with db.execute(
                "SELECT COUNT(*) FROM mm_inline_waits w WHERE w.parent_delivery_id=? "
                "AND w.message_id IN (SELECT value FROM json_each(?)) "
                "AND w.state IN ('waiting','released') AND NOT EXISTS ("
                "SELECT 1 FROM mm_messages m JOIN mm_deliveries d ON d.message_id=m.message_id "
                "WHERE m.reply_to=w.message_id AND d.recipient_id=? AND ("
                "d.state!='pending' OR d.turn_id IS NOT NULL OR d.consumed_node_id IS NOT NULL "
                "OR EXISTS (SELECT 1 FROM mm_delivery_results r "
                "WHERE r.delivery_id=d.delivery_id)))",
                (delivery["delivery_id"], json.dumps(request_ids), delivery["recipient_id"]),
            ) as cursor:
                available = await cursor.fetchone()
            assert available is not None
            if available[0] != len(request_ids):
                return node, "inline_reply_unavailable"
        return node, None

    async def _read_message(self, db: aiosqlite.Connection, message_id: str) -> MessageRecord:
        row = await (
            await db.execute("SELECT * FROM mm_messages WHERE message_id=?", (message_id,))
        ).fetchone()
        if row is None:
            raise KeyError(message_id)
        recipients = await (
            await db.execute(
                "SELECT recipient_id FROM mm_deliveries WHERE message_id=? ORDER BY recipient_id",
                (message_id,),
            )
        ).fetchall()
        return MessageRecord(
            message_id,
            row["sender_id"],
            tuple(item[0] for item in recipients),
            row["content"],
            MessageKind(row["kind"]),
            row["reply_to"],
            row["accepted_at"],
            row["origin_node_id"],
            row["sender_session_id"],
            json.loads(row["metadata"]),
            bool(row["external_sender"]),
        )

    async def _session_status(
        self, db: aiosqlite.Connection, agent_id: str, session_id: str
    ) -> str | None:
        exists = await (
            await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'")
        ).fetchone()
        if exists is None:
            return None
        row = await (
            await db.execute(
                "SELECT lifecycle FROM sessions WHERE agent_name=? AND session_id=?",
                (agent_id, session_id),
            )
        ).fetchone()
        return row[0] if row else None

    async def _validate_session(
        self,
        db: aiosqlite.Connection,
        agent_id: str,
        session_id: str | None,
        *,
        allow_unavailable: bool = False,
    ) -> None:
        status = await self._session_status(db, agent_id, session_id) if session_id else None
        if status is None or (status != "open" and not allow_unavailable):
            raise ValueError(f"Session is missing, not open or owned by another agent: {agent_id}")
