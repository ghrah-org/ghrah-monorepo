# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次、审批与回滚的运行时存储接口。

``ContextManager`` 只通过这里的类型化操作读写持久轮次，不持有数据库连接、
不解释 SQL 行。实现方负责事务边界与行转换；同一作用域的投递结算以
:class:`DeliverySettlement` 的形式在轮次事务内被调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from ghrah.context.branch import ActionBranch
    from ghrah.context.persistence.changes import ContextChanges
    from ghrah.context.recovery import ConvergenceOutcome
    from ghrah.context.turn import (
        ActiveTurn,
        HitlOutcome,
        RollbackOutcome,
        RollbackPoint,
        RollbackTarget,
        TurnCloseStatus,
    )

__all__ = [
    "DeliverySettlement",
    "HitlWriteResult",
    "RollbackPreview",
    "TurnRecord",
    "TurnStore",
]


@dataclass(frozen=True)
class TurnRecord:
    """一条持久轮次的权威事实。"""

    turn_id: str
    agent_name: str
    session_id: str
    opener_delivery_id: str | None
    status: str
    closing_kind: str | None
    opened_at: str | None
    closed_at: str | None
    start_node_id: str | None
    base_node_id: str | None
    end_node_id: str | None
    branch_id: str | None
    end_branch_id: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class HitlWriteResult:
    """持久审批操作的结果。持久记录缺失与写入未确认分别表达。"""

    persisted: bool = False
    changed: bool = False
    turn_id: str | None = None
    status: str | None = None
    resolution: Any = None
    session_id: str | None = None
    delivery_id: str | None = None
    error: Literal["no_active_turn", "unconfirmed"] | None = None


@dataclass(frozen=True)
class RollbackPreview:
    """从已提交数据计算的回滚目标与影响。"""

    turn_id: str
    session_id: str
    target: RollbackTarget
    target_node_id: str
    target_branch_id: str
    target_is_compact: bool
    base_node_id: str | None
    start_node_id: str | None
    end_node_id: str | None
    truncated_node_count: int
    crosses_compact: bool
    head_unchanged: bool
    released_delivery_ids: tuple[str, ...]


class DeliverySettlement(Protocol):
    """在轮次事务内结算投递；实现方与轮次存储共享同一事务。"""

    async def converge(
        self,
        *,
        turn_id: str,
        opener_delivery_id: str | None,
        opener_state: str,
        opener_error: str,
        terminal_state: str,
    ) -> int: ...

    async def release_inputs(
        self, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]: ...

    async def pending_inputs(
        self, *, turn_id: str, opener_delivery_id: str | None
    ) -> list[str]: ...


class TurnStore(Protocol):
    """同一存储域内的轮次、审批与回滚操作。"""

    async def load_turn(self, agent_name: str, turn_id: str) -> TurnRecord | None: ...

    async def has_active_turn(self, agent_name: str) -> bool: ...

    async def open_turn(self, turn: ActiveTurn) -> str | None: ...

    async def close_turn(
        self,
        *,
        turn_id: str,
        status: TurnCloseStatus,
        closing_kind: str,
        end_node_id: str | None,
    ) -> None: ...

    async def commit_context_iteration(
        self, changes: ContextChanges, *, turn_id: str | None, idle_compact: bool
    ) -> None: ...

    async def converge_turn(
        self,
        *,
        agent_name: str,
        turn_id: str | None,
        session_id: str | None,
        reason: str,
        status: Literal["aborted", "failed"],
        closing_kind: str,
        description: str,
        execution_phase: str,
        active_turn_id: str | None,
        error: Exception | None,
        settlement: DeliverySettlement | None,
    ) -> ConvergenceOutcome: ...

    async def preview_rollback(
        self,
        *,
        agent_name: str,
        turn_id: str,
        target: RollbackTarget,
        node_id: str | None,
        settlement: DeliverySettlement | None,
    ) -> RollbackPreview: ...

    async def rollback_turn(
        self,
        *,
        agent_name: str,
        turn_id: str,
        target: RollbackTarget,
        node_id: str | None,
        create_branch: Callable[[RollbackPoint, str], tuple[ActionBranch, ContextChanges]],
        settlement: DeliverySettlement | None,
    ) -> RollbackOutcome: ...

    async def normalize_turns(self, agent_name: str) -> None: ...

    async def apply_pending_diagnoses(self, agent_name: str) -> None: ...

    async def open_hitl_request(
        self,
        *,
        turn_id: str,
        promise_id: str,
        ability_name: str,
        tool_call_id: str,
        tool_call: dict[str, Any],
        reason: str,
        options: dict[str, Any],
        last_node_id: str,
        deadline_at: str | None,
    ) -> HitlWriteResult: ...

    async def finish_hitl_request(
        self,
        *,
        agent_name: str,
        promise_id: str,
        status: HitlOutcome,
        resolved_by: str | None,
        resolution: Any,
    ) -> HitlWriteResult: ...

    async def read_hitl_request(
        self, agent_name: str | None, promise_id: str
    ) -> HitlWriteResult: ...
