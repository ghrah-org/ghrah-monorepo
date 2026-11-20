# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从投递、轮次与结果权威表派生顺序门和队列位置。"""

from __future__ import annotations

from typing import Any

import aiosqlite

from ghrah.context.turn import TERMINAL_STATUSES

_TURN_INPUT_WHERE = """
turn_id=? AND delivery_id IS NOT ? AND state='pending' AND consumed_node_id IS NULL
AND NOT EXISTS(SELECT 1 FROM mm_delivery_results r WHERE r.delivery_id=mm_deliveries.delivery_id)
"""
QUEUED_SQL = "state='pending' AND turn_id IS NULL AND consumed_node_id IS NULL AND parked=0"
_UNOWNED_INLINE_WAIT = (
    "state='waiting' AND EXISTS (SELECT 1 FROM mm_deliveries d "
    "WHERE d.delivery_id=parent_delivery_id AND d.state!='processing' "
    "AND NOT (d.state='pending' AND d.resume_requested=1))"
)


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
            "AND current.turn_id IS NULL AND prior.parked=0 "
            f"AND ({reason}) IS NOT NULL ORDER BY prior.seq LIMIT 1",
            (delivery_id,),
        )
    ).fetchone()


async def first_queue_blocker(
    db: aiosqlite.Connection,
    *,
    recipient_id: str,
    session_id: str | None,
    with_turns: bool,
) -> aiosqlite.Row | None:
    """在整个会话队列中找一次最早的阻塞投递，供按序投影复用。"""
    reason = blocker_reason_sql(with_turns)
    async with db.execute(
        "SELECT prior.delivery_id, prior.seq, "
        f"{reason} AS queue_block_reason FROM mm_deliveries prior "
        + blocker_joins(with_turns)
        + "WHERE prior.recipient_id=? AND prior.target_session_id IS ? AND prior.parked=0 "
        f"AND ({reason}) IS NOT NULL ORDER BY prior.seq LIMIT 1",
        (recipient_id, session_id),
    ) as cursor:
        return await cursor.fetchone()


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


def delivery_projection(
    row: aiosqlite.Row, *, position: int, blocker: aiosqlite.Row | None
) -> dict[str, Any]:
    """从同一快照中的记录和顺序门证据派生投递视图。"""
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
    item["queue_position"] = position
    item["queue_reason"] = blocker["queue_block_reason"] if blocker is not None else None
    item["blocked_by_delivery_id"] = blocker["delivery_id"] if blocker is not None else None
    if state == "finalizing":
        item["queue_reason"] = "completion_pending"
    elif item.get("blocked_reason"):
        item["queue_reason"] = item["blocked_reason"]
    if item.get("blocked_reason"):
        item["state"] = "blocked"
    return item


async def project_delivery(db: aiosqlite.Connection, row: aiosqlite.Row) -> dict[str, Any]:
    """读取单条投递的队列位置和阻塞者。"""
    if row["state"] != "pending" or row["turn_id"] is not None:
        return delivery_projection(row, position=0, blocker=None)
    async with db.execute(
        "SELECT COUNT(*) FROM mm_deliveries WHERE recipient_id=? "
        f"AND target_session_id IS ? AND seq<=? AND {QUEUED_SQL}",
        (row["recipient_id"], row["target_session_id"], row["seq"]),
    ) as cursor:
        position = await cursor.fetchone()
    assert position is not None
    blocker = await find_blocker(db, row["delivery_id"])
    return delivery_projection(row, position=position[0], blocker=blocker)


async def pending_turn_input_ids(
    db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
) -> list[str]:
    """按原顺序读取可释放的输入。"""
    async with db.execute(
        f"SELECT delivery_id FROM mm_deliveries WHERE {_TURN_INPUT_WHERE} ORDER BY seq",
        (turn_id, opener_delivery_id),
    ) as cursor:
        return [row[0] for row in await cursor.fetchall()]


async def release_turn_inputs(
    db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None
) -> list[str]:
    """在宿主事务内解绑未消费输入，排除 opener 与已持久化结果。"""
    async with db.execute(
        f"UPDATE mm_deliveries SET turn_id=NULL, delivery_policy='queue', error=NULL "
        f"WHERE {_TURN_INPUT_WHERE} RETURNING delivery_id, seq",
        (turn_id, opener_delivery_id),
    ) as cursor:
        rows = await cursor.fetchall()
    return [row[0] for row in sorted(rows, key=lambda row: row[1])]


async def settle_turn_deliveries(
    db: aiosqlite.Connection, *, turn_id: str, opener_delivery_id: str | None, terminal_state: str
) -> list[str]:
    """在关闭事务内释放未消费注入并终结已消费注入。"""
    await db.execute(
        "UPDATE mm_deliveries SET state=? WHERE turn_id=? AND delivery_id IS NOT ? "
        "AND state='pending' AND consumed_node_id IS NOT NULL "
        "AND NOT EXISTS(SELECT 1 FROM mm_delivery_results r "
        "WHERE r.delivery_id=mm_deliveries.delivery_id)",
        (terminal_state, turn_id, opener_delivery_id),
    )
    return await release_turn_inputs(db, turn_id=turn_id, opener_delivery_id=opener_delivery_id)


async def settle_turn_opener(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    opener_delivery_id: str | None,
    state: str,
    error: str,
) -> None:
    """收敛仍归属此轮的首条投递，保留已落库结果和已申请续跑。"""
    if opener_delivery_id is None:
        return
    await db.execute(
        "UPDATE mm_deliveries SET state=?,error=? WHERE delivery_id=? AND turn_id=? "
        "AND state IN ('processing','pending') AND resume_requested=0 "
        "AND NOT EXISTS (SELECT 1 FROM mm_delivery_results r "
        "WHERE r.delivery_id=mm_deliveries.delivery_id)",
        (state, error, opener_delivery_id, turn_id),
    )


async def release_unowned_inline_waits(
    db: aiosqlite.Connection, *, parent_delivery_id: str | None = None
) -> None:
    """将失去执行方的等待释放到普通邮箱，保留执行中或已预约续跑的等待。"""
    parent_filter = "" if parent_delivery_id is None else " AND parent_delivery_id=?"
    params = () if parent_delivery_id is None else (parent_delivery_id,)
    await db.execute(
        f"UPDATE mm_inline_waits SET state='released' WHERE {_UNOWNED_INLINE_WAIT}{parent_filter}",
        params,
    )


def _terminal_turn_candidates_sql() -> str:
    """用少量绑定中投递驱动查询，避免从历史轮次反查全库。"""
    statuses = TERMINAL_STATUSES
    placeholders = ",".join("?" for _ in statuses)
    return (
        "FROM mm_deliveries d INDEXED BY mm_bound_inflight "
        "CROSS JOIN turns t ON d.turn_id=t.turn_id "
        "WHERE d.turn_id IS NOT NULL AND d.state IN ('pending','processing') "
        f"AND t.status IN ({placeholders}) AND ("
        "(d.delivery_id IS NOT t.opener_delivery_id AND d.state='pending') OR "
        "(d.delivery_id=t.opener_delivery_id AND d.resume_requested=0)) AND NOT EXISTS "
        "(SELECT 1 FROM mm_delivery_results r WHERE r.delivery_id=d.delivery_id)"
    )


async def needs_delivery_repair(db: aiosqlite.Connection, *, with_turns: bool) -> bool:
    """空闲时在只读路径检查是否需要开启收敛写事务。"""
    terminal = f" OR EXISTS (SELECT 1 {_terminal_turn_candidates_sql()})" if with_turns else ""
    async with db.execute(
        f"SELECT EXISTS (SELECT 1 FROM mm_inline_waits WHERE {_UNOWNED_INLINE_WAIT}){terminal}",
        TERMINAL_STATUSES if with_turns else (),
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row and row[0])


async def settle_terminal_turns(db: aiosqlite.Connection) -> None:
    """幂等修复终态轮次上的首条投递与残留注入。"""
    if not await has_turns(db):
        return
    rows = await db.execute_fetchall(
        "SELECT DISTINCT t.turn_id, t.opener_delivery_id, t.status "
        + _terminal_turn_candidates_sql(),
        TERMINAL_STATUSES,
    )
    for row in rows:
        status = row["status"]
        await settle_turn_opener(
            db,
            turn_id=row["turn_id"],
            opener_delivery_id=row["opener_delivery_id"],
            state="failed" if status == "failed" else "interrupted",
            error="aborted_by_user" if status == "aborted" else f"turn_{status}_without_result",
        )
        await settle_turn_deliveries(
            db,
            turn_id=row["turn_id"],
            opener_delivery_id=row["opener_delivery_id"],
            terminal_state={"closed": "completed", "failed": "failed"}.get(status, "interrupted"),
        )
