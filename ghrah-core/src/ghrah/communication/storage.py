# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""消息领域记录与投递存储接口。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from ghrah.context.persistence.changes import ContextChanges
    from ghrah.context.turn import TurnClosure


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
    deliveries: tuple[DeliveryRecord, ...] = field(default=(), compare=False)


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
    turn_id: str | None = None
    delivery_policy: str = "queue"
    parked: bool = False
    parked_reason: str | None = None
    queue_block_reason: str | None = field(default=None, compare=False)
    display_state: str = field(default="", compare=False)
    queue_position: int = field(default=0, compare=False)
    policy_applied: bool = field(default=False, compare=False)


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


@dataclass(frozen=True)
class MessageSubmission:
    """已通过调用入口身份校验的待受理消息。"""

    sender_id: str
    recipient_ids: tuple[str, ...]
    content: str
    kind: MessageKind
    idempotency_key: str
    session_routing: bool
    sender_session_id: str | None = None
    target_sessions: Mapping[str, str] | None = None
    metadata: Mapping[str, object] | None = None
    external_sender: bool = False
    delivery_policy: Literal["inject", "queue"] = "queue"
    await_reply: bool = True


@dataclass(frozen=True)
class OutboundPreparation:
    """出站路由预检的结果，以及持久库是否已有同一请求。"""

    draft: PreparedMessage
    already_accepted: bool


@dataclass(frozen=True)
class ResumeCheckpoint:
    """显式续跑所用的持久节点位置。"""

    node_id: str
    session_id: str
    branch_id: str


@dataclass(frozen=True)
class DeliveryExecution:
    """领取后保存的执行身份，用于阻止此前领取者提交。"""

    delivery_id: str
    agent_id: str
    attempt: int
    turn_id: str | None


@dataclass(frozen=True)
class DeliveryClaim:
    """已提交的投递领取结果。"""

    execution: DeliveryExecution
    message: MessageRecord
    target_session_id: str | None
    resume: ResumeCheckpoint | None


@dataclass(frozen=True)
class CommittedIteration:
    """已确认的迭代节点和首条输入消费状态。"""

    node_id: str
    input_consumed: bool


@dataclass(frozen=True)
class FinalDeliveryResult:
    """一次执行交给存储的最终输出。"""

    content: str
    node_id: str | None
    content_blocks: Sequence[Mapping[str, object]] | None


@dataclass(frozen=True)
class SavedDeliveryResult:
    """第一段提交后可继续收尾的结果和轮次变化。"""

    needs_completion: bool
    turn_closure: TurnClosure | None = None


@dataclass(frozen=True)
class DeliveryRecoveryProbe:
    """同一次存储探测发现的轮次能力与收敛需求。"""

    with_turns: bool
    needs_repair: bool


@dataclass(frozen=True)
class TurnInput:
    """一轮待消费的注入输入；正文组装由调用方负责。"""

    delivery_id: str
    message_id: str
    target_session_id: str | None
    content: str
    sender_id: str
    external_sender: bool
    metadata: dict


class MessageQuery(Protocol):
    """同一存储域的类型化读查询。

    调用方只收发领域记录；SQL、行对象与连接留在实现内。查询各自取一次
    存储锁，不与其他写操作嵌套。
    """

    def require_open(self) -> None:
        """未打开或正在关闭时立即报错。"""
        ...

    async def read_message(self, message_id: str) -> MessageRecord: ...

    async def list_deliveries(self, message_id: str) -> tuple[DeliveryRecord, ...]: ...

    async def list_replies(self, message_id: str) -> tuple[MessageRecord, ...]: ...

    async def queue_blocker(self, delivery_id: str) -> DeliveryRecord | None: ...

    async def has_active_turn(self, agent_id: str) -> bool: ...

    async def require_active_turn(self, turn_id: str) -> None: ...

    async def load_turn_inputs(self, turn_id: str) -> tuple[TurnInput, ...]: ...

    async def has_injected_peer(self, message_id: str) -> bool: ...

    async def inline_wait_is_active(self, message_id: str, parent_delivery_id: str) -> bool: ...

    async def pending_room_projections(self, limit: int) -> tuple[tuple[str, int], ...]: ...

    async def complete_room_projection(self, message_id: str) -> None: ...

    async def record_room_projection_failure(
        self, message_id: str, *, attempts: int, error: str, exhausted: bool
    ) -> None: ...

    async def list_session_agent_ids(self) -> frozenset[str] | None: ...


@dataclass(frozen=True)
class RecoveredDelivery:
    """已提交的恢复结果，用于唤醒消息等待者和发布轮次闭合。"""

    message_id: str
    recipient_id: str
    turn_closure: TurnClosure | None


class DeliveryRepairError(RuntimeError):
    """收敛事务因存储错误未能完成。"""


class DeliveryClaimError(RuntimeError):
    """已定位投递，但未能确认领取。"""

    def __init__(self, delivery_id: str, message: str) -> None:
        super().__init__(message)
        self.delivery_id = delivery_id


class DeliveryClaimCancelled(asyncio.CancelledError):
    """领取取消前已定位投递，供上层收敛可能已提交的状态。"""

    def __init__(self, delivery_id: str) -> None:
        super().__init__(delivery_id)
        self.delivery_id = delivery_id


class DeliveryStore(Protocol):
    """同一存储域的消息受理、执行与持久收尾。"""

    async def prepare_outbound(self, submission: MessageSubmission) -> OutboundPreparation: ...

    async def accept_message(self, submission: MessageSubmission) -> MessageRecord: ...

    async def claim_delivery(
        self,
        agent_id: str,
        *,
        session_aware: bool,
        session_routing: bool,
        require_turns: bool,
    ) -> DeliveryClaim | None: ...

    async def commit_iteration(
        self,
        execution: DeliveryExecution,
        changes: ContextChanges,
        *,
        drafts: Sequence[PreparedMessage],
        consumed_deliveries: frozenset[str],
        inline_updates: Mapping[str, bool],
    ) -> CommittedIteration: ...

    async def save_final_result(
        self, execution: DeliveryExecution, result: FinalDeliveryResult
    ) -> SavedDeliveryResult: ...

    async def finish_delivery(self, delivery_id: str) -> str | None: ...

    async def unfinished_completions(self) -> tuple[str, ...]: ...

    async def probe_delivery_recovery(
        self, workers: Collection[str], *, require_turns: bool, check_repair: bool
    ) -> DeliveryRecoveryProbe: ...

    async def has_claimable_delivery(self, agent_id: str, *, with_turns: bool) -> bool: ...

    async def repair_deliveries(
        self, workers: Collection[str], *, require_turns: bool
    ) -> tuple[RecoveredDelivery, ...]: ...

    async def record_failure(
        self,
        delivery_id: str,
        state: DeliveryState,
        error: str,
        *,
        session_unavailable: bool = False,
        execution: DeliveryExecution | None = None,
    ) -> RecoveredDelivery | None: ...

    async def cancel_delivery(self, delivery_id: str) -> str: ...

    async def retry_delivery(self, delivery_id: str) -> None: ...

    async def delivery_progress(self, delivery_id: str) -> DeliveryProgress: ...

    async def request_resume(
        self,
        delivery_id: str,
        *,
        expected_last_node_id: str,
        recipient_busy: Callable[[str], bool],
    ) -> None: ...

    async def unblock_delivery(self, delivery_id: str) -> None: ...
