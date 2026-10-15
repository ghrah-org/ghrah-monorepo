# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SQLite RoomStore：RoomRecord 持久化 + RoomLog append-only 链。

机制复用 TaskStore 范式（独立 aiosqlite 连接 / WAL / asyncio.Lock 串行化 /
乐观锁 mutator-update）；RoomLog 按 room_id 分区，seq 单点分配在
``append_log`` 内经锁保护完成（watermark 读-增-写与日志插入同临界区）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite

from ghrah.subject.room.models import (
    RoomLogRecord,
    RoomRecord,
    RoomStatus,
    now_iso,
)

logger = logging.getLogger(__name__)

__all__ = ["ConcurrentModificationError", "RoomArchivedError", "RoomStore"]

RoomRecordList = list[RoomRecord]
RoomLogList = list[RoomLogRecord]


class ConcurrentModificationError(Exception):
    """乐观锁：expected_version 与当前 version 不匹配。"""


class RoomArchivedError(Exception):
    """A write attempted to cross the archived Room boundary."""


_DDL = """
CREATE TABLE IF NOT EXISTS subject_rooms (
    room_id       TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    members       TEXT NOT NULL DEFAULT '[]',
    seq_watermark INTEGER NOT NULL DEFAULT 0,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    archived_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_subject_rooms_project ON subject_rooms(project_id);
CREATE TABLE IF NOT EXISTS subject_room_logs (
    id         TEXT NOT NULL,
    room_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    author     TEXT NOT NULL,
    author_type TEXT NOT NULL,
    timestamp  REAL NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (room_id, seq)
);
"""

_ROOM_COLUMNS = (
    "room_id",
    "project_id",
    "name",
    "status",
    "members",
    "seq_watermark",
    "version",
    "created_at",
    "updated_at",
    "archived_at",
)


async def _ensure_compat_columns(db: aiosqlite.Connection) -> None:
    """Idempotently migrate Room tables created before archived_at existed."""

    cursor = await db.execute("PRAGMA table_info(subject_rooms)")
    columns = {str(row["name"]) for row in await cursor.fetchall()}
    if "archived_at" not in columns:
        await db.execute("ALTER TABLE subject_rooms ADD COLUMN archived_at TEXT")


def _room_to_row(record: RoomRecord) -> dict[str, Any]:
    d = record.model_dump(mode="json")
    d["members"] = json.dumps(d["members"])
    return d


def _row_to_room(row: aiosqlite.Row) -> RoomRecord:
    d = dict(row)
    d["members"] = json.loads(d["members"]) if d["members"] else []
    return RoomRecord.model_validate(d)


def _row_to_log(row: aiosqlite.Row) -> RoomLogRecord:
    d = dict(row)
    d["data"] = json.loads(d["data"]) if d["data"] else {}
    return RoomLogRecord.model_validate(d)


class RoomStore:
    """RoomStore：subject_rooms 表 + subject_room_logs 表（append-only）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._db is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(str(self._db_path), isolation_level=None)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_DDL)
            await _ensure_compat_columns(db)
            self._db = db
        logger.debug("RoomStore started (db=%s)", self._db_path)

    async def stop(self) -> None:
        async with self._lock:
            db = self._db
            if db is None:
                return
            self._db = None
            await db.close()
        logger.debug("RoomStore stopped (db=%s)", self._db_path)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("RoomStore not started. Call start() first.")
        return self._db

    # ─── RoomRecord 读写 ───

    async def upsert(self, room: RoomRecord) -> None:
        async with self._lock:
            db = self._require_db()
            row = _room_to_row(room)
            placeholders = ", ".join(["?"] * len(_ROOM_COLUMNS))
            cols = ", ".join(_ROOM_COLUMNS)
            await db.execute(
                f"INSERT OR REPLACE INTO subject_rooms ({cols}) VALUES ({placeholders})",  # noqa: S608
                tuple(row[c] for c in _ROOM_COLUMNS),
            )

    async def update(
        self,
        room_id: str,
        *,
        expected_version: int | None,
        mutator: Callable[[RoomRecord], RoomRecord],
    ) -> RoomRecord | None:
        """读出 → 校验乐观锁 → mutator → version+1 → 写回（范式同 TaskStore）。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM subject_rooms WHERE room_id = ?", (room_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            current = _row_to_room(row)
            if expected_version is not None and current.version != expected_version:
                raise ConcurrentModificationError(
                    f"Room {room_id} modified: expected version {expected_version}, "
                    f"got {current.version}"
                )
            updated = mutator(current)
            updated.version = current.version + 1
            new_row = _room_to_row(updated)
            placeholders = ", ".join([f"{c} = ?" for c in _ROOM_COLUMNS])
            await db.execute(
                f"UPDATE subject_rooms SET {placeholders} WHERE room_id = ?",  # noqa: S608
                tuple(new_row[c] for c in _ROOM_COLUMNS) + (room_id,),
            )
            return updated

    async def get(self, room_id: str) -> RoomRecord | None:
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM subject_rooms WHERE room_id = ?", (room_id,))
            row = await cursor.fetchone()
            return _row_to_room(row) if row is not None else None

    async def list(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
    ) -> RoomRecordList:
        async with self._lock:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if project_id is not None:
                clauses.append("project_id = ?")
                params.append(project_id)
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM subject_rooms {where} ORDER BY created_at",  # noqa: S608
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [_row_to_room(r) for r in rows]

    async def delete(self, room_id: str, *, expected_version: int | None = None) -> bool:
        """Atomically hard-delete Room metadata, membership and all log entries."""

        async with self._lock:
            db = self._require_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "SELECT version FROM subject_rooms WHERE room_id = ?", (room_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.rollback()
                    return False
                current_version = int(row["version"])
                if expected_version is not None and current_version != expected_version:
                    raise ConcurrentModificationError(
                        f"Room {room_id} modified: expected version {expected_version}, "
                        f"got {current_version}"
                    )
                await db.execute("DELETE FROM subject_room_logs WHERE room_id = ?", (room_id,))
                await db.execute("DELETE FROM subject_rooms WHERE room_id = ?", (room_id,))
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                raise

    async def count_logs(self, room_id: str) -> int:
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT COUNT(*) FROM subject_room_logs WHERE room_id = ?",
                (room_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row is not None else 0

    # ─── RoomLog append-only 链 ───

    async def get_log_by_id(self, room_id: str, entry_id: str) -> RoomLogRecord | None:
        """按持久引用查询投影，供恢复时幂等追加。"""
        async with self._lock:
            row = await (
                await self._require_db().execute(
                    "SELECT * FROM subject_room_logs WHERE room_id=? AND id=?", (room_id, entry_id)
                )
            ).fetchone()
            return _row_to_log(row) if row else None

    async def append_log(
        self,
        room_id: str,
        record_factory: Callable[[int], RoomLogRecord],
    ) -> tuple[RoomLogRecord, RoomRecord] | None:
        """seq 单点分配 + 追加日志 + 推进水位（同一临界区，锁内单调）。

        ``record_factory(seq)`` 由调用方组装节点（manager 注入 author/data）；
        水位推进不 bump version（对齐 mock 契约：send 只改 seq_watermark +
        updated_at）。room 不存在返回 None。
        """
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM subject_rooms WHERE room_id = ?", (room_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            room = _row_to_room(row)
            if room.status == RoomStatus.ARCHIVED:
                raise RoomArchivedError(room_id)
            seq = room.seq_watermark + 1
            record = record_factory(seq)
            if record.data.get("mm_message_id"):
                previous = await (
                    await db.execute(
                        "SELECT * FROM subject_room_logs WHERE room_id=? AND id=?",
                        (room_id, record.id),
                    )
                ).fetchone()
                if previous:
                    return _row_to_log(previous), room
            await db.execute(
                "INSERT INTO subject_room_logs "
                "(id, room_id, seq, author, author_type, timestamp, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.room_id,
                    record.seq,
                    record.author,
                    record.author_type.value,
                    record.timestamp,
                    json.dumps(record.data),
                ),
            )
            room.seq_watermark = seq
            room.updated_at = now_iso()
            await db.execute(
                "UPDATE subject_rooms SET seq_watermark = ?, updated_at = ? WHERE room_id = ?",
                (seq, room.updated_at, room_id),
            )
            return record, room

    async def get_log(
        self,
        room_id: str,
        *,
        since_seq: int | None = None,
        limit: int = 100,
    ) -> RoomLogList:
        """按 seq 升序读取 room 日志；since_seq 增量、limit 截尾（取尾部 N 条）。"""
        async with self._lock:
            db = self._require_db()
            if since_seq is not None:
                cursor = await db.execute(
                    "SELECT * FROM subject_room_logs WHERE room_id = ? AND seq > ? ORDER BY seq",  # noqa: S608
                    (room_id, since_seq),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM subject_room_logs WHERE room_id = ? ORDER BY seq",  # noqa: S608
                    (room_id,),
                )
            rows = await cursor.fetchall()
            return [_row_to_log(r) for r in rows[-limit:]]
