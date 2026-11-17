# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""轮次审批请求的持久状态。调用方持有事务。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import aiosqlite

HitlOutcome = Literal["approved", "rejected", "expired"]


@dataclass(frozen=True)
class HITLWriteResult:
    """持久审批的结果，供执行侧区分已解决、未持久化与成功转换。"""

    persisted: bool = False
    changed: bool = False
    turn_id: str | None = None
    status: str | None = None
    resolution: Any = None
    session_id: str | None = None
    delivery_id: str | None = None


async def insert_pending(
    db: aiosqlite.Connection,
    *,
    turn_id: str,
    promise_id: str,
    ability_name: str,
    tool_call_id: str,
    tool_call: dict[str, Any],
    reason: str,
    options: dict[str, Any],
    last_node_id: str | None,
    now: str,
    deadline_at: str | None,
) -> aiosqlite.Row | None:
    """从活动轮次登记待审批请求，继承租户与投递归属。"""
    cursor = await db.execute(
        """
        INSERT INTO hitl_requests (
            request_id, promise_id, agent_name, session_id, turn_id, delivery_id,
            last_node_id, ability_name, tool_call_id, tool_call, reason, options,
            status, created_at, deadline_at
        )
        SELECT ?, ?, agent_name, session_id, turn_id, opener_delivery_id,
            ?, ?, ?, ?, ?, ?, 'pending', ?, ?
        FROM turns WHERE turn_id=? AND status IN ('open','waiting_hitl')
        RETURNING session_id, delivery_id
        """,
        (
            promise_id,
            promise_id,
            last_node_id,
            ability_name,
            tool_call_id,
            json.dumps(tool_call, ensure_ascii=False),
            reason,
            json.dumps(options, ensure_ascii=False),
            now,
            deadline_at,
            turn_id,
        ),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def resolve_cas(
    db: aiosqlite.Connection,
    *,
    agent_name: str,
    promise_id: str,
    status: HitlOutcome,
    resolved_by: str | None,
    resolution: Any,
    now: str,
) -> HITLWriteResult:
    """仅转换 pending 请求，已处理时返回当前权威结果。"""
    cursor = await db.execute(
        "UPDATE hitl_requests SET status=?, resolved_by=?, resolution=?, resolved_at=? "
        "WHERE agent_name=? AND promise_id=? AND status='pending' "
        "RETURNING turn_id, status",
        (
            status,
            resolved_by,
            json.dumps(resolution, ensure_ascii=False),
            now,
            agent_name,
            promise_id,
        ),
    )
    updated = await cursor.fetchone()
    await cursor.close()
    if updated is not None:
        return HITLWriteResult(True, True, updated["turn_id"], status, resolution)
    row = await (
        await db.execute(
            "SELECT turn_id, status, resolution FROM hitl_requests "
            "WHERE agent_name=? AND promise_id=?",
            (agent_name, promise_id),
        )
    ).fetchone()
    if row is None:
        return HITLWriteResult()
    return HITLWriteResult(
        True, False, row["turn_id"], row["status"], json.loads(row["resolution"])
    )


async def orphan_all(
    db: aiosqlite.Connection, *, now: str, agent_name: str | None, session_id: str | None
) -> None:
    """将崩溃前超期的审批标为过期，其余待审批标为孤儿。"""
    await db.execute(
        "UPDATE hitl_requests SET status=CASE WHEN deadline_at < ? THEN 'expired' ELSE 'orphaned' "
        "END, resolved_at=?, resolved_by='startup' WHERE status='pending' "
        "AND (? IS NULL OR agent_name=?) AND (? IS NULL OR session_id=?)",
        (now, now, agent_name, agent_name, session_id, session_id),
    )
