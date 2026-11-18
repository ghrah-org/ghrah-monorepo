# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从投递、轮次与结果权威表派生顺序门和队列位置。"""

from __future__ import annotations

from typing import Any

import aiosqlite


async def has_turns(db: aiosqlite.Connection) -> bool:
    """兼容仅有消息表的回调模式。"""
    return (
        await (
            await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'")
        ).fetchone()
        is not None
    )


def blocker_reason_sql(with_turns: bool) -> str:
    """调度器与读面共用的前序阻塞判定，别名固定为 prior、t、r。"""
    turn_rules = (
        "WHEN prior.turn_id IS NOT NULL AND t.turn_id IS NULL THEN 'turn_missing' "
        "WHEN t.status IN ('open','waiting_hitl') THEN 'turn_active' "
        "WHEN prior.turn_id IS NOT NULL AND prior.state='pending' "
        "AND prior.consumed_node_id IS NULL THEN 'turn_terminal_pending_input' "
        if with_turns
        else "WHEN prior.turn_id IS NOT NULL THEN 'turn_missing' "
    )
    return (
        "CASE WHEN r.delivery_id IS NOT NULL AND prior.state!='completed' "
        "THEN 'completion_pending' "
        + turn_rules
        + "WHEN prior.state NOT IN ('completed','failed','interrupted') THEN "
        "CASE WHEN prior.blocked_reason IS NOT NULL THEN prior.blocked_reason "
        "ELSE prior.state END END"
    )


def blocker_joins(with_turns: bool) -> str:
    """顺序门所需的只读关联。"""
    return (
        "LEFT JOIN turns t ON t.turn_id=prior.turn_id " if with_turns else ""
    ) + "LEFT JOIN mm_delivery_results r ON r.delivery_id=prior.delivery_id "


async def find_blocker(db: aiosqlite.Connection, delivery_id: str) -> aiosqlite.Row | None:
    """返回同队列中首个真正阻塞的前序及原因。"""
    with_turns = await has_turns(db)
    reason = blocker_reason_sql(with_turns)
    return await (
        await db.execute(
            "SELECT prior.*, r.delivery_id AS result_id, r.result_node_id, "
            f"{reason} AS queue_block_reason "
            "FROM mm_deliveries current JOIN mm_deliveries prior "
            "ON prior.recipient_id=current.recipient_id "
            "AND prior.target_session_id IS current.target_session_id AND prior.seq<current.seq "
            + blocker_joins(with_turns)
            + "WHERE current.delivery_id=? AND current.state='pending' "
            "AND current.turn_id IS NULL AND COALESCE(prior.parked,0)=0 "
            f"AND ({reason}) IS NOT NULL ORDER BY prior.seq LIMIT 1",
            (delivery_id,),
        )
    ).fetchone()


def display_state(item: dict[str, Any]) -> str:
    """结果证据优先的展示状态。"""
    state = item["state"]
    if item.get("result_ready") and state != "completed":
        return "finalizing"
    if state == "interrupted" and item.get("error") == "cancelled_by_user":
        return "cancelled"
    if item.get("blocked_reason"):
        return "blocked"
    if state == "failed" and item.get("parked"):
        return "parked"
    if state == "pending":
        if item.get("consumed_node_id"):
            return "consumed"
        return "injected" if item.get("turn_id") else "queued"
    return state


async def project_delivery(db: aiosqlite.Connection, row: aiosqlite.Row) -> dict[str, Any]:
    """一次查询快照内计算展示状态、位置、阻塞原因与取消资格。"""
    item = dict(row)
    item.pop("_sender", None)
    state = display_state(item)
    item["display_state"] = state
    cancelable = (
        item["state"] == "pending"
        and not item.get("turn_id")
        and not item.get("consumed_node_id")
        and not item.get("result_ready")
    )
    item["cancelable"] = cancelable
    item["queue_position"] = 0
    item["queue_reason"] = None
    item["blocked_by_delivery_id"] = None
    if item["state"] == "pending" and item.get("turn_id") is None:
        position = await (
            await db.execute(
                "SELECT COUNT(*) FROM mm_deliveries WHERE recipient_id=? "
                "AND target_session_id IS ? AND seq<=? AND state='pending' "
                "AND turn_id IS NULL AND consumed_node_id IS NULL AND parked=0",
                (item["recipient_id"], item["target_session_id"], item["seq"]),
            )
        ).fetchone()
        item["queue_position"] = position[0]
        blocker = await find_blocker(db, item["delivery_id"])
        if blocker is not None:
            item["queue_reason"] = blocker["queue_block_reason"]
            item["blocked_by_delivery_id"] = blocker["delivery_id"]
    if state == "finalizing":
        item["queue_reason"] = "completion_pending"
    elif item.get("blocked_reason"):
        item["queue_reason"] = item["blocked_reason"]
    if item.get("blocked_reason"):
        item["state"] = "blocked"
    return item


async def settle_turn_deliveries(
    db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None, terminal_state: str
) -> None:
    """在关闭事务内释放未消费注入并终结已消费注入。"""
    await db.execute(
        "UPDATE mm_deliveries SET state=? WHERE turn_id=? AND delivery_id IS NOT ? "
        "AND state='pending' AND consumed_node_id IS NOT NULL "
        "AND NOT EXISTS(SELECT 1 FROM mm_delivery_results r "
        "WHERE r.delivery_id=mm_deliveries.delivery_id)",
        (terminal_state, turn_id, opener_delivery_id),
    )
    await db.execute(
        "UPDATE mm_deliveries SET turn_id=NULL, delivery_policy='queue' "
        "WHERE turn_id=? AND delivery_id IS NOT ? AND state='pending' "
        "AND consumed_node_id IS NULL AND NOT EXISTS(SELECT 1 FROM mm_delivery_results r "
        "WHERE r.delivery_id=mm_deliveries.delivery_id)",
        (turn_id, opener_delivery_id),
    )


async def settle_terminal_turns(db: aiosqlite.Connection) -> None:
    """幂等修复终态轮次上的残留输入。"""
    if not await has_turns(db):
        return
    rows = await (
        await db.execute(
            "SELECT DISTINCT t.turn_id, t.opener_delivery_id, t.status FROM turns t "
            "JOIN mm_deliveries d ON d.turn_id=t.turn_id "
            "WHERE t.status NOT IN ('open','waiting_hitl') AND d.state='pending'"
        )
    ).fetchall()
    for row in rows:
        state = {"closed": "completed", "failed": "failed"}.get(row["status"], "interrupted")
        await settle_turn_deliveries(
            db,
            turn_id=row["turn_id"],
            opener_delivery_id=row["opener_delivery_id"],
            terminal_state=state,
        )
