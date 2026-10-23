# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""显式装配的本地持久消息服务；支持 Supervisor 的原子发送与接收。

默认回调模式分别保存消息和执行历史；bind_atomic 显式启用节点/消费/出站同事务。
最终结果先保存为收尾意图，再原子生成回复和完成状态；可在重启后恢复收尾。
已消费但没有持久最终结果的中断输入禁止重新注入；不保证工具只执行一次。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import aiosqlite

from ghrah.agents.runner import detached_runner_context
from ghrah.context.iteration_commit import clear_durable_iteration_scope
from ghrah.context.manager import DeliverySessionUnavailableError
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.message import AgentMessage, MessageType

if TYPE_CHECKING:
    from ghrah.agents.base import ActorAgent
    from ghrah.context.manager import ContextManager

logger = logging.getLogger(__name__)
Receiver = Callable[[AgentMessage], Awaitable[AgentMessage]]


class MessageKind(StrEnum):
    """请求自动产生一次最终回复；通知和回复不自动产生新回复。"""

    REQUEST = "request"
    NOTIFICATION = "notification"
    RESPONSE = "response"


class DeliveryState(StrEnum):
    """投递状态；完成表示接收回调正常返回并持久保存结果。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MessageRecord:
    """不可变消息及其持久接收者集合。"""

    message_id: str
    project_id: str
    sender_id: str
    recipient_ids: tuple[str, ...]
    content: str
    kind: MessageKind
    reply_to: str | None
    accepted_at: float
    origin_node_id: str | None = None
    sender_session_id: str | None = None
    metadata: dict = field(default_factory=dict)
    external_sender: bool = False


@dataclass(frozen=True)
class DeliveryRecord:
    """单个接收者的投递状态和失败信息。"""

    delivery_id: str
    message_id: str
    recipient_id: str
    state: DeliveryState
    attempts: int
    error: str | None
    consumed_node_id: str | None = None
    consumed_session_id: str | None = None
    result_ready: bool = False
    result_node_id: str | None = None
    target_session_id: str | None = None
    blocked_reason: str | None = None
    last_node_id: str | None = None
    resume_requested: bool = False


@dataclass(frozen=True)
class DeliveryProgress:
    """持久中断进度；恢复阻塞原因来自当前 Core checkpoint 校验。"""

    delivery: DeliveryRecord
    branch_id: str | None
    iteration: int | None
    resume_block_reason: str | None


@dataclass(frozen=True)
class PreparedMessage:
    """本轮待提交出站消息；此对象不表示已持久受理。"""

    message_id: str
    sender_id: str
    recipient_ids: tuple[str, ...]
    content: str
    kind: MessageKind
    idempotency_key: str
    sender_session_id: str | None = None
    target_sessions: tuple[tuple[str, str], ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass
class _AtomicDelivery:
    manager: MessageManager
    backend: SqliteBackend
    delivery_id: str
    agent_id: str
    drafts: list[PreparedMessage]
    context_manager: ContextManager
    active: bool = True
    consumed: bool = False
    committing: bool = False
    last_node_id: str | None = None
    inline_updates: dict[str, bool] = field(default_factory=dict)


_ATOMIC: ContextVar[_AtomicDelivery | None] = ContextVar("ghrah_atomic_delivery", default=None)


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
    UNIQUE (message_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS mm_inbox ON mm_deliveries(recipient_id, seq);
CREATE INDEX IF NOT EXISTS mm_replies ON mm_messages(reply_to, seq);
CREATE TABLE IF NOT EXISTS mm_room_projections (
    message_id TEXT PRIMARY KEY REFERENCES mm_messages(message_id),
    completed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS mm_inline_waits (
    message_id TEXT PRIMARY KEY REFERENCES mm_messages(message_id),
    parent_delivery_id TEXT NOT NULL REFERENCES mm_deliveries(delivery_id),
    state TEXT NOT NULL CHECK(state IN ('waiting','consumed','released'))
);
CREATE TABLE IF NOT EXISTS mm_delivery_results (
    delivery_id TEXT PRIMARY KEY REFERENCES mm_deliveries(delivery_id),
    content TEXT NOT NULL,
    result_node_id TEXT,
    recorded_at REAL NOT NULL,
    content_blocks TEXT
);
"""


class MessageManager:
    """单项目、单本地进程的持久消息管理器。

    agent_ids 为部署方显式提供的稳定身份白名单，包含离线 agent。
    bind 只登记运行回调，不创建 agent，也不复制其上下文。受管回调应只由本服务驱动。
    独立 SQLite 锁连接确保一个数据库只有一个活跃管理器，强杀后锁由操作系统释放。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        project_id: str,
        agent_ids: Collection[str],
        poll_interval: float = 0.1,
        session_routing: bool = False,
        allow_persisted_agents: bool = False,
    ) -> None:
        if (
            not project_id.strip()
            or isinstance(agent_ids, str)
            or (not agent_ids and not allow_persisted_agents)
            or any(not item.strip() for item in agent_ids)
        ):
            raise ValueError("project_id and explicit non-empty agent_ids are required")
        if not 0 < poll_interval < float("inf"):
            raise ValueError("poll_interval must be positive and finite")
        self._path = Path(db_path).resolve()
        self._project_id = project_id
        self._agent_ids = frozenset(agent_ids)
        self._poll_interval = poll_interval
        self._session_routing = session_routing
        self._allow_persisted_agents = allow_persisted_agents
        self._wait_edges: dict[str, set[str]] = {}
        self._room_projector: Callable[[MessageRecord], Awaitable[None]] | None = None
        self._projection_lock = asyncio.Lock()
        self._session_receivers: set[str] = set()
        self._db: aiosqlite.Connection | None = None
        self._owner: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._receivers: dict[str, Receiver] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._dispatcher: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = False

    @property
    def db_path(self) -> Path:
        """返回与 Core 原子提交共用的数据库路径。"""
        return self._path

    @property
    def project_id(self) -> str:
        """返回持久消息所属项目。"""
        return self._project_id

    @property
    def agent_ids(self) -> frozenset[str]:
        """返回部署方声明的稳定身份集合，含离线目标。"""
        return self._agent_ids

    @property
    def session_routing(self) -> bool:
        """返回是否已显式启用固定会话路由。"""
        return self._session_routing

    async def __aenter__(self) -> MessageManager:
        """显式打开持久存储和调度器。"""
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        """关闭服务，保留尚未完成的消息。"""
        await self.close()

    async def open(self) -> None:
        """打开服务；上次进程未完成的投递转为中断，禁止隐式重跑。"""
        if self._db is not None:
            raise RuntimeError("MessageManager is already open")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        owner = sqlite3.connect(str(self._path) + ".owner", timeout=0, isolation_level=None)
        try:
            owner.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            owner.close()
            raise RuntimeError("Message database already has an active manager") from exc
        self._owner = owner
        self._closing = False
        try:
            self._db = await aiosqlite.connect(self._path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._db.executescript(_SCHEMA)
            async with self._transaction() as db:
                row = await (await db.execute("SELECT * FROM mm_meta")).fetchone()
                if row is None:
                    await db.execute("INSERT INTO mm_meta VALUES (1, ?, 6)", (self._project_id,))
                elif row["project_id"] != self._project_id or row["version"] not in (
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                ):
                    raise ValueError("Message database project/schema mismatch")
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
                await db.execute("UPDATE mm_meta SET version=6 WHERE singleton=1")
                await db.execute(
                    "UPDATE mm_deliveries SET state='interrupted', error=? "
                    "WHERE state='processing'",
                    ("Previous process stopped before durable completion",),
                )
            await self.refresh_agent_ids()
            await self.recover_completions()
            self._dispatcher = asyncio.create_task(
                self._dispatch_loop(), context=self._task_context()
            )
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """停止调度并等待取消收敛；处理中断记录保留到下次显式重试。"""
        self._closing = True
        tasks = [*self._workers.values()]
        if self._dispatcher is not None:
            tasks.append(self._dispatcher)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._receivers.clear()
        self._session_receivers.clear()
        self._dispatcher = None
        async with self._lock:
            if self._db is not None:
                await self._db.close()
                self._db = None
        if self._owner is not None:
            self._owner.close()
            self._owner = None

    def bind(self, agent_id: str, receiver: Receiver, *, _session_aware: bool = False) -> None:
        """将稳定 agent ID 绑定到在线接收回调，并唤醒待收扫描。"""
        self._require_db()
        self._check_identity(agent_id)
        if self._session_routing and not _session_aware:
            raise ValueError("Session routing requires bind_atomic")
        if agent_id in self._receivers:
            raise ValueError(f"Agent already bound: {agent_id}")
        self._receivers[agent_id] = receiver
        if _session_aware:
            self._session_receivers.add(agent_id)
        self._wake.set()

    async def unbind(self, agent_id: str) -> None:
        """停止该 agent 的消费；未开始消息继续离线待收。"""
        self._require_db()
        self._receivers.pop(agent_id, None)
        self._session_receivers.discard(agent_id)
        task = self._workers.get(agent_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._workers.pop(agent_id, None)

    async def bind_atomic(self, agent: ActorAgent) -> None:
        """显式绑定同文件 SQLite 上下文，原子提交消费确认与本轮出站。"""
        agent_id = agent.config.effective_agent_id
        self._require_db()
        self._check_identity(agent_id)
        if agent_id in self._receivers:
            raise ValueError(f"Agent already bound: {agent_id}")
        cm = agent._context_manager
        backend = cm.persistence
        if not isinstance(backend, SqliteBackend) or backend.db_path.resolve() != self._path:
            raise ValueError("Atomic receive requires a same-file SqliteBackend")
        if agent._runner.busy or cm.in_iteration:
            raise RuntimeError("Cannot bind atomic receive while agent is running")
        await cm.persist()

        async def receive(message: AgentMessage) -> AgentMessage:
            scope = _AtomicDelivery(
                self, backend, message.metadata["delivery_id"], agent_id, [], cm
            )
            resume_from = message.metadata.get("resume_from_node_id")
            if resume_from is not None:
                scope.consumed = True
                scope.last_node_id = resume_from
            token = _ATOMIC.set(scope)
            try:

                async def commit(changes: ContextChanges) -> None:
                    await self._commit_atomic(scope, changes)

                async def finish(result: AgentMessage) -> None:
                    await self._finish_atomic(scope, result)

                def rollback() -> None:
                    scope.drafts.clear()
                    scope.inline_updates.clear()

                if resume_from is not None:
                    result = await agent.resume_atomic(
                        message,
                        commit,
                        rollback,
                        finish,
                        session_id=message.metadata["resume_session_id"],
                        branch_id=message.metadata["resume_branch_id"],
                        last_node_id=resume_from,
                    )
                else:
                    result = await agent.receive_atomic(
                        message,
                        commit,
                        rollback,
                        finish,
                        message.metadata.get("target_session_id"),
                    )
                if result.type != MessageType.ERROR and not scope.consumed:
                    raise RuntimeError("Atomic receive finished without consuming the input")
                return result
            finally:
                scope.active = False
                clear = getattr(agent._ability_executor, "clear_deferred", None)
                if callable(clear):
                    clear()
                _ATOMIC.reset(token)

        self.bind(agent_id, receive, _session_aware=True)

    async def prepare_send(
        self,
        *,
        recipient_ids: Collection[str],
        content: str,
        kind: MessageKind,
        idempotency_key: str,
        target_sessions: Mapping[str, str] | None = None,
        expected_sender_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> PreparedMessage:
        """在原子接收本轮中准备出站；持久提交前不会投递，回滚会丢弃。"""
        scope = _ATOMIC.get()
        if (
            scope is None
            or scope.manager is not self
            or not scope.active
            or scope.committing
            or not scope.context_manager.in_iteration
        ):
            raise RuntimeError("prepare_send requires this manager's atomic receive scope")
        if expected_sender_id is not None and expected_sender_id != scope.agent_id:
            raise ValueError("Sender does not match the active atomic delivery")
        if isinstance(recipient_ids, str):
            raise ValueError("recipient_ids must be a collection")
        targets = tuple(sorted(set(recipient_ids)))
        if (
            (not targets and not (metadata or {}).get("room_ids"))
            or not content.strip()
            or not idempotency_key.strip()
        ):
            raise ValueError("recipients, content and idempotency_key are required")
        if idempotency_key.startswith("response:") or MessageKind(kind) == MessageKind.RESPONSE:
            raise ValueError("Response identity is reserved")
        for target in targets:
            self._check_identity(target)
        async with self._lock:
            source_session, routes = await self._resolve_sessions(
                self._require_db(),
                scope.agent_id,
                targets,
                idempotency_key,
                scope.context_manager.active_session_id
                if self._session_routing or target_sessions is not None
                else None,
                target_sessions,
            )
        draft = PreparedMessage(
            uuid4().hex,
            scope.agent_id,
            targets,
            content,
            MessageKind(kind),
            idempotency_key,
            source_session,
            tuple(sorted(routes.items())),
            dict(metadata or {}),
        )
        for previous in scope.drafts:
            if previous.idempotency_key == idempotency_key:
                if previous.metadata != draft.metadata:
                    raise ValueError("Idempotency key conflicts with prepared metadata")
                if (previous.recipient_ids, previous.content, previous.kind) != (
                    targets,
                    content,
                    draft.kind,
                ):
                    raise ValueError("Idempotency key conflicts with a prepared payload")
                if (previous.sender_session_id, previous.target_sessions) != (
                    source_session,
                    tuple(sorted(routes.items())),
                ):
                    raise ValueError("Idempotency key conflicts with prepared sessions")
                return previous
        async with self._lock:
            row = await (
                await self._require_db().execute(
                    "SELECT message_id FROM mm_messages WHERE sender_id=? AND idempotency_key=?",
                    (scope.agent_id, idempotency_key),
                )
            ).fetchone()
            if row is not None:
                existing = await self._read_message(self._require_db(), row[0])
                if existing.metadata != draft.metadata:
                    raise ValueError("Idempotency key conflicts with accepted metadata")
                if (existing.recipient_ids, existing.content, existing.kind) != (
                    targets,
                    content,
                    draft.kind,
                ):
                    raise ValueError("Idempotency key conflicts with an accepted payload")
                return PreparedMessage(
                    existing.message_id,
                    scope.agent_id,
                    targets,
                    content,
                    draft.kind,
                    idempotency_key,
                    source_session,
                    tuple(sorted(routes.items())),
                    dict(metadata or {}),
                )
        scope.drafts.append(draft)
        return draft

    async def send(
        self,
        *,
        sender_id: str,
        recipient_ids: Collection[str],
        content: str,
        kind: MessageKind,
        idempotency_key: str,
        sender_session_id: str | None = None,
        target_sessions: Mapping[str, str] | None = None,
        metadata: Mapping[str, object] | None = None,
        _external_sender: bool = False,
    ) -> MessageRecord:
        """原子受理一对一或一对多消息；同键同载荷返回原消息。"""
        if not _external_sender:
            self._check_identity(sender_id)
        if _ATOMIC.get() is not None:
            raise RuntimeError(
                "Use prepare_send during atomic receive; immediate send is forbidden"
            )
        if isinstance(recipient_ids, str):
            raise ValueError("recipient_ids must be a collection of IDs, not a string")
        targets = tuple(sorted(set(recipient_ids)))
        if (
            (not targets and not (metadata or {}).get("room_ids"))
            or (not content.strip() and not (metadata or {}).get("content_blocks"))
            or not idempotency_key.strip()
        ):
            raise ValueError("recipients, content and idempotency_key are required")
        if idempotency_key.startswith("response:"):
            raise ValueError("response: is a reserved idempotency key prefix")
        for target in targets:
            self._check_identity(target)
        kind = MessageKind(kind)
        if kind == MessageKind.RESPONSE:
            raise ValueError("Responses are generated from completed requests")
        async with self._transaction() as db:
            if _external_sender:
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
                        await self._validate_session(db, target, session)
                        routes[target] = session
            else:
                source_session, routes = await self._resolve_sessions(
                    db, sender_id, targets, idempotency_key, sender_session_id, target_sessions
                )
            message_id = await self._insert_message(
                db,
                sender_id,
                targets,
                content,
                kind,
                idempotency_key,
                None,
                sender_session_id=source_session,
                target_sessions=routes,
                metadata=metadata,
                external_sender=_external_sender,
            )
        self._wake.set()
        return await self.get_message(message_id)

    async def send_external(
        self,
        *,
        sender: str,
        recipient_ids: Collection[str],
        content: str,
        idempotency_key: str,
        metadata: Mapping[str, object] | None = None,
    ) -> MessageRecord:
        """由受信宿主受理外部输入；保留来源，固定目标会话，无需伪造 agent 身份。"""
        if not sender.strip():
            raise ValueError("External sender is required")
        return await self.send(
            sender_id=f"human:{sender}",
            recipient_ids=recipient_ids,
            content=content,
            kind=MessageKind.REQUEST,
            idempotency_key=idempotency_key,
            metadata=metadata,
            _external_sender=True,
        )

    async def wait_replies(
        self, message_id: str, *, timeout: float = -1, tolerate_errors: bool = False
    ) -> tuple[MessageRecord, ...]:
        """等候已受理请求的持久结果；超时不删除消息或取消目标执行。"""

        async def wait() -> tuple[MessageRecord, ...]:
            while True:
                deliveries = await self.get_deliveries(message_id)
                terminal = {
                    DeliveryState.COMPLETED,
                    DeliveryState.FAILED,
                    DeliveryState.INTERRUPTED,
                    DeliveryState.BLOCKED,
                }
                if all(d.state == DeliveryState.COMPLETED for d in deliveries) or (
                    tolerate_errors and all(d.state in terminal for d in deliveries)
                ):
                    return await self.get_replies(message_id)
                bad = next(
                    (
                        d
                        for d in deliveries
                        if d.state
                        in (DeliveryState.FAILED, DeliveryState.INTERRUPTED, DeliveryState.BLOCKED)
                    ),
                    None,
                )
                if bad and not tolerate_errors:
                    raise RuntimeError(
                        f"Delivery {bad.delivery_id}: {bad.state}: "
                        f"{bad.error or bad.blocked_reason}"
                    )
                if not tolerate_errors:
                    for delivery in deliveries:
                        if delivery.state != DeliveryState.PENDING:
                            continue
                        blocker = await self.get_queue_blocker(delivery.delivery_id)
                        if blocker is not None:
                            raise RuntimeError(
                                "message_queue_blocked: "
                                f"recipient={delivery.recipient_id}, "
                                f"session={delivery.target_session_id}, "
                                f"blocking_delivery={blocker.delivery_id}, "
                                f"state={blocker.state}, "
                                f"reason={blocker.error or blocker.blocked_reason}. "
                                "Recover the preceding delivery explicitly or use a new session. "
                                "The pending request is retained."
                            )
                await asyncio.sleep(min(self._poll_interval, 0.05))

        if timeout < 0:
            return await wait()
        return await asyncio.wait_for(wait(), timeout)

    async def wait_inline(
        self, message_id: str, *, timeout: float = -1, tolerate_errors: bool = False
    ) -> tuple[MessageRecord, ...]:
        """等待检查点已提交的同步工具请求，回复消费随后续节点原子确认。"""
        scope = _ATOMIC.get()
        if scope is None or scope.manager is not self or not scope.active:
            raise RuntimeError("Inline wait requires an atomic delivery")
        request = await self.get_message(message_id)
        async with self._lock:
            row = await (
                await self._require_db().execute(
                    "SELECT parent_delivery_id FROM mm_inline_waits WHERE message_id=?",
                    (message_id,),
                )
            ).fetchone()
        if row is None or row[0] != scope.delivery_id:
            raise ValueError("Inline request does not belong to this execution")
        targets = set(request.recipient_ids)

        def reaches(start: str, wanted: str, seen: set[str]) -> bool:
            if start == wanted:
                return True
            if start in seen:
                return False
            seen.add(start)
            return any(reaches(nxt, wanted, seen) for nxt in self._wait_edges.get(start, ()))

        if any(reaches(target, scope.agent_id, set()) for target in targets):
            scope.inline_updates[message_id] = False
            raise RuntimeError("Persistent synchronous wait cycle detected")
        previous = self._wait_edges.get(scope.agent_id, set())
        self._wait_edges[scope.agent_id] = previous | targets
        try:
            replies = await self.wait_replies(
                message_id, timeout=timeout, tolerate_errors=tolerate_errors
            )
            scope.inline_updates[message_id] = True
            return replies
        except Exception:
            scope.inline_updates[message_id] = False
            raise
        finally:
            if previous:
                self._wait_edges[scope.agent_id] = previous
            else:
                self._wait_edges.pop(scope.agent_id, None)

    async def get_message(self, message_id: str) -> MessageRecord:
        """查询持久消息及来源与去向。"""
        async with self._lock:
            return await self._read_message(self._require_db(), message_id)

    async def get_deliveries(self, message_id: str) -> tuple[DeliveryRecord, ...]:
        """查询各接收者的独立状态；未知消息报错。"""
        async with self._lock:
            db = self._require_db()
            await self._read_message(db, message_id)
            rows = await (
                await db.execute(
                    "SELECT d.*, r.delivery_id AS result_id, r.result_node_id "
                    "FROM mm_deliveries d LEFT JOIN mm_delivery_results r "
                    "ON r.delivery_id=d.delivery_id WHERE d.message_id=? ORDER BY d.seq",
                    (message_id,),
                )
            ).fetchall()
        return tuple(self._delivery_record(row) for row in rows)

    async def get_queue_blocker(self, delivery_id: str) -> DeliveryRecord | None:
        """从权威投递表查询同会话中阻塞待收消息的前序失败，不修改队列。"""
        async with self._lock:
            row = await (
                await self._require_db().execute(
                    "SELECT prior.*, r.delivery_id AS result_id, r.result_node_id "
                    "FROM mm_deliveries current JOIN mm_deliveries prior "
                    "ON prior.recipient_id=current.recipient_id "
                    "AND prior.target_session_id IS current.target_session_id "
                    "AND prior.seq<current.seq "
                    "LEFT JOIN mm_delivery_results r ON r.delivery_id=prior.delivery_id "
                    "WHERE current.delivery_id=? AND current.state='pending' "
                    "AND (prior.state IN ('failed', 'interrupted') "
                    "OR (prior.state!='completed' AND prior.blocked_reason IS NOT NULL)) "
                    "ORDER BY prior.seq LIMIT 1",
                    (delivery_id,),
                )
            ).fetchone()
        return self._delivery_record(row) if row is not None else None

    @staticmethod
    def _delivery_record(row: aiosqlite.Row) -> DeliveryRecord:
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
        )

    async def get_replies(self, message_id: str) -> tuple[MessageRecord, ...]:
        """按原请求查询各响应者持久回复，不依赖内存 Future。"""
        async with self._lock:
            db = self._require_db()
            await self._read_message(db, message_id)
            rows = await (
                await db.execute(
                    "SELECT message_id FROM mm_messages WHERE reply_to=? ORDER BY seq",
                    (message_id,),
                )
            ).fetchall()
            return tuple([await self._read_message(db, row[0]) for row in rows])

    async def retry(self, delivery_id: str) -> None:
        """显式重试失败或中断投递；调用方须确认重复副作用风险。"""
        async with self._transaction() as db:
            cursor = await db.execute(
                "UPDATE mm_deliveries SET state='pending', error=NULL "
                "WHERE delivery_id=? AND state IN ('failed', 'interrupted') "
                "AND consumed_node_id IS NULL "
                "AND blocked_reason IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM mm_delivery_results r "
                "WHERE r.delivery_id=mm_deliveries.delivery_id)",
                (delivery_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Delivery is missing or not retryable")
        self._wake.set()

    async def get_progress(self, delivery_id: str) -> DeliveryProgress:
        """查询消费与最后提交节点，说明当前是否可申请续跑。"""
        async with self._lock:
            db = self._require_db()
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
            result = await (
                await db.execute(
                    "SELECT 1 FROM mm_delivery_results WHERE delivery_id=?", (delivery_id,)
                )
            ).fetchone()
            checkpoint, reason = await self._resume_checkpoint(db, row)
        delivery = self._delivery_record(row)
        if result is not None:
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

    async def resume_interrupted(
        self,
        delivery_id: str,
        *,
        expected_last_node_id: str,
        acknowledge_side_effect_risk: bool,
    ) -> None:
        """显式申请从最后持久节点续跑；须确认未提交外部副作用的重复风险。"""
        if acknowledge_side_effect_risk is not True:
            raise ValueError("Explicit acknowledgement of side-effect risk is required")
        async with self._transaction() as db:
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
            _, reason = await self._resume_checkpoint(db, row)
            if reason:
                raise ValueError(f"Cannot resume: {reason}")
            worker = self._workers.get(row["recipient_id"])
            if worker is not None and not worker.done():
                raise ValueError("Recipient still has an active worker")
            await db.execute(
                "UPDATE mm_deliveries SET state='pending', resume_requested=1, error=NULL "
                "WHERE delivery_id=?",
                (delivery_id,),
            )
        self._wake.set()

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
        return node, None

    async def resume_delivery(self, delivery_id: str) -> None:
        """目标会话恢复开放后解除阻塞，保留原归属，不自动改投其他会话。"""
        async with self._transaction() as db:
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
        self._wake.set()

    async def dispatch_pending(self) -> None:
        """处理当前在线 agent 可领取的消息；离线和中断不阻塞调用返回。"""
        self._require_db()
        while True:
            await self._scan()
            tasks = tuple(self._workers.values())
            if not tasks:
                return
            await asyncio.gather(*tasks)

    async def recover_completions(self) -> int:
        """补齐已持久最终结果的收尾，不重新执行 agent；返回补齐数量。"""
        async with self._lock:
            rows = await (
                await self._require_db().execute(
                    "SELECT r.delivery_id FROM mm_delivery_results r "
                    "JOIN mm_deliveries d ON d.delivery_id=r.delivery_id "
                    "WHERE d.state != 'completed' ORDER BY d.seq"
                )
            ).fetchall()
        completed = 0
        for row in rows:
            try:
                completed += int(await self._finish_result(row[0]))
            except Exception as exc:
                await self._record_failure(row[0], DeliveryState.FAILED, str(exc))
                logger.warning("Completion recovery failed: %s", row[0], exc_info=True)
        if completed:
            self._wake.set()
        return completed

    @property
    def allow_persisted_agents(self) -> bool:
        """返回部署方是否允许由 Core 已持久身份更新接收白名单。"""
        return self._allow_persisted_agents

    def configure_room_projector(
        self, projector: Callable[[MessageRecord], Awaitable[None]]
    ) -> None:
        """显式安装单向 Room 日志投影器；持久消息正文仍只由 MM 写入。"""
        if self._room_projector is None:
            self._room_projector = projector
        self._wake.set()

    async def recover_room_projections(self) -> int:
        """重放未确认的 Room 投影；接收端按消息引用幂等，不重新执行 agent。"""
        if self._room_projector is None:
            return 0
        async with self._projection_lock:
            async with self._lock:
                rows = await (
                    await self._require_db().execute(
                        "SELECT message_id FROM mm_room_projections "
                        "WHERE completed=0 ORDER BY rowid"
                    )
                ).fetchall()
            completed = 0
            for row in rows:
                try:
                    await self._room_projector(await self.get_message(row[0]))
                    async with self._transaction() as db:
                        await db.execute(
                            "UPDATE mm_room_projections SET completed=1, error=NULL "
                            "WHERE message_id=?",
                            (row[0],),
                        )
                    completed += 1
                except Exception as exc:
                    async with self._transaction() as db:
                        await db.execute(
                            "UPDATE mm_room_projections SET error=? WHERE message_id=?",
                            (str(exc), row[0]),
                        )
                    logger.warning("Room projection remains pending: %s: %s", row[0], exc)
            return completed

    async def refresh_agent_ids(self) -> None:
        """从 Core sessions 重建身份校验缓存，不独立写入身份事实。"""
        if not self._allow_persisted_agents:
            return
        async with self._lock:
            db = self._require_db()
            exists = await (
                await db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
                )
            ).fetchone()
            if exists:
                rows = await (
                    await db.execute("SELECT DISTINCT agent_name FROM sessions")
                ).fetchall()
                self._agent_ids = frozenset(row[0] for row in rows)

    def _check_identity(self, agent_id: str) -> None:
        if agent_id not in self._agent_ids:
            raise ValueError(f"Unknown agent ID: {agent_id}")

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None or self._closing:
            raise RuntimeError("MessageManager is not open")
        return self._db

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            db = self._require_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _insert_message(
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
    ) -> str:
        routes = dict(target_sessions or {})
        parts = [list(recipients), content, kind.value, reply_to]
        if sender_session_id is not None or routes:
            parts.extend([sender_session_id, sorted(routes.items())])
        if metadata or external_sender:
            parts.extend([dict(metadata or {}), external_sender])
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
            await db.execute(
                "INSERT INTO mm_deliveries "
                "(delivery_id, message_id, recipient_id, state, target_session_id, blocked_reason) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (uuid4().hex, message_id, target, session, blocked),
            )
        if (metadata or {}).get("room_projection"):
            await db.execute(
                "INSERT OR IGNORE INTO mm_room_projections(message_id) VALUES (?)", (message_id,)
            )
        return message_id

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
            self._project_id,
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
        self, db: aiosqlite.Connection, agent_id: str, session_id: str | None
    ) -> None:
        if session_id is None or await self._session_status(db, agent_id, session_id) != "open":
            raise ValueError(f"Session is missing, not open or owned by another agent: {agent_id}")

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
    ) -> tuple[str | None, dict[str, str]]:
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
            return existing[1], frozen
        if not self._session_routing and sender_session is None and target_sessions is None:
            return None, {}
        source = sender_session or await self._default_session(db, sender)
        await self._validate_session(db, sender, source)
        routes = {}
        for target in targets:
            session = supplied.get(target) or await self._default_session(db, target)
            await self._validate_session(db, target, session)
            routes[target] = session
        return source, routes

    @staticmethod
    def _next_delivery_sql() -> str:
        return (
            "SELECT d.* FROM mm_deliveries d WHERE d.recipient_id=? "
            "AND d.state='pending' AND d.blocked_reason IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM mm_messages reply JOIN mm_inline_waits w "
            "ON w.message_id=reply.reply_to WHERE reply.message_id=d.message_id "
            "AND w.state='waiting') "
            "AND NOT EXISTS (SELECT 1 FROM mm_deliveries prior "
            "WHERE prior.recipient_id=d.recipient_id AND prior.seq<d.seq "
            "AND prior.state!='completed' AND prior.target_session_id IS d.target_session_id) "
            "ORDER BY d.seq LIMIT 1"
        )

    async def _commit_atomic(self, scope: _AtomicDelivery, changes: ContextChanges) -> None:
        scope.committing = True
        try:
            await self._apply_atomic(scope, changes)
        finally:
            scope.committing = False

    async def _apply_atomic(self, scope: _AtomicDelivery, changes: ContextChanges) -> None:
        if changes.agent_name != scope.agent_id or len(changes.nodes) != 1:
            raise ValueError("Atomic iteration must contain one node owned by the recipient")
        node = changes.nodes[0]
        async with self._transaction() as db:
            delivery = await (
                await db.execute(
                    "SELECT * FROM mm_deliveries WHERE delivery_id=?", (scope.delivery_id,)
                )
            ).fetchone()
            if delivery is None or delivery["state"] != DeliveryState.PROCESSING.value:
                raise RuntimeError("Delivery is no longer processing")
            if delivery["target_session_id"] is not None:
                if node.session_id != delivery["target_session_id"]:
                    raise RuntimeError("Atomic node does not match frozen delivery session")
                await self._validate_session(db, scope.agent_id, node.session_id)
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
            if compact and (
                node.messages_delta or node.ability_names != ["compact"] or scope.drafts
            ):
                raise ValueError(
                    "Compact commit must not consume input or publish outbound messages"
                )
            await scope.backend.apply_changes_in_transaction(db, changes)
            if delivery["consumed_node_id"] is None and not compact:
                if not any(
                    message.metadata.get("delivery_id") == scope.delivery_id
                    for message in node.messages_delta
                ):
                    raise RuntimeError("First atomic iteration must include the input delivery")
                await db.execute(
                    "UPDATE mm_deliveries SET consumed_node_id=?, consumed_session_id=? "
                    "WHERE delivery_id=?",
                    (node.id, node.session_id, scope.delivery_id),
                )
            for draft in scope.drafts:
                await self._insert_message(
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
                        (draft.message_id, scope.delivery_id),
                    )
            for request_id, consumed in scope.inline_updates.items():
                await db.execute(
                    "UPDATE mm_inline_waits SET state=? "
                    "WHERE message_id=? AND parent_delivery_id=?",
                    ("consumed" if consumed else "released", request_id, scope.delivery_id),
                )
                if consumed:
                    await db.execute(
                        "UPDATE mm_deliveries SET state='completed', consumed_node_id=?, "
                        "consumed_session_id=?, last_node_id=? WHERE message_id IN "
                        "(SELECT message_id FROM mm_messages WHERE reply_to=?) AND recipient_id=?",
                        (node.id, node.session_id, node.id, request_id, scope.agent_id),
                    )
            await db.execute(
                "UPDATE mm_deliveries SET last_node_id=? WHERE delivery_id=?",
                (node.id, scope.delivery_id),
            )
        scope.drafts.clear()
        scope.inline_updates.clear()
        scope.consumed = delivery["consumed_node_id"] is not None or not compact
        scope.last_node_id = node.id
        self._wake.set()

    @staticmethod
    def _task_context() -> Context:
        context = detached_runner_context()
        context.run(_ATOMIC.set, None)
        context.run(clear_durable_iteration_scope)
        return context

    async def _dispatch_loop(self) -> None:
        while not self._closing:
            self._wake.clear()
            try:
                await self._scan()
            except Exception:
                logger.exception("MessageManager pending scan failed")
            try:
                await asyncio.wait_for(self._wake.wait(), self._poll_interval)
            except TimeoutError:
                pass

    async def _scan(self) -> None:
        async with self._scan_lock:
            await self.recover_completions()
            await self.recover_room_projections()
            for agent_id, receiver in tuple(self._receivers.items()):
                if agent_id in self._workers:
                    continue
                async with self._lock:
                    row = await (
                        await self._require_db().execute(
                            self._next_delivery_sql(),
                            (agent_id,),
                        )
                    ).fetchone()
                if (
                    row is not None
                    and row["state"] == DeliveryState.PENDING.value
                    and self._receivers.get(agent_id) is receiver
                ):
                    self._workers[agent_id] = asyncio.create_task(
                        self._run(agent_id, receiver), context=self._task_context()
                    )

    async def _run(self, agent_id: str, receiver: Receiver) -> None:
        delivery_id: str | None = None
        try:
            async with self._transaction() as db:
                row = await (
                    await db.execute(
                        self._next_delivery_sql(),
                        (agent_id,),
                    )
                ).fetchone()
                if row is None or row["state"] != DeliveryState.PENDING.value:
                    return
                delivery_id = row["delivery_id"]
                record = await self._read_message(db, row["message_id"])
                target_session = row["target_session_id"]
                resume_node = None
                if row["resume_requested"]:
                    resume_node, reason = await self._resume_checkpoint(db, row)
                    if reason or agent_id not in self._session_receivers:
                        await db.execute(
                            "UPDATE mm_deliveries SET state='interrupted', "
                            "resume_requested=0, error=? "
                            "WHERE delivery_id=?",
                            (reason or "atomic_receiver_required", delivery_id),
                        )
                        return
                    target_session = resume_node["session_id"]
                if target_session is None and self._session_routing:
                    await db.execute(
                        "UPDATE mm_deliveries SET blocked_reason='legacy_session_unassigned' "
                        "WHERE delivery_id=?",
                        (delivery_id,),
                    )
                    return
                if target_session is not None:
                    reason = None
                    if agent_id not in self._session_receivers:
                        reason = "receiver_cannot_route_session"
                    elif await self._session_status(db, agent_id, target_session) != "open":
                        reason = "target_session_unavailable"
                    if reason:
                        await db.execute(
                            "UPDATE mm_deliveries SET blocked_reason=? WHERE delivery_id=?",
                            (reason, delivery_id),
                        )
                        return
                await db.execute(
                    "UPDATE mm_deliveries SET state='processing', "
                    "attempts=attempts+1, resume_requested=0 "
                    "WHERE delivery_id=?",
                    (delivery_id,),
                )
            message = AgentMessage(
                id=record.message_id,
                sender=record.sender_id.removeprefix("human:")
                if record.external_sender
                else record.sender_id,
                recipient=agent_id,
                content=record.content,
                reply_to=record.reply_to,
                type=MessageType(record.metadata.get("message_type", "chat")),
                content_blocks=record.metadata.get("content_blocks"),
                metadata={
                    **record.metadata,
                    "external_sender": record.external_sender,
                    "message_manager": True,
                    "project_id": self._project_id,
                    "agent_id": agent_id,
                    "message_id": record.message_id,
                    "delivery_id": delivery_id,
                    "message_kind": record.kind.value,
                    "reply_to": record.reply_to,
                    "sender_session_id": record.sender_session_id,
                    "target_session_id": target_session,
                    "resume_from_node_id": resume_node["id"] if resume_node else None,
                    "resume_session_id": resume_node["session_id"] if resume_node else None,
                    "resume_branch_id": resume_node["created_on_branch_id"]
                    if resume_node
                    else None,
                },
            )
            result = await receiver(message)
            if not isinstance(result, AgentMessage):
                raise TypeError("Receiver must return AgentMessage")
            if result.type == MessageType.ERROR:
                raise RuntimeError(result.content)
            await self._complete_delivery(delivery_id, result)
        except asyncio.CancelledError:
            if delivery_id is not None:
                await self._record_failure(delivery_id, DeliveryState.INTERRUPTED, "Worker stopped")
            raise
        except Exception as exc:
            if delivery_id is not None:
                if isinstance(exc, DeliverySessionUnavailableError):
                    async with self._transaction() as db:
                        cursor = await db.execute(
                            "UPDATE mm_deliveries SET state='pending', "
                            "blocked_reason='target_session_unavailable', error=? "
                            "WHERE delivery_id=? AND consumed_node_id IS NULL",
                            (str(exc), delivery_id),
                        )
                    if cursor.rowcount == 0:
                        # 已消费续跑失去会话时不能降回待注入，也不能残留 processing。
                        await self._record_failure(delivery_id, DeliveryState.FAILED, str(exc))
                else:
                    await self._record_failure(delivery_id, DeliveryState.FAILED, str(exc))
            logger.warning("Message delivery failed: %s", delivery_id, exc_info=True)
        finally:
            self._workers.pop(agent_id, None)
            self._wake.set()

    async def _record_failure(self, delivery_id: str, state: DeliveryState, error: str) -> None:
        # close 期间也必须保存中断；若磁盘失败，processing 留待下次 open 恢复。
        try:
            async with self._lock:
                if self._db is None:
                    return
                await self._db.execute(
                    "UPDATE mm_deliveries SET state=?, error=? "
                    "WHERE delivery_id=? AND (state='processing' OR "
                    "(state IN ('failed', 'interrupted') AND EXISTS "
                    "(SELECT 1 FROM mm_delivery_results r "
                    "WHERE r.delivery_id=mm_deliveries.delivery_id)))",
                    (state.value, error, delivery_id),
                )
                await self._db.commit()
        except Exception:
            logger.exception("Could not persist delivery failure: %s", delivery_id)

    async def _complete_delivery(
        self, delivery_id: str, result: AgentMessage, result_node_id: str | None = None
    ) -> None:
        if result.type == MessageType.ERROR:
            raise ValueError("Error responses cannot be recorded as successful final results")
        # 最终结果一旦落库，后续操作只重试事务，不再调用模型或工具。
        async with self._transaction() as db:
            delivery = await (
                await db.execute("SELECT * FROM mm_deliveries WHERE delivery_id=?", (delivery_id,))
            ).fetchone()
            if delivery is None:
                raise KeyError(delivery_id)
            existing = await (
                await db.execute(
                    "SELECT * FROM mm_delivery_results WHERE delivery_id=?", (delivery_id,)
                )
            ).fetchone()
            if existing is not None:
                if existing["content"] != result.content or (
                    result_node_id is not None and existing["result_node_id"] != result_node_id
                ):
                    raise ValueError("Final result conflicts with the durable result")
            else:
                if delivery["state"] != DeliveryState.PROCESSING.value:
                    raise RuntimeError("Only processing deliveries may record a final result")
                if delivery["consumed_node_id"] is not None and result_node_id is None:
                    raise ValueError("Atomic final result requires a context node reference")
                if result_node_id is not None:
                    node = await (
                        await db.execute(
                            "SELECT agent_name, session_id FROM nodes WHERE id=?", (result_node_id,)
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
                        result_node_id,
                        time.time(),
                        json.dumps(result.content_blocks)
                        if result.content_blocks is not None
                        else None,
                    ),
                )
        await self._finish_result(delivery_id)

    async def _finish_atomic(self, scope: _AtomicDelivery, result: AgentMessage) -> None:
        if not scope.consumed or scope.last_node_id is None:
            raise RuntimeError("Atomic final result requires a durably consumed input")
        if scope.drafts:
            raise RuntimeError("Cannot finish with uncommitted outbound messages")
        await self._complete_delivery(scope.delivery_id, result, scope.last_node_id)

    async def _finish_result(self, delivery_id: str) -> bool:
        async with self._transaction() as db:
            row = await (
                await db.execute(
                    "SELECT d.state, d.message_id, d.recipient_id, d.target_session_id, "
                    "d.consumed_session_id, r.content, r.result_node_id, r.content_blocks "
                    "FROM mm_deliveries d JOIN mm_delivery_results r "
                    "ON r.delivery_id=d.delivery_id WHERE d.delivery_id=?",
                    (delivery_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("No durable final result to complete")
            if row["state"] == DeliveryState.COMPLETED.value:
                return False
            message = await self._read_message(db, row["message_id"])
            if message.kind == MessageKind.REQUEST:
                reply_id = await self._insert_message(
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
        self._wake.set()
        return True
