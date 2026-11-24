# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目 MessageManager 权威表的实体作用域只读端口，不启动消息投递。"""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import aiosqlite
from ghrah.protocol.payloads.entity import AgentIdentity

from ghrah.communication.delivery_queue import (
    delivery_projection,
    first_queue_blocker,
    has_turns,
    project_delivery,
)
from ghrah.communication.delivery_queue import display_state as _display_state  # noqa: F401
from ghrah.communication.sqlite_message_store import sqlite_message_reads
from ghrah.context.persistence.sqlite_read import SqliteReadDomain, StorageScopeMismatchError


class MailboxReader:
    """通过同文件只读事务查询单个实体的消息，生命周期限定在一次查询。"""

    def __init__(self, source: Path | SqliteReadDomain, identity: AgentIdentity) -> None:
        self._domain = (
            source
            if isinstance(source, SqliteReadDomain)
            else sqlite_message_reads(source, identity.project_id)
        )
        if self._domain.scope_id != identity.project_id:
            raise ValueError("mailbox_project_mismatch")
        self._identity = identity
        self._db: aiosqlite.Connection | None = None
        self._snapshot: (
            AbstractAsyncContextManager[tuple[aiosqlite.Connection | None, bool]] | None
        ) = None

    async def __aenter__(self) -> MailboxReader:
        """借用本域的一次只读消息快照。"""
        snapshot = self._domain.message_snapshot()
        try:
            db, has_messages = await snapshot.__aenter__()
        except StorageScopeMismatchError as exc:
            raise ValueError(str(exc)) from exc
        if db is None or not has_messages:
            await snapshot.__aexit__(None, None, None)
            raise ValueError("mailbox_unavailable")
        self._db = db
        self._snapshot = snapshot
        return self

    async def __aexit__(self, *args: object) -> None:
        """关闭消息快照。"""
        self._db = None
        if self._snapshot is not None:
            snapshot, self._snapshot = self._snapshot, None
            await snapshot.__aexit__(None, None, None)

    async def summary(self) -> dict[str, Any]:
        """按实体统计各投递状态与发送消息数。"""
        assert self._db is not None
        rows = await (
            await self._db.execute(
                "SELECT CASE WHEN blocked_reason IS NOT NULL THEN 'blocked' "
                "ELSE state END AS status, "
                "COUNT(*) AS count FROM mm_deliveries WHERE recipient_id=? GROUP BY status",
                (self._identity.agent_id,),
            )
        ).fetchall()
        sent = await (
            await self._db.execute(
                "SELECT COUNT(*) FROM mm_messages WHERE sender_id=?", (self._identity.agent_id,)
            )
        ).fetchone()
        return {
            "availability": "available",
            "inbox_counts": {row["status"]: row["count"] for row in rows},
            "outbox_count": sent[0],
        }

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """读取本实体参与的消息，接收者只能看到自己的投递记录。"""
        assert self._db is not None
        row = await (
            await self._db.execute(
                "SELECT m.message_id,m.sender_id,m.content,m.kind,m.reply_to,m.accepted_at,"
                "m.origin_node_id,m.sender_session_id,m.metadata,m.external_sender "
                "FROM mm_messages m WHERE m.message_id=? AND (m.sender_id=? OR EXISTS "
                "(SELECT 1 FROM mm_deliveries d WHERE d.message_id=m.message_id "
                "AND d.recipient_id=?))",
                (message_id, self._identity.agent_id, self._identity.agent_id),
            )
        ).fetchone()
        if row is None:
            raise ValueError("message_not_found")
        deliveries = await (
            await self._db.execute(
                "SELECT d.*, r.delivery_id IS NOT NULL AS result_ready FROM mm_deliveries d "
                "LEFT JOIN mm_delivery_results r ON r.delivery_id=d.delivery_id "
                "WHERE d.message_id=? AND (? OR d.recipient_id=?) "
                "ORDER BY d.seq",
                (message_id, row["sender_id"] == self._identity.agent_id, self._identity.agent_id),
            )
        ).fetchall()
        message = dict(row)
        message["metadata"] = json.loads(message["metadata"])
        message["external_sender"] = bool(message["external_sender"])
        projected = []
        for delivery in deliveries:
            item = await project_delivery(self._db, delivery)
            projected.append(item)
        return {"message": message, "deliveries": projected}

    async def get_delivery(self, delivery_id: str) -> dict[str, Any]:
        """读取本实体可见的单条投递。

        可见性：收件人只看自己的投递；发件人可看该消息的全部投递。
        """
        assert self._db is not None
        row = await (
            await self._db.execute(
                "SELECT d.*, m.sender_id AS _sender, r.delivery_id IS NOT NULL AS result_ready "
                "FROM mm_deliveries d LEFT JOIN mm_delivery_results r "
                "ON r.delivery_id=d.delivery_id "
                "JOIN mm_messages m ON m.message_id=d.message_id "
                "WHERE d.delivery_id=? AND (d.recipient_id=? OR m.sender_id=?)",
                (delivery_id, self._identity.agent_id, self._identity.agent_id),
            )
        ).fetchone()
        if row is None:
            raise ValueError("delivery_not_found")
        return await project_delivery(self._db, row)

    async def queue(self, session_id: str | None) -> dict[str, Any]:
        """返回会话中尚未完成的投递与首个活动轮次。"""
        assert self._db is not None
        rows = await (
            await self._db.execute(
                "SELECT d.*, r.delivery_id IS NOT NULL AS result_ready FROM mm_deliveries d "
                "LEFT JOIN mm_delivery_results r ON r.delivery_id=d.delivery_id "
                "WHERE d.recipient_id=? AND d.target_session_id IS ? "
                "AND d.state!='completed' "
                "AND (d.state IN ('pending','processing') OR "
                "(r.delivery_id IS NOT NULL AND d.state!='completed')) ORDER BY d.seq",
                (self._identity.agent_id, session_id),
            )
        ).fetchall()
        with_turns = await has_turns(self._db)
        first_blocker = None
        if any(row["state"] == "pending" and row["turn_id"] is None for row in rows):
            first_blocker = await first_queue_blocker(
                self._db,
                recipient_id=self._identity.agent_id,
                session_id=session_id,
                with_turns=with_turns,
            )
        entries = []
        queue_position = 0
        for row in rows:
            queued = row["state"] == "pending" and row["turn_id"] is None
            if queued and row["consumed_node_id"] is None and row["parked"] == 0:
                queue_position += 1
            item = delivery_projection(
                row,
                position=queue_position if queued else 0,
                blocker=(
                    first_blocker
                    if queued and first_blocker is not None and first_blocker["seq"] < row["seq"]
                    else None
                ),
            )
            entries.append(
                {
                    "delivery_id": item["delivery_id"],
                    "message_id": item["message_id"],
                    "display_state": item["display_state"],
                    "position": item["queue_position"],
                    "queue_reason": item["queue_reason"],
                    "blocked_by_delivery_id": item["blocked_by_delivery_id"],
                    "turn_id": item["turn_id"],
                    "cancelable": item["cancelable"],
                }
            )
        active = None
        if with_turns:
            active = await (
                await self._db.execute(
                    "SELECT turn_id FROM turns WHERE agent_name=? AND session_id IS ? "
                    "AND status IN ('open','waiting_hitl')",
                    (self._identity.agent_id, session_id),
                )
            ).fetchone()
        return {
            "agent_id": self._identity.agent_id,
            "session_id": session_id,
            "entries": entries,
            "open_turn_id": active[0] if active else None,
        }

    async def list_messages(
        self, *, inbox: bool, limit: int, before_seq: int | None
    ) -> dict[str, Any]:
        """使用递减序号分页；查询期间新消息不会造成重复翻页。"""
        if not 1 <= limit <= 200:
            raise ValueError("invalid_page_size")
        assert self._db is not None
        if inbox:
            sql = (
                "SELECT seq,message_id FROM mm_deliveries WHERE recipient_id=? "
                "AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT ?"
            )
        else:
            sql = (
                "SELECT seq,message_id FROM mm_messages WHERE sender_id=? "
                "AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT ?"
            )
        rows = await (
            await self._db.execute(
                sql, (self._identity.agent_id, before_seq, before_seq, limit + 1)
            )
        ).fetchall()
        items = [await self.get_message(row["message_id"]) for row in rows[:limit]]
        return {
            "items": items,
            "next_before_seq": rows[limit - 1]["seq"] if len(rows) > limit else None,
        }
