"""项目 MessageManager 权威表的实体作用域只读端口，不启动消息投递。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
from ghrah.protocol.payloads.entity import AgentIdentity


class MailboxReader:
    """通过同文件只读事务查询单个实体的消息，生命周期限定在一次查询。"""

    def __init__(self, db_path: Path, identity: AgentIdentity) -> None:
        self._path = db_path
        self._identity = identity
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> MailboxReader:
        """只打开既有数据库，验证项目归属，不建表或恢复 worker。"""
        self._db = await aiosqlite.connect(
            self._path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
        )
        self._db.row_factory = aiosqlite.Row
        try:
            await self._db.execute("PRAGMA query_only=ON")
            await self._db.execute("BEGIN")
            tables = await (
                await self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='mm_meta'"
                )
            ).fetchone()
            if tables is None:
                raise ValueError("mailbox_unavailable")
            meta = await (
                await self._db.execute("SELECT project_id FROM mm_meta WHERE singleton=1")
            ).fetchone()
            if meta is None or meta[0] != self._identity.project_id:
                raise ValueError("mailbox_project_mismatch")
        except BaseException:
            await self._db.close()
            self._db = None
            raise
        return self

    async def __aexit__(self, *args: object) -> None:
        """关闭只读事务连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None

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
                "SELECT d.* FROM mm_deliveries d WHERE d.message_id=? AND (? OR d.recipient_id=?) "
                "ORDER BY d.seq",
                (message_id, row["sender_id"] == self._identity.agent_id, self._identity.agent_id),
            )
        ).fetchall()
        message = dict(row)
        message["project_id"] = self._identity.project_id
        message["metadata"] = json.loads(message["metadata"])
        message["external_sender"] = bool(message["external_sender"])
        projected = []
        for delivery in deliveries:
            item = dict(delivery)
            if item.get("blocked_reason"):
                item["state"] = "blocked"
            projected.append(item)
        return {"message": message, "deliveries": projected}

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
