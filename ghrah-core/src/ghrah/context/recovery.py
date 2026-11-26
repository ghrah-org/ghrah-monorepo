# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""以持久轮次、节点和审批事实为依据，计划上下文收敛。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Protocol

import aiosqlite

from ghrah.context import turn
from ghrah.context.branch import ActionBranch
from ghrah.context.node import ContextNode
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.serialization import deserialize_action_session, deserialize_branch
from ghrah.context.turn_store import TurnRecord


class SettleDeliveries(Protocol):
    async def __call__(
        self,
        db: aiosqlite.Connection,
        *,
        turn_id: str,
        opener_delivery_id: str | None,
        opener_state: str,
        opener_error: str,
        terminal_state: str,
    ) -> int: ...


@dataclass(frozen=True)
class SceneNode:
    """轮次最后一个有效成员节点的持久事实。"""

    id: str
    iteration: int
    created_on_branch_id: str
    agent_state: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Scene:
    """轮次结束前可重建的不完整现场。"""

    last: SceneNode | None
    pending_hitl: int
    tool_ids: list[str]
    pending_tools: bool

    @property
    def incomplete(self) -> bool:
        return self.last is not None and bool(self.pending_hitl or self.pending_tools)


@dataclass(frozen=True)
class ConvergenceOutcome:
    """一次收敛后的权威回执与可选链变更。"""

    record: TurnRecord
    changes: ContextChanges | None = None
    changed: bool = False
    result_pending: bool = False
    cancelled_hitl: int = 0
    released_deliveries: int = 0

    def as_payload(self) -> dict[str, Any]:
        metadata = self.record.metadata
        return {
            "turn_id": self.record.turn_id,
            "status": self.record.status,
            "closing_kind": self.record.closing_kind,
            "opener_delivery_id": self.record.opener_delivery_id,
            "aborted": self.changed and self.record.status == "aborted",
            "already_terminal": self.record.status in turn.TERMINAL_STATUSES
            and not self.changed
            and not self.result_pending,
            "result_pending": self.result_pending,
            "cancelled_hitl": self.cancelled_hitl,
            "released_deliveries": self.released_deliveries,
            "incomplete_node_id": metadata.get("incomplete_node_id"),
            "recovery_branch_id": metadata.get("recovery_branch_id"),
        }


async def load_scene(db: aiosqlite.Connection, row: aiosqlite.Row) -> Scene:
    """读取轮次的最后有效成员和未完成工作凭证。"""
    async with db.execute(
        f"SELECT id, iteration, created_on_branch_id, agent_state, metadata FROM nodes "
        f"WHERE turn_id=? AND agent_name=? AND session_id=? AND {turn.NOT_INCOMPLETE_SQL} "
        "ORDER BY rowid DESC LIMIT 1",
        (row["turn_id"], row["agent_name"], row["session_id"]),
    ) as cursor:
        last = await cursor.fetchone()
    async with db.execute(
        "SELECT COUNT(*) FROM hitl_requests WHERE turn_id=? AND status='pending'", (row["turn_id"],)
    ) as cursor:
        count = await cursor.fetchone()
    assert count is not None
    metadata = json.loads(last["metadata"]) if last is not None else {}
    tool_ids = metadata.get("pending_tool_call_ids") or []
    node = (
        SceneNode(
            id=last["id"],
            iteration=last["iteration"],
            created_on_branch_id=last["created_on_branch_id"],
            agent_state=json.loads(last["agent_state"]),
            metadata=metadata,
        )
        if last is not None
        else None
    )
    return Scene(node, count[0], tool_ids, bool(tool_ids or metadata.get("pending_tool_replies")))


async def plan_convergence(
    db: aiosqlite.Connection,
    row: aiosqlite.Row,
    scene: Scene,
    *,
    reason: str,
    phase: str,
    error: Exception | None = None,
    crash_marker: dict[str, Any] | None = None,
) -> ContextChanges | None:
    """仅在存在持久证据且会话仍停在恢复点时构造诊断与分支。"""
    last = scene.last
    if last is None or row["start_node_id"] is None or not (scene.incomplete or crash_marker):
        return None
    if crash_marker and crash_marker["last_correct_node_id"] != last.id:
        return None
    async with db.execute(
        "SELECT * FROM sessions WHERE agent_name=? AND session_id=? AND lifecycle='open'",
        (row["agent_name"], row["session_id"]),
    ) as cursor:
        session_row = await cursor.fetchone()
    if session_row is None:
        return None
    session = deserialize_action_session(dict(session_row))
    async with db.execute(
        "SELECT * FROM branches WHERE branch_id=? AND session_id=? AND lifecycle='open' "
        "AND head_node_id=?",
        (session.active_branch_id, session.session_id, last.id),
    ) as cursor:
        branch_row = await cursor.fetchone()
    if branch_row is None:
        return None
    old = deserialize_branch(dict(branch_row))
    node = ContextNode(
        parent_id=last.id,
        agent_name=row["agent_name"],
        session_id=session.session_id,
        created_on_branch_id=old.branch_id,
        iteration=last.iteration + 1,
        ability_names=[turn.INCOMPLETE_KIND],
        agent_state=last.agent_state,
        turn_id=row["turn_id"],
        metadata={
            "node_kind": turn.INCOMPLETE_KIND,
            "is_diagnosis": True,
            "incomplete_reason": reason,
            "phase": phase,
            "last_correct_node_id": last.id,
            "turn_id": row["turn_id"],
            "pending_tool_call_ids": (
                crash_marker["pending_tool_call_ids"] if crash_marker else scene.tool_ids
            ),
            "error": str(error) if error is not None else None,
            "error_type": type(error).__name__ if error is not None else None,
        },
    )
    branch = ActionBranch.create_fork(
        session_id=session.session_id,
        name=f"recovery-{row['turn_id'][:8]}",
        head_node_id=last.id,
        parent_branch_id=old.branch_id,
        fork_point_node_id=last.id,
        metadata={
            "is_rollback": True,
            "trigger": reason,
            "turn_id": row["turn_id"],
            "rollback_from_branch_id": old.branch_id,
            "rollback_from_node_id": node.id,
            "rollback_to_node_id": last.id,
        },
    )
    return ContextChanges(
        agent_name=row["agent_name"],
        nodes=(node,),
        branches=(replace(old, head_node_id=node.id), branch),
        sessions=(replace(session, active_branch_id=branch.branch_id),),
    )


def diagnosis_metadata(changes: ContextChanges | None) -> dict[str, str]:
    """提取需要写入轮次回执的诊断引用。"""
    if changes is None:
        return {}
    return {
        "incomplete_node_id": changes.nodes[0].id,
        "recovery_branch_id": changes.sessions[0].active_branch_id,
    }


async def mark_crash_scenes(db: aiosqlite.Connection) -> None:
    """在归一审批前记录可证现场，供 ContextManager 恢复。"""
    async with db.execute("SELECT * FROM turns WHERE status IN ('open','waiting_hitl')") as cursor:
        rows = await cursor.fetchall()
    for row in rows:
        scene = await load_scene(db, row)
        if row["start_node_id"] is None or not scene.incomplete or scene.last is None:
            continue
        marker = {
            "reason": "crash",
            "phase": "waiting_hitl" if scene.pending_hitl else "unknown",
            "last_correct_node_id": scene.last.id,
            "pending_tool_call_ids": scene.tool_ids,
        }
        await db.execute(
            "UPDATE turns SET metadata=json_set(metadata,'$.crash_diagnosis',json(?)) "
            "WHERE turn_id=?",
            (json.dumps(marker), row["turn_id"]),
        )
