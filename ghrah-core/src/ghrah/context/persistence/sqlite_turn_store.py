# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次、审批与回滚的 SQLite 实现。

本类只依赖 ``transaction()``、``db_path`` 与 ``_apply_chain_changes()`` 三个入口，
由持有写连接的宿主提供：独立链库由 :class:`SqliteBackend` 自己承载，
受管消息运行时由同一存储域的消息存储承载。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import aiosqlite

from ghrah.context import hitl, recovery, turn
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.turn_store import HitlWriteResult, RollbackPreview, TurnRecord

if TYPE_CHECKING:
    from ghrah.context.branch import ActionBranch
    from ghrah.context.persistence.sqlite_backend import SqliteBackend
    from ghrah.context.turn_store import DeliverySettlement

    SettlementMethod = Callable[..., Awaitable[Any]]

__all__ = ["SqliteTurnStore"]


class SqliteTurnStore:
    """在消息存储的写连接上执行轮次事务。

    子类须提供 ``transaction()``、``db_path`` 与 ``_apply_chain_changes()``。
    """

    db_path: Any

    def transaction(self) -> AbstractAsyncContextManager[aiosqlite.Connection]:
        raise NotImplementedError  # pragma: no cover - 由宿主提供

    async def _apply_chain_changes(self, db: aiosqlite.Connection, changes: ContextChanges) -> None:
        raise NotImplementedError  # pragma: no cover - 由宿主提供

    def _read_only(self) -> SqliteBackend:
        from ghrah.context.persistence.sqlite_backend import SqliteBackend

        return SqliteBackend(self.db_path, read_only=True)

    @staticmethod
    def _in_transaction(
        settlement: DeliverySettlement | None,
    ) -> tuple[SettlementMethod | None, SettlementMethod | None, SettlementMethod | None]:
        """取得结算方的事务内入口；跨域或不支持同事务的结算方被拒绝。"""
        if settlement is None:
            return None, None, None
        methods: tuple[SettlementMethod | None, ...] = tuple(
            getattr(settlement, name, None)  # type: ignore[misc]
            for name in (
                "converge_in_transaction",
                "release_inputs_in_transaction",
                "pending_inputs_in_transaction",
            )
        )
        if not all(callable(item) for item in methods):
            raise TypeError("Delivery settlement does not share this storage domain")
        return methods  # type: ignore[return-value]

    async def load_turn(self, agent_name: str, turn_id: str) -> TurnRecord | None:
        """读取已提交的轮次记录。"""
        async with self._read_only() as reader:
            row = await reader.load_turn(agent_name, turn_id)
        return turn.turn_record(row) if row is not None else None

    async def has_active_turn(self, agent_name: str) -> bool:
        """检查该 Agent 是否已有持久活动轮次。"""
        async with self._read_only() as reader:
            async with reader.read_snapshot() as db:
                return await turn.has_active_turn(db, agent_name)

    async def open_turn(self, active: turn.ActiveTurn) -> str | None:
        """为直接收到的输入开启轮次。"""
        async with self.transaction() as db:
            now = datetime.now(UTC).isoformat()
            await turn.normalize_open_turns(db, agent_name=active.agent_name, now=now)
            await turn.insert_turn_placeholder(db, active, opened_at=now)
        return active.turn_id

    async def close_turn(
        self,
        *,
        turn_id: str,
        status: turn.TurnCloseStatus,
        closing_kind: str,
        end_node_id: str | None,
    ) -> None:
        """幂等终结轮次。"""
        async with self.transaction() as db:
            await turn.close_turn(
                db,
                turn_id=turn_id,
                status=status,
                closing_kind=closing_kind,
                end_node_id=end_node_id,
                closed_at=datetime.now(UTC).isoformat(),
            )

    async def commit_iteration(
        self, changes: ContextChanges, *, turn_id: str | None, idle_compact: bool
    ) -> None:
        """校验轮次执行权后与链增量一起提交。"""
        async with self.transaction() as db:
            if turn_id is not None:
                await turn.require_active(db, turn_id)
            elif idle_compact and await turn.has_active_turn(db, changes.agent_name):
                raise turn.CompactClaimedError("A turn was claimed during idle compaction")
            await self._apply_chain_changes(db, changes)
            if turn_id is not None:
                await turn.backfill_start(db, changes.nodes[0])

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
    ) -> recovery.ConvergenceOutcome:
        """在写事务内以持久现场裁定收敛，并同事务结算投递。"""
        async with self.transaction() as db:
            if turn_id is None:
                rows = await (
                    await db.execute(
                        "SELECT * FROM turns WHERE agent_name=? AND (? IS NULL OR session_id=?) "
                        "AND status IN ('open','waiting_hitl') LIMIT 2",
                        (agent_name, session_id, session_id),
                    )
                ).fetchall()
                if not rows:
                    raise ValueError("turn_not_found")
                if len(rows) != 1:
                    raise ValueError("ambiguous_active_turn")
                row = rows[0]
            else:
                row = await turn.load_turn_for_rollback(db, turn_id=turn_id, agent_name=agent_name)
                if session_id is not None and row["session_id"] != session_id:
                    raise ValueError("turn_not_found")
            if row["status"] in turn.TERMINAL_STATUSES:
                return recovery.ConvergenceOutcome(turn.turn_record(row))
            resolved_id = row["turn_id"]
            opener = row["opener_delivery_id"]
            if opener is not None and (
                settlement is not None
                or await (
                    await db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='mm_delivery_results'"
                    )
                ).fetchone()
            ):
                pending = await (
                    await db.execute(
                        "SELECT 1 FROM mm_delivery_results r JOIN mm_deliveries d "
                        "ON d.delivery_id=r.delivery_id WHERE d.delivery_id=? AND d.turn_id=?",
                        (opener, resolved_id),
                    )
                ).fetchone()
                if pending is not None:
                    return recovery.ConvergenceOutcome(turn.turn_record(row), result_pending=True)

            scene = await recovery.load_scene(db, row)
            phase = (
                "waiting_hitl"
                if scene.pending_hitl
                else execution_phase
                if active_turn_id == resolved_id
                else "unknown"
            )
            changes = await recovery.plan_convergence(
                db, row, scene, reason=reason, phase=phase, error=error
            )
            if changes is not None:
                await self._apply_chain_changes(db, changes)
            metadata = {
                "abort_reason": description,
                "abort_phase": phase,
                **recovery.diagnosis_metadata(changes),
            }
            opts = {
                "turn_id": resolved_id,
                "end_node_id": scene.last.id if scene.last is not None else None,
                "closed_at": datetime.now(UTC).isoformat(),
            }
            if status == "aborted":
                updated = await turn.abort_turn(
                    db, **opts, metadata_patch={**metadata, "aborted_by": "user"}
                )
            else:
                await db.execute(
                    "UPDATE turns SET metadata=json_patch(metadata,?) WHERE turn_id=?",
                    (json.dumps(metadata), resolved_id),
                )
                updated = await turn.close_turn(
                    db, **opts, status="failed", closing_kind=closing_kind
                )
            if updated is None:
                raise RuntimeError("Active turn changed during convergence")
            released = 0
            converge, _, _ = self._in_transaction(settlement)
            if converge is not None:
                delivery_state = "interrupted" if status == "aborted" else "failed"
                released = await converge(
                    db,
                    turn_id=resolved_id,
                    opener_delivery_id=opener,
                    opener_state=delivery_state,
                    terminal_state=delivery_state,
                    opener_error="aborted_by_user" if status == "aborted" else description,
                )
            return recovery.ConvergenceOutcome(
                turn.turn_record(updated),
                changes,
                changed=True,
                cancelled_hitl=scene.pending_hitl,
                released_deliveries=released,
            )

    async def preview_rollback(
        self,
        *,
        agent_name: str,
        turn_id: str,
        target: turn.RollbackTarget,
        node_id: str | None,
        settlement: DeliverySettlement | None,
    ) -> RollbackPreview:
        """在只读快照内计算回滚目标与影响。"""
        _, _, pending_inputs = self._in_transaction(settlement)
        async with self._read_only() as reader:
            async with reader.read_snapshot() as db:
                row = await turn.load_turn_for_rollback(db, turn_id=turn_id, agent_name=agent_name)
                point = await turn.resolve_rollback_point(db, row, target, node_id)
                truncated, crosses_compact = await turn.count_truncated_nodes(
                    db, row, target_node_id=point.node_id
                )
                released = (
                    await pending_inputs(
                        db, turn_id=turn_id, opener_delivery_id=row["opener_delivery_id"]
                    )
                    if pending_inputs is not None
                    else []
                )
        return RollbackPreview(
            turn_id=turn_id,
            session_id=row["session_id"],
            target=target,
            target_node_id=point.node_id,
            target_branch_id=point.branch_id,
            target_is_compact=point.target_is_compact,
            base_node_id=row["base_node_id"],
            start_node_id=row["start_node_id"],
            end_node_id=row["end_node_id"],
            truncated_node_count=truncated,
            crosses_compact=crosses_compact,
            head_unchanged=point.previous_head_id == point.node_id,
            released_delivery_ids=tuple(released),
        )

    async def rollback_turn(
        self,
        *,
        agent_name: str,
        turn_id: str,
        target: turn.RollbackTarget,
        node_id: str | None,
        create_branch: Callable[[turn.RollbackPoint, str], tuple[ActionBranch, ContextChanges]],
        settlement: DeliverySettlement | None,
    ) -> turn.RollbackOutcome:
        """在同一事务内认领回滚、提交新分支并解绑输入；重放返回原记录。"""
        _, release_inputs, _ = self._in_transaction(settlement)
        async with self.transaction() as db:
            row = await turn.load_turn_for_rollback(db, turn_id=turn_id, agent_name=agent_name)
            if row["status"] == "rolled_back":
                return turn.replay_rollback(row, target, node_id)
            point = await turn.resolve_rollback_point(db, row, target, node_id)
            branch, changes = create_branch(point, row["session_id"])
            await turn.rollback_turn(
                db,
                turn_id=turn_id,
                agent_name=agent_name,
                target=target,
                point=point,
                new_branch_id=branch.branch_id,
                rolled_back_at=datetime.now(UTC).isoformat(),
            )
            await self._apply_chain_changes(db, changes)
            released = (
                await release_inputs(
                    db, turn_id=turn_id, opener_delivery_id=row["opener_delivery_id"]
                )
                if release_inputs is not None
                else []
            )
            row = await turn.record_released_inputs(
                db, turn_id=turn_id, released_delivery_ids=released
            )
            return turn.RollbackOutcome(turn.turn_record(row), branch)

    async def normalize_turns(self, agent_name: str) -> None:
        """恢复上下文时归一属于该 Agent 的遗留轮次。"""
        async with self.transaction() as db:
            await turn.normalize_open_turns(
                db, agent_name=agent_name, now=datetime.now(UTC).isoformat()
            )

    async def apply_pending_diagnoses(self, agent_name: str) -> None:
        """为遗留轮次补写诊断节点并归一。"""
        async with self.transaction() as db:
            async with db.execute(
                "SELECT * FROM turns WHERE agent_name=? AND "
                "(status IN ('open','waiting_hitl') OR "
                "json_type(metadata,'$.crash_diagnosis')='object') ORDER BY opened_at",
                (agent_name,),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"])
                marker = metadata.pop("crash_diagnosis", None)
                scene = await recovery.load_scene(db, row)
                phase = (
                    marker["phase"]
                    if marker
                    else ("waiting_hitl" if scene.pending_hitl else "unknown")
                )
                changes = await recovery.plan_convergence(
                    db, row, scene, reason="crash", phase=phase, crash_marker=marker
                )
                if changes is not None:
                    await self._apply_chain_changes(db, changes)
                metadata.update(recovery.diagnosis_metadata(changes))
                await db.execute(
                    "UPDATE turns SET metadata=? WHERE turn_id=?",
                    (json.dumps(metadata), row["turn_id"]),
                )
            await turn.normalize_open_turns(
                db, agent_name=agent_name, now=datetime.now(UTC).isoformat()
            )

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
    ) -> HitlWriteResult:
        """登记待审批请求，与轮次等待状态同事务提交。"""
        async with self.transaction() as db:
            await turn.require_active(db, turn_id)
            now = datetime.now(UTC).isoformat()
            row = await hitl.insert_pending(
                db,
                turn_id=turn_id,
                promise_id=promise_id,
                ability_name=ability_name,
                tool_call_id=tool_call_id,
                tool_call=tool_call,
                reason=reason,
                options=options,
                last_node_id=last_node_id,
                now=now,
                deadline_at=deadline_at,
            )
            if row is not None:
                changed = await turn.set_waiting_hitl(db, turn_id, now)
                return HitlWriteResult(
                    persisted=True,
                    changed=changed,
                    turn_id=turn_id,
                    status="pending",
                    session_id=row["session_id"],
                    delivery_id=row["delivery_id"],
                )
        raise turn.TurnRevokedError("turn is no longer active")

    async def finish_hitl_request(
        self,
        *,
        agent_name: str,
        promise_id: str,
        status: hitl.HitlOutcome,
        resolved_by: str | None,
        resolution: Any,
    ) -> HitlWriteResult:
        """原子记录人工审批结果，最后一条待审批解决后恢复轮次。"""
        async with self.transaction() as db:
            result = await hitl.resolve_cas(
                db,
                agent_name=agent_name,
                promise_id=promise_id,
                status=status,
                resolved_by=resolved_by,
                resolution=resolution,
                now=datetime.now(UTC).isoformat(),
            )
            if result.changed and result.turn_id is not None:
                await turn.set_open(db, result.turn_id)
        return HitlWriteResult(
            persisted=result.persisted,
            changed=result.changed,
            turn_id=result.turn_id,
            status=result.status,
            resolution=result.resolution,
        )

    async def read_hitl_request(self, agent_name: str, promise_id: str) -> HitlWriteResult:
        """用独立只读连接读取已提交的审批，避免观察到写事务中的状态。"""
        async with self._read_only() as reader:
            row = await reader.load_hitl_request(agent_name, promise_id)
        if row is not None:
            return HitlWriteResult(
                persisted=True,
                turn_id=row["turn_id"],
                status=row["status"],
                resolution=json.loads(row["resolution"]),
            )
        return HitlWriteResult()
