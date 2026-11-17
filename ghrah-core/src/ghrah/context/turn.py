# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次的持久写入。调用方持有所在数据库的事务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import aiosqlite

from ghrah.context import hitl
from ghrah.context.node import ContextNode

TurnTerminalStatus = Literal["closed", "failed", "interrupted"]
_ACTIVE = "status IN ('open', 'waiting_hitl')"


@dataclass(frozen=True)
class ActiveTurn:
    """执行期间的轮次引用，锚点由数据库保存。"""

    turn_id: str
    agent_name: str
    session_id: str
    opener_delivery_id: str | None
    owner: Literal["mm", "self"]


async def insert_turn_placeholder(
    db: aiosqlite.Connection, turn: ActiveTurn, *, opened_at: str
) -> None:
    """在执行前登记轮次，提交首个有效节点时再补锚点。"""
    await db.execute(
        "INSERT INTO turns (turn_id, agent_name, session_id, "
        "opener_delivery_id, status, opened_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (turn.turn_id, turn.agent_name, turn.session_id, turn.opener_delivery_id, opened_at),
    )


async def backfill_start(db: aiosqlite.Connection, node: ContextNode) -> None:
    """从首个普通节点回填起点，并跳过轮内 compact 祖先。"""
    if node.metadata.get("node_kind") == "compact":
        return
    await db.execute(
        """
        WITH RECURSIVE ancestors(id, parent_id, turn_id) AS (
            SELECT id, parent_id, turn_id FROM nodes WHERE id=?
            UNION ALL
            SELECT n.id, n.parent_id, n.turn_id
            FROM nodes n JOIN ancestors a ON n.id=a.parent_id
            WHERE a.turn_id=?
        )
        UPDATE turns SET start_node_id=?, branch_id=?,
            base_node_id=(SELECT id FROM ancestors WHERE turn_id IS NOT ? LIMIT 1)
        WHERE turn_id=? AND start_node_id IS NULL AND status IN ('open', 'waiting_hitl')
        """,
        (
            node.parent_id,
            node.turn_id,
            node.id,
            node.created_on_branch_id,
            node.turn_id,
            node.turn_id,
        ),
    )


async def set_waiting_hitl(db: aiosqlite.Connection, turn_id: str, now: str) -> bool:
    """开始一次审批等待阶段，返回本次是否发生状态转换。"""
    cursor = await db.execute(
        "UPDATE turns SET status='waiting_hitl', waiting_since=? WHERE turn_id=? AND status='open'",
        (now, turn_id),
    )
    return cursor.rowcount == 1


async def set_open(db: aiosqlite.Connection, turn_id: str) -> None:
    """最后一条待审批项解决之后恢复轮次。"""
    await db.execute(
        "UPDATE turns SET status='open', waiting_since=NULL "
        "WHERE turn_id=? AND status='waiting_hitl' AND NOT EXISTS "
        "(SELECT 1 FROM hitl_requests WHERE turn_id=? AND status='pending')",
        (turn_id, turn_id),
    )


async def close_turn(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    status: TurnTerminalStatus,
    closing_kind: str,
    end_node_id: str | None,
    closed_at: str,
) -> aiosqlite.Row | None:
    """幂等终结轮次，同时清理仍待审批的请求。"""
    anchors = await (
        await db.execute(
            "SELECT t.start_node_id, s.active_branch_id, b.head_node_id "
            "FROM turns t LEFT JOIN sessions s "
            "ON s.session_id=t.session_id AND s.agent_name=t.agent_name "
            "LEFT JOIN branches b ON b.branch_id=s.active_branch_id AND b.session_id=t.session_id "
            f"WHERE t.turn_id=? AND t.{_ACTIVE}",
            (turn_id,),
        )
    ).fetchone()
    if anchors is None:
        return None
    branch = anchors["active_branch_id"]
    head = anchors["head_node_id"]
    if anchors["start_node_id"] is not None:
        node = await (
            await db.execute(
                "SELECT id, created_on_branch_id FROM nodes WHERE turn_id=? "
                "ORDER BY (id IS ?) DESC, rowid DESC LIMIT 1",
                (turn_id, end_node_id),
            )
        ).fetchone()
        if node is not None:
            head, branch = node
    cursor = await db.execute(
        f"""
        UPDATE turns SET status=?, closing_kind=?, closed_at=?, waiting_since=NULL,
            end_node_id=?, end_branch_id=?,
            base_node_id=CASE WHEN start_node_id IS NULL THEN ? ELSE base_node_id END,
            branch_id=CASE WHEN start_node_id IS NULL THEN ? ELSE branch_id END
        WHERE turn_id=? AND {_ACTIVE}
        RETURNING *
        """,
        (status, closing_kind, closed_at, head, branch, head, branch, turn_id),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is not None:
        await db.execute(
            "UPDATE hitl_requests SET status='cancelled', resolved_at=? "
            "WHERE turn_id=? AND status='pending'",
            (closed_at, turn_id),
        )
    return row


async def normalize_open_turns(
    db: aiosqlite.Connection,
    *,
    now: str,
    agent_name: str | None = None,
    session_id: str | None = None,
) -> None:
    """归一已停止的执行，仅补写本次收敛的活动轮次。"""
    await hitl.orphan_all(db, now=now, agent_name=agent_name, session_id=session_id)
    rows = await (
        await db.execute(
            f"SELECT turn_id FROM turns WHERE {_ACTIVE} "
            "AND (? IS NULL OR agent_name=?) AND (? IS NULL OR session_id=?)",
            (agent_name, agent_name, session_id, session_id),
        )
    ).fetchall()
    for row in rows:
        await close_turn(
            db,
            turn_id=row["turn_id"],
            status="interrupted",
            closing_kind="cancelled",
            end_node_id=None,
            closed_at=now,
        )
