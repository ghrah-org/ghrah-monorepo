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
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from contextlib import AbstractAsyncContextManager
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import aiosqlite

from ghrah.agents.runner import detached_runner_context
from ghrah.chat.content import TextBlock, blocks_from_dicts
from ghrah.chat.message import ChatMessage
from ghrah.communication.delivery_queue import (
    find_blocker,
    has_turns,
    pending_turn_input_ids,
    release_turn_inputs,
)
from ghrah.communication.sqlite_message_store import SqliteMessageStore
from ghrah.communication.storage import (
    DeliveryClaimCancelled,
    DeliveryClaimError,
    DeliveryExecution,
    DeliveryProgress,
    DeliveryRecord,
    DeliveryRepairError,
    DeliveryState,
    DeliveryStore,
    FinalDeliveryResult,
    MessageKind,
    MessageRecord,
    MessageSubmission,
    PreparedMessage,
)
from ghrah.context import turn
from ghrah.context.iteration_commit import clear_durable_iteration_scope
from ghrah.context.manager import DeliverySessionUnavailableError
from ghrah.context.persistence.changes import ContextChanges
from ghrah.core.message import AgentMessage, MessageType

if TYPE_CHECKING:
    from ghrah.agents.base import ActorAgent
    from ghrah.context.manager import ContextManager
    from ghrah.context.persistence import PersistenceBackend

logger = logging.getLogger(__name__)

#: Room 投影的最大重试次数；到达后标记终结，避免永久失败的行每轮扫描空转。
ROOM_PROJECTION_MAX_ATTEMPTS = 5
ROOM_PROJECTION_BATCH_SIZE = 32
Receiver = Callable[[AgentMessage], Awaitable[AgentMessage]]
TurnClosurePublisher = Callable[[turn.TurnClosure], Awaitable[None]]


@dataclass
class _AtomicDelivery:
    manager: MessageManager
    execution: DeliveryExecution
    drafts: list[PreparedMessage]
    context_manager: ContextManager
    active: bool = True
    consumed: bool = False
    committing: bool = False
    last_node_id: str | None = None
    inline_updates: dict[str, bool] = field(default_factory=dict)
    consumed_deliveries: set[str] = field(default_factory=set)

    @property
    def delivery_id(self) -> str:
        return self.execution.delivery_id

    @property
    def agent_id(self) -> str:
        return self.execution.agent_id


_CLAIM: ContextVar[DeliveryExecution | None] = ContextVar("ghrah_delivery_claim", default=None)
_ATOMIC: ContextVar[_AtomicDelivery | None] = ContextVar("ghrah_atomic_delivery", default=None)


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
        _store: SqliteMessageStore | None = None,
    ) -> None:
        if (
            not project_id.strip()
            or isinstance(agent_ids, str)
            or (not agent_ids and not allow_persisted_agents)
            or any(not item.strip() for item in agent_ids)
        ):
            raise ValueError("Scope and explicit non-empty agent_ids are required")
        if not 0 < poll_interval < float("inf"):
            raise ValueError("poll_interval must be positive and finite")
        path = Path(db_path).resolve()
        if _store is not None and (_store.db_path != path or _store.scope_id != project_id):
            raise ValueError("Message store path/scope mismatch")
        self._sqlite = _store or SqliteMessageStore(path, project_id)
        self._delivery_store: DeliveryStore = self._sqlite
        self._agent_ids = frozenset(agent_ids)
        self._poll_interval = poll_interval
        self._session_routing = session_routing
        self._allow_persisted_agents = allow_persisted_agents
        self._wait_edges: dict[str, set[str]] = {}
        self._room_projector: Callable[[MessageRecord], Awaitable[None]] | None = None
        self._projection_lock = asyncio.Lock()
        self._session_receivers: set[str] = set()
        self._lock = self._sqlite.lock
        self._scan_lock = asyncio.Lock()
        self._receivers: dict[str, Receiver] = {}
        self._turn_closure_publishers: dict[str, TurnClosurePublisher] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._reply_waiters: set[asyncio.Future[None]] = set()
        self._reply_changes: dict[str, set[asyncio.Event]] = {}
        self._paused_agents: set[str] = set()
        self._dispatcher: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closing = False
        self._repair_retry_at = 0.0

    @property
    def db_path(self) -> Path:
        """返回与 Core 原子提交共用的数据库路径。"""
        return self._sqlite.db_path

    def context_backend(self, agent_config: object) -> PersistenceBackend:
        """为受管 Agent 创建可参与同域事务的链存储。"""
        return self._sqlite.context_backend(agent_config)

    def validate_context_backend(self, backend: object) -> None:
        """检查 Agent 的链存储能否参加消息事务。"""
        self._sqlite.validate_context_backend(backend)

    @property
    def project_id(self) -> str:
        """返回持久消息所属项目。"""
        return self._sqlite.scope_id

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

    async def open(self, *, start_dispatcher: bool = True, prepare_context: bool = False) -> None:
        """打开服务；上次进程未完成的投递转为中断，禁止隐式重跑。"""
        await self._sqlite.open(prepare_context=prepare_context)
        self._closing = False
        self._repair_retry_at = 0.0
        try:
            await self.refresh_agent_ids()
            await self.recover_completions()
            if start_dispatcher:
                self.start_dispatcher()
        except BaseException:
            await self.close()
            raise

    async def prepare_dispatch(self) -> None:
        """显式验收恢复路径；错误直接传播，不通过后台循环吞掉初始化故障。"""
        self._require_db()
        if self._closing or self._dispatcher is not None or self._receivers:
            raise RuntimeError("Dispatch preparation requires an open, inactive manager")
        await self.recover_completions()
        await self.recover_room_projections()

    def start_dispatcher(self) -> None:
        """在运行时就绪时幂等启动调度，不创建或绑定 Agent。"""
        self._require_db()
        if self._closing:
            raise RuntimeError("MessageManager is closing")
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(
                self._dispatch_loop(), context=self._task_context()
            )

    async def close(self) -> None:
        """停止调度并等待取消收敛；处理中断记录保留到下次显式重试。"""
        self._closing = True
        self._sqlite.stop_accepting()
        tasks = [*self._workers.values()]
        if self._dispatcher is not None:
            tasks.append(self._dispatcher)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._paused_agents.clear()
        self._receivers.clear()
        self._turn_closure_publishers.clear()
        self._session_receivers.clear()
        self._dispatcher = None
        await self._sqlite.close()

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
        self._turn_closure_publishers.pop(agent_id, None)
        self._paused_agents.discard(agent_id)
        self._session_receivers.discard(agent_id)
        task = self._workers.get(agent_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._workers.pop(agent_id, None)

    async def drain(self, agent_ids: Collection[str], *, timeout: float) -> None:
        """暂停指定 Agent 的新投递，有界等待在途工作及回复读取。"""
        async with self._scan_lock:
            self._paused_agents.update(agent_ids)
            active = [
                *(self._workers[key] for key in agent_ids if key in self._workers),
                *self._reply_waiters,
            ]
        if active:
            await asyncio.wait(active, timeout=timeout)

    async def bind_atomic(self, agent: ActorAgent) -> None:
        """显式绑定同文件 SQLite 上下文，原子提交消费确认与本轮出站。"""
        agent_id = agent.config.effective_agent_id
        self._require_db()
        self._check_identity(agent_id)
        if agent_id in self._receivers:
            raise ValueError(f"Agent already bound: {agent_id}")
        cm = agent._context_manager
        self.validate_context_backend(cm.persistence)
        if agent._runner.busy or cm.in_iteration:
            raise RuntimeError("Cannot bind atomic receive while agent is running")
        await cm.persist()

        async def receive(message: AgentMessage) -> AgentMessage:
            execution = _CLAIM.get()
            if (
                execution is None
                or execution.agent_id != agent_id
                or execution.delivery_id != message.metadata.get("delivery_id")
            ):
                raise RuntimeError("Atomic receive requires a claimed delivery")
            scope = _AtomicDelivery(self, execution, [], cm)
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
                    scope.consumed_deliveries.clear()

                if resume_from is not None:
                    result = await agent.resume_atomic(
                        message,
                        commit,
                        rollback,
                        finish,
                        session_id=message.metadata["resume_session_id"],
                        branch_id=message.metadata["resume_branch_id"],
                        last_node_id=resume_from,
                        input_loader=self.load_turn_inputs,
                        register_consumed=scope.consumed_deliveries.add,
                    )
                else:
                    result = await agent.receive_atomic(
                        message,
                        commit,
                        rollback,
                        finish,
                        message.metadata.get("target_session_id"),
                        self.load_turn_inputs,
                        scope.consumed_deliveries.add,
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
        self._turn_closure_publishers[agent_id] = agent.publish_turn_closure

    @staticmethod
    async def release_turn_inputs(
        db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]:
        """使用 ContextManager 持有的事务，无需获取消息服务锁。"""
        return await release_turn_inputs(db, turn_id=turn_id, opener_delivery_id=opener_delivery_id)

    @staticmethod
    async def pending_turn_input_ids(
        db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]:
        """从预览的只读连接查询将解绑的投递。"""
        return await pending_turn_input_ids(
            db, turn_id=turn_id, opener_delivery_id=opener_delivery_id
        )

    async def has_active_turn(self, agent_id: str) -> bool:
        """检查调度器是否已为该接收方持久领取一轮。"""
        async with self._lock:
            db = self._require_db()
            return await has_turns(db) and await turn.has_active_turn(db, agent_id)

    converge_turn_deliveries = staticmethod(SqliteMessageStore.converge_turn_deliveries)

    async def load_turn_inputs(self, turn_id: str) -> tuple[ChatMessage, ...]:
        """按受理顺序读取本轮尚未消费的持久注入，不修改投递。"""
        async with self._lock:
            db = self._require_db()
            rows = await (
                await db.execute(
                    "SELECT d.delivery_id, d.target_session_id, m.message_id, m.content, "
                    "m.sender_id, m.external_sender, m.metadata FROM mm_deliveries d "
                    "JOIN turns t ON t.turn_id=d.turn_id "
                    "JOIN mm_messages m ON m.message_id=d.message_id "
                    "WHERE d.turn_id=? AND t.status IN ('open','waiting_hitl') "
                    "AND d.state='pending' AND d.consumed_node_id IS NULL "
                    "AND d.delivery_policy='inject' AND d.blocked_reason IS NULL AND d.parked=0 "
                    "ORDER BY d.seq",
                    (turn_id,),
                )
            ).fetchall()
            inputs = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                blocks = metadata.get("content_blocks")
                text = row["content"]
                content = blocks_from_dicts(blocks) if blocks else text
                if (
                    blocks
                    and text
                    and not any(
                        isinstance(block, TextBlock) and block.text == text for block in content
                    )
                ):
                    content.insert(0, TextBlock(text=text))
                inputs.append(
                    ChatMessage.user(
                        content,
                        source=row["sender_id"].removeprefix("human:")
                        if row["external_sender"]
                        else f"agent:{row['sender_id']}",
                        metadata={
                            **metadata,
                            "message_id": row["message_id"],
                            "delivery_id": row["delivery_id"],
                            "turn_id": turn_id,
                            "delivery_policy": "inject",
                            "message_type": metadata.get("message_type", "chat"),
                            "target_session_id": row["target_session_id"],
                        },
                    )
                )
        return tuple(inputs)

    async def cancel(self, delivery_id: str) -> None:
        """取消尚未领取的排队投递，保留显式重试入口。"""
        message_id = await self._delivery_store.cancel_delivery(delivery_id)
        self._notify_reply_waiters(message_id)
        self._wake.set()

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
        if scope is not None and scope.manager is self:
            scope.context_manager.check_turn_revoked()
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
        prepared = await self._delivery_store.prepare_outbound(
            MessageSubmission(
                sender_id=scope.agent_id,
                recipient_ids=targets,
                content=content,
                kind=MessageKind(kind),
                idempotency_key=idempotency_key,
                session_routing=self._session_routing,
                sender_session_id=scope.context_manager.active_session_id
                if self._session_routing or target_sessions is not None
                else None,
                target_sessions=target_sessions,
                metadata=metadata,
            )
        )
        draft = prepared.draft
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
                    draft.sender_session_id,
                    draft.target_sessions,
                ):
                    raise ValueError("Idempotency key conflicts with prepared sessions")
                return previous
        if not prepared.already_accepted:
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
        delivery_policy: Literal["inject", "queue"] = "queue",
        await_reply: bool = True,
    ) -> MessageRecord:
        """原子受理一对一或一对多消息；同键同载荷返回原消息。"""
        if delivery_policy not in ("inject", "queue") or not isinstance(await_reply, bool):
            raise ValueError("Invalid delivery policy or reply expectation")
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
        accepted = await self._delivery_store.accept_message(
            MessageSubmission(
                sender_id=sender_id,
                recipient_ids=targets,
                content=content,
                kind=kind,
                idempotency_key=idempotency_key,
                session_routing=self._session_routing,
                sender_session_id=sender_session_id,
                target_sessions=target_sessions,
                metadata=metadata,
                external_sender=_external_sender,
                delivery_policy=delivery_policy,
                await_reply=await_reply,
            )
        )
        self._wake.set()
        return accepted

    async def send_external(
        self,
        *,
        sender: str,
        recipient_ids: Collection[str],
        content: str,
        idempotency_key: str,
        metadata: Mapping[str, object] | None = None,
        delivery_policy: Literal["inject", "queue"] = "queue",
        await_reply: bool = True,
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
            delivery_policy=delivery_policy,
            await_reply=await_reply,
        )

    async def wait_replies(
        self, message_id: str, *, timeout: float = -1, tolerate_errors: bool = False
    ) -> tuple[MessageRecord, ...]:
        """等候已受理请求的持久结果；超时不删除消息或取消目标执行。"""
        return await self._wait_replies(
            message_id, timeout=timeout, tolerate_errors=tolerate_errors
        )

    async def _wait_replies(
        self,
        message_id: str,
        *,
        timeout: float,
        tolerate_errors: bool,
        context_manager: ContextManager | None = None,
    ) -> tuple[MessageRecord, ...]:
        changed = asyncio.Event()
        poll_fallback = min(0.25, timeout / 2) if timeout > 0 else 0.25
        turn_id = context_manager.active_turn_id if context_manager is not None else None
        abort_event = context_manager.turn_abort_event() if context_manager is not None else None

        async def require_turn() -> None:
            if context_manager is None:
                return
            context_manager.require_turn(turn_id)
            if turn_id is not None:
                async with self._lock:
                    # 其他进程的中止不会设置本进程的 Event。
                    await turn.require_active(self._require_db(), turn_id)
                context_manager.require_turn(turn_id)

        async def wake_on_abort(event: asyncio.Event) -> None:
            await event.wait()
            changed.set()

        async def wait() -> tuple[MessageRecord, ...]:
            while True:
                changed.clear()
                await require_turn()
                deliveries = await self.get_deliveries(message_id)
                if any(d.delivery_policy == "inject" and d.turn_id for d in deliveries):
                    async with self._lock:
                        db = self._require_db()
                        injected = await (
                            await db.execute(
                                "SELECT 1 FROM mm_deliveries d JOIN turns t ON t.turn_id=d.turn_id "
                                "WHERE d.message_id=? AND d.delivery_policy='inject' "
                                "AND d.delivery_id IS NOT t.opener_delivery_id LIMIT 1",
                                (message_id,),
                            )
                        ).fetchone()
                    if injected is not None:
                        raise ValueError("injected delivery has no independent reply")
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
                                f"reason={blocker.queue_block_reason or blocker.error}, "
                                f"blocked_reason={blocker.blocked_reason}. "
                                "Wait for the blocking delivery to finish, or fix its session; "
                                "the pending request is retained."
                            )
                # 本进程在提交后唤醒，定期回读覆盖外部进程和同队列的其他投递变化。
                try:
                    await asyncio.wait_for(changed.wait(), timeout=poll_fallback)
                except TimeoutError:
                    pass

        marker = asyncio.get_running_loop().create_future()
        updates = self._reply_changes.setdefault(message_id, set())
        updates.add(changed)
        self._reply_waiters.add(marker)
        abort_watch = asyncio.create_task(wake_on_abort(abort_event)) if abort_event else None
        try:
            replies = await wait() if timeout < 0 else await asyncio.wait_for(wait(), timeout)
        except turn.TurnRevokedError:
            raise
        except Exception:
            # 同时出现回复错误或超时时，先确认本轮是否已经中止。
            await require_turn()
            raise
        finally:
            self._reply_waiters.discard(marker)
            updates.discard(changed)
            if not updates:
                self._reply_changes.pop(message_id, None)
            if not marker.done():
                marker.set_result(None)
            if abort_watch is not None:
                abort_watch.cancel()
                await asyncio.gather(abort_watch, return_exceptions=True)
        # 读取结果期间也可能发生中止。提交节点时还会在事务内复核。
        await require_turn()
        return replies

    def _notify_reply_waiters(self, message_id: str) -> None:
        for changed in self._reply_changes.get(message_id, ()):
            changed.set()

    async def wait_inline(
        self, message_id: str, *, timeout: float = -1, tolerate_errors: bool = False
    ) -> tuple[MessageRecord, ...]:
        """等待检查点已提交的同步工具请求，回复消费随后续节点原子确认。"""
        scope = _ATOMIC.get()
        if scope is None or scope.manager is not self or not scope.active:
            raise RuntimeError("Inline wait requires an atomic delivery")
        context_manager = scope.context_manager
        context_manager.check_turn_revoked()
        turn_id = context_manager.active_turn_id
        request = await self.get_message(message_id)
        async with self._lock:
            db = self._require_db()
            row = await (
                await db.execute(
                    "SELECT 1 FROM mm_inline_waits "
                    "WHERE message_id=? AND parent_delivery_id=? AND state='waiting'",
                    (message_id, scope.delivery_id),
                )
            ).fetchone()
            if row is None and turn_id is not None:
                await turn.require_active(db, turn_id)
        context_manager.require_turn(turn_id)
        if row is None:
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
            replies = await self._wait_replies(
                message_id,
                timeout=timeout,
                tolerate_errors=tolerate_errors,
                context_manager=context_manager,
            )
            scope.inline_updates[message_id] = True
            return replies
        except turn.TurnRevokedError:
            raise
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
            return await self._sqlite._read_message(self._require_db(), message_id)

    async def get_deliveries(self, message_id: str) -> tuple[DeliveryRecord, ...]:
        """查询各接收者的独立状态；未知消息报错。"""
        async with self._lock:
            db = self._require_db()
            rows = await (
                await db.execute(
                    "SELECT d.*, r.delivery_id AS result_id, r.result_node_id "
                    "FROM mm_messages m LEFT JOIN mm_deliveries d ON d.message_id=m.message_id "
                    "LEFT JOIN mm_delivery_results r ON r.delivery_id=d.delivery_id "
                    "WHERE m.message_id=? ORDER BY d.seq",
                    (message_id,),
                )
            ).fetchall()
        if not rows:
            raise KeyError(message_id)
        return tuple(
            self._sqlite.delivery_record(row) for row in rows if row["delivery_id"] is not None
        )

    async def get_queue_blocker(self, delivery_id: str) -> DeliveryRecord | None:
        """查询同会话尚未收敛的前序投递，并报告轮次或结果收尾的阻塞原因。"""
        async with self._lock:
            row = await find_blocker(self._require_db(), delivery_id)
        return self._sqlite.delivery_record(row) if row is not None else None

    async def get_replies(self, message_id: str) -> tuple[MessageRecord, ...]:
        """按原请求查询各响应者持久回复，不依赖内存 Future。"""
        async with self._lock:
            db = self._require_db()
            await self._sqlite._read_message(db, message_id)
            rows = await (
                await db.execute(
                    "SELECT message_id FROM mm_messages WHERE reply_to=? ORDER BY seq",
                    (message_id,),
                )
            ).fetchall()
            return tuple([await self._sqlite._read_message(db, row[0]) for row in rows])

    async def retry(self, delivery_id: str) -> None:
        """显式重试失败或中断投递；调用方须确认重复副作用风险。"""
        await self._delivery_store.retry_delivery(delivery_id)
        self._wake.set()

    async def get_progress(self, delivery_id: str) -> DeliveryProgress:
        """查询消费与最后提交节点，说明当前是否可申请续跑。"""
        return await self._delivery_store.delivery_progress(delivery_id)

    async def resume_interrupted(
        self,
        delivery_id: str,
        *,
        expected_last_node_id: str,
        acknowledge_side_effect_risk: bool,
    ) -> None:
        """显式申请从最后持久节点续跑。

        当前仅供内部调用，未接入产品命令。调用方须满足事务前置条件，
        并确认未提交外部副作用的重复风险。
        """
        if acknowledge_side_effect_risk is not True:
            raise ValueError("Explicit acknowledgement of side-effect risk is required")

        def recipient_busy(agent_id: str) -> bool:
            worker = self._workers.get(agent_id)
            return worker is not None and not worker.done()

        await self._delivery_store.request_resume(
            delivery_id, expected_last_node_id=expected_last_node_id, recipient_busy=recipient_busy
        )
        self._wake.set()

    async def resume_delivery(self, delivery_id: str) -> None:
        """目标会话恢复开放后解除阻塞，保留原归属，不自动改投其他会话。"""
        await self._delivery_store.unblock_delivery(delivery_id)
        self._wake.set()

    async def dispatch_pending(self) -> None:
        """处理当前在线 agent 可领取的消息；离线和中断不阻塞调用返回。"""
        self._require_db()
        while True:
            scan_saw_worker = await self._scan()
            tasks = tuple(self._workers.values())
            if tasks:
                await asyncio.gather(*tasks)
            elif not scan_saw_worker:
                return

    async def recover_completions(self) -> int:
        """补齐已持久最终结果的收尾，不重新执行 agent；返回补齐数量。"""
        deliveries = await self._delivery_store.unfinished_completions()
        completed = 0
        for delivery_id in deliveries:
            try:
                completed += int(await self._finish_result(delivery_id))
            except Exception as exc:
                await self._record_failure(delivery_id, DeliveryState.FAILED, str(exc))
                logger.warning("Completion recovery failed: %s", delivery_id, exc_info=True)
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

    async def recover_room_projections(self, *, limit: int = -1) -> int:
        """重放未确认的 Room 投影；接收端按消息引用幂等，不重新执行 agent。

        永久性失败（如目标 Room 已删除）不能无限重试：每轮扫描都会重试一次，
        会让日志持续刷屏并空转写库。这里按 ``attempts`` 计数，到达
        :data:`ROOM_PROJECTION_MAX_ATTEMPTS` 后标记为终结并只告警一次；
        ``error`` 保留最后一次失败原因供排查。
        """
        if self._room_projector is None:
            return 0
        async with self._projection_lock:
            async with self._lock:
                rows = await (
                    await self._require_db().execute(
                        "SELECT message_id, attempts FROM mm_room_projections "
                        "WHERE completed=0 ORDER BY rowid LIMIT ?",
                        (limit,),
                    )
                ).fetchall()
            completed = 0
            for row in rows:
                message_id, attempts = row[0], int(row[1] or 0)
                try:
                    await self._room_projector(await self.get_message(message_id))
                    async with self._transaction() as db:
                        await db.execute(
                            "UPDATE mm_room_projections SET completed=1, error=NULL "
                            "WHERE message_id=?",
                            (message_id,),
                        )
                    completed += 1
                except Exception as exc:
                    failed = attempts + 1
                    exhausted = failed >= ROOM_PROJECTION_MAX_ATTEMPTS
                    async with self._transaction() as db:
                        await db.execute(
                            "UPDATE mm_room_projections SET attempts=?, error=?, completed=? "
                            "WHERE message_id=?",
                            (failed, str(exc), 1 if exhausted else 0, message_id),
                        )
                    if exhausted:
                        logger.warning(
                            "Room projection abandoned after %d attempts: %s: %s",
                            failed,
                            message_id,
                            exc,
                        )
                    elif failed == 1:
                        logger.warning("Room projection remains pending: %s: %s", message_id, exc)
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
        return self._sqlite.require_connection()

    def _transaction(self) -> AbstractAsyncContextManager[aiosqlite.Connection]:
        return self._sqlite.transaction()

    _next_delivery_sql = staticmethod(SqliteMessageStore._next_delivery_sql)

    async def _commit_atomic(self, scope: _AtomicDelivery, changes: ContextChanges) -> None:
        scope.committing = True
        try:
            await self._apply_atomic(scope, changes)
        finally:
            scope.committing = False

    async def _apply_atomic(self, scope: _AtomicDelivery, changes: ContextChanges) -> None:
        committed = await self._delivery_store.commit_iteration(
            scope.execution,
            changes,
            drafts=tuple(scope.drafts),
            consumed_deliveries=frozenset(scope.consumed_deliveries),
            inline_updates=dict(scope.inline_updates),
        )
        scope.drafts.clear()
        scope.inline_updates.clear()
        scope.consumed_deliveries.clear()
        scope.consumed = committed.input_consumed
        scope.last_node_id = committed.node_id
        self._wake.set()

    @staticmethod
    def _task_context() -> Context:
        context = detached_runner_context()
        context.run(_CLAIM.set, None)
        context.run(_ATOMIC.set, None)
        context.run(clear_durable_iteration_scope)
        return context

    async def _dispatch_loop(self) -> None:
        while not self._closing:
            self._wake.clear()
            delay = self._poll_interval
            try:
                await self._scan()
            except Exception:
                delay = max(1.0, delay)
                logger.exception("MessageManager pending scan failed")
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except TimeoutError:
                pass

    async def _scan(self) -> bool:
        """扫描并报告是否遇到 worker，供同步调度复查刚释放的队列。"""
        async with self._scan_lock:
            await self.recover_completions()
            probe = await self._delivery_store.probe_delivery_recovery(
                self._workers.keys(),
                require_turns=bool(self._session_receivers),
                check_repair=time.monotonic() >= self._repair_retry_at,
            )
            if probe.needs_repair:
                await self._repair_deliveries()
            saw_worker = False
            for agent_id, receiver in tuple(self._receivers.items()):
                if agent_id in self._paused_agents:
                    continue
                if agent_id in self._workers:
                    saw_worker = True
                    continue
                candidate = await self._delivery_store.has_claimable_delivery(
                    agent_id, with_turns=probe.with_turns
                )
                if candidate and self._receivers.get(agent_id) is receiver:
                    self._workers[agent_id] = asyncio.create_task(
                        self._run(agent_id, receiver), context=self._task_context()
                    )
                    saw_worker = True
            await self.recover_room_projections(
                limit=ROOM_PROJECTION_BATCH_SIZE if self._dispatcher is not None else -1
            )
            return saw_worker

    async def _repair_deliveries(self) -> None:
        try:
            recovered = await self._delivery_store.repair_deliveries(
                self._workers.keys(), require_turns=bool(self._session_receivers)
            )
        except DeliveryRepairError:
            if self._dispatcher is None:
                raise
            self._repair_retry_at = time.monotonic() + max(1.0, self._poll_interval)
            logger.warning("Could not recover stranded message deliveries", exc_info=True)
            return
        for item in recovered:
            self._notify_reply_waiters(item.message_id)
            publisher = self._turn_closure_publishers.get(item.recipient_id)
            if item.turn_closure is not None and publisher is not None:
                await publisher(item.turn_closure)

    async def _run(self, agent_id: str, receiver: Receiver) -> None:
        delivery_id: str | None = None
        execution: DeliveryExecution | None = None
        closure = None
        publish_closure = self._turn_closure_publishers.get(agent_id)
        try:
            claim = await self._delivery_store.claim_delivery(
                agent_id,
                session_aware=agent_id in self._session_receivers,
                session_routing=self._session_routing,
                require_turns=bool(self._session_receivers),
            )
            if claim is None:
                return
            execution = claim.execution
            delivery_id = execution.delivery_id
            record = claim.message
            resume = claim.resume
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
                    "agent_id": agent_id,
                    "message_id": record.message_id,
                    "delivery_id": delivery_id,
                    "message_kind": record.kind.value,
                    "turn_id": claim.execution.turn_id,
                    "reply_to": record.reply_to,
                    "sender_session_id": record.sender_session_id,
                    "target_session_id": claim.target_session_id,
                    "resume_from_node_id": resume.node_id if resume else None,
                    "resume_session_id": resume.session_id if resume else None,
                    "resume_branch_id": resume.branch_id if resume else None,
                },
            )
            token = _CLAIM.set(claim.execution)
            try:
                result = await receiver(message)
            finally:
                _CLAIM.reset(token)
            if not isinstance(result, AgentMessage):
                raise TypeError("Receiver must return AgentMessage")
            if result.type == MessageType.ERROR:
                raise RuntimeError(result.content)
            closure = await self._complete_delivery(claim.execution, result)
        except turn.TurnRevokedError:
            logger.debug("Delivery lost its turn execution right: %s", delivery_id)
        except Exception as exc:
            if isinstance(exc, DeliveryClaimError):
                delivery_id = exc.delivery_id
            if delivery_id is not None:
                closure = await self._record_failure(
                    delivery_id,
                    DeliveryState.FAILED,
                    str(exc),
                    session_unavailable=isinstance(exc, DeliverySessionUnavailableError),
                    execution=execution,
                )
            logger.warning("Message delivery failed: %s", delivery_id, exc_info=True)
        except BaseException as exc:
            if isinstance(exc, DeliveryClaimCancelled):
                delivery_id = exc.delivery_id
            if delivery_id is not None:
                closure = await self._record_failure(
                    delivery_id, DeliveryState.INTERRUPTED, "Worker stopped", execution=execution
                )
            raise
        finally:
            try:
                if closure is not None and publish_closure is not None:
                    await publish_closure(closure)
            finally:
                self._workers.pop(agent_id, None)
                self._wake.set()

    async def _record_failure(
        self,
        delivery_id: str,
        state: DeliveryState,
        error: str,
        *,
        session_unavailable: bool = False,
        execution: DeliveryExecution | None = None,
    ) -> turn.TurnClosure | None:
        try:
            saved = await self._delivery_store.record_failure(
                delivery_id,
                state,
                error,
                session_unavailable=session_unavailable,
                execution=execution,
            )
        except turn.TurnRevokedError:
            return None
        except Exception:
            logger.exception("Could not persist delivery failure: %s", delivery_id)
            return None
        if saved is None:
            return None
        self._notify_reply_waiters(saved.message_id)
        return saved.turn_closure

    async def _complete_delivery(
        self, execution: DeliveryExecution, result: AgentMessage, result_node_id: str | None = None
    ) -> turn.TurnClosure | None:
        if result.type == MessageType.ERROR:
            raise ValueError("Error responses cannot be recorded as successful final results")
        saved = await self._delivery_store.save_final_result(
            execution, FinalDeliveryResult(result.content, result_node_id, result.content_blocks)
        )
        if saved.needs_completion:
            await self._finish_result(execution.delivery_id)
        return saved.turn_closure

    async def _finish_atomic(self, scope: _AtomicDelivery, result: AgentMessage) -> None:
        scope.context_manager.check_turn_revoked()
        if not scope.consumed or scope.last_node_id is None:
            raise RuntimeError("Atomic final result requires a durably consumed input")
        if scope.drafts:
            raise RuntimeError("Cannot finish with uncommitted outbound messages")
        await self._complete_delivery(scope.execution, result, scope.last_node_id)

    async def _finish_result(self, delivery_id: str) -> bool:
        message_id = await self._delivery_store.finish_delivery(delivery_id)
        if message_id is None:
            return False
        self._notify_reply_waiters(message_id)
        self._wake.set()
        return True
