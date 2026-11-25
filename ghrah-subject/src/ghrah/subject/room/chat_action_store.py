# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""结构化聊天页面动作的 SQLite 持久化。

RoomLog 仍是消息权威；本存储只记录页面发出的动作意图及其到 RoomLog 的持久
关联，不持有业务数据或物料定义。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite


@dataclass(frozen=True)
class ChatActionRecord:
    """一条页面动作意图的持久记录（含到 RoomLog 的关联与状态）。"""

    action_id: str
    project_id: str
    room_id: str
    action: str
    payload_json: str
    status: str
    error: str
    room_log_id: str | None
    created_at: str
    updated_at: str

    def to_wire(self) -> dict[str, Any]:
        """转换为 ``chat_action_list`` 的 wire 形状（payload 还原为对象）。"""
        return {
            "action_id": self.action_id,
            "project_id": self.project_id,
            "room_id": self.room_id,
            "action": self.action,
            "payload": json.loads(self.payload_json),
            "status": self.status,
            "error": self.error,
            "room_log_id": self.room_log_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ChatActionStore:
    """项目内 append-only 的 Workbench 页面动作存储。"""

    _DDL = """
    CREATE TABLE IF NOT EXISTS chat_actions (
        action_id    TEXT PRIMARY KEY,
        project_id   TEXT NOT NULL,
        room_id      TEXT NOT NULL,
        action       TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status       TEXT NOT NULL,
        error        TEXT,
        room_log_id  TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chat_actions_room
        ON chat_actions(room_id, created_at);
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """按需建库并创建表（WAL）；重复调用幂等。"""
        async with self._lock:
            if self._db is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self._db_path, isolation_level=None)
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(self._DDL)
            self._db = db

    async def stop(self) -> None:
        """关闭连接（项目归档/删除与 Subject 退出共用入口）。"""
        async with self._lock:
            db = self._db
            self._db = None
            if db is not None:
                await db.close()

    async def append(
        self,
        *,
        project_id: str,
        room_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> ChatActionRecord:
        """落一条 ``pending`` 动作意图；返回带 ``action_id`` 的记录。"""
        now = datetime.now(UTC).isoformat()
        record = ChatActionRecord(
            action_id=uuid4().hex,
            project_id=project_id,
            room_id=room_id,
            action=action,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            status="pending",
            error="",
            room_log_id=None,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                INSERT INTO chat_actions
                    (action_id, project_id, room_id, action, payload_json,
                    status, error, room_log_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.action_id,
                    record.project_id,
                    record.room_id,
                    record.action,
                    record.payload_json,
                    record.status,
                    record.error,
                    record.room_log_id,
                    record.created_at,
                    record.updated_at,
                ),
            )
            return record

    async def mark_sent(self, action_id: str, room_log_id: str) -> ChatActionRecord | None:
        """标记动作已随消息落账，并记录对应 RoomLog 条目。"""
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                UPDATE chat_actions
                SET status = 'sent', room_log_id = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (room_log_id, datetime.now(UTC).isoformat(), action_id),
            )
            return await self._get_locked(db, action_id)

    async def mark_failed(self, action_id: str, reason: str) -> ChatActionRecord | None:
        """标记动作失败并保留原因（不删除记录，便于恢复时可见）。"""
        async with self._lock:
            db = self._require_db()
            await db.execute(
                """
                UPDATE chat_actions
                SET status = ?, error = ?, updated_at = ?
                WHERE action_id = ?
                """,
                (
                    "failed",
                    reason,
                    datetime.now(UTC).isoformat(),
                    action_id,
                ),
            )
            return await self._get_locked(db, action_id)

    async def list(self, room_id: str, *, limit: int = 200) -> list[ChatActionRecord]:
        """按创建顺序返回 Room 内最近的页面动作（上限 1000）。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                """
                SELECT action_id, project_id, room_id, action, payload_json,
                       status, error, room_log_id, created_at, updated_at
                FROM chat_actions
                WHERE room_id = ?
                ORDER BY created_at
                LIMIT ?
                """,
                (room_id, max(1, min(limit, 1000))),
            )
            rows = await cursor.fetchall()
            return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: Any) -> ChatActionRecord:
        return ChatActionRecord(
            action_id=row[0],
            project_id=row[1],
            room_id=row[2],
            action=row[3],
            payload_json=row[4],
            status=row[5],
            error=row[6] or "",
            room_log_id=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    @staticmethod
    async def _get_locked(db: aiosqlite.Connection, action_id: str) -> ChatActionRecord | None:
        cursor = await db.execute(
            """
            SELECT action_id, project_id, room_id, action, payload_json,
                   status, error, room_log_id, created_at, updated_at
            FROM chat_actions
            WHERE action_id = ?
            """,
            (action_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else ChatActionStore._record(row)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ChatActionStore not started")
        return self._db
