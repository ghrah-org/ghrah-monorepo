# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目页面状态的唯一写入存储（单页：page_id=main）。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite


class PageStateStore:
    """持有项目页面当前值；HTML 与 data 分离，revision 单调递增。"""

    _DDL = """
    CREATE TABLE IF NOT EXISTS page_state (
        project_id TEXT NOT NULL,
        page_id TEXT NOT NULL,
        title TEXT NOT NULL,
        html TEXT NOT NULL,
        data_json TEXT NOT NULL,
        revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (project_id, page_id)
    );
    """

    def __init__(self, db_path: str | Path, project_id: str) -> None:
        self._path = Path(db_path)
        self._project_id = project_id
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """打开项目库并按需创建表。"""
        async with self._lock:
            if self._db is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self._path, isolation_level=None)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(self._DDL)
            self._db = db

    async def stop(self) -> None:
        """关闭连接；重复调用安全。"""
        async with self._lock:
            if self._db is None:
                return
            await self._db.close()
            self._db = None

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("page store is not started")
        return self._db

    @staticmethod
    def _to_view(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "project_id": row["project_id"],
            "page_id": row["page_id"],
            "title": row["title"],
            "html": row["html"],
            "data": json.loads(row["data_json"]),
            "revision": int(row["revision"]),
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    async def get(self, page_id: str = "main") -> dict[str, Any] | None:
        """读取当前页面；未渲染过时返回 None。"""
        db = self._require()
        async with db.execute(
            "SELECT * FROM page_state WHERE project_id=? AND page_id=?",
            (self._project_id, page_id),
        ) as cursor:
            row = await cursor.fetchone()
        return None if row is None else self._to_view(row)

    async def set_page(
        self,
        *,
        page_id: str,
        title: str,
        html: str,
        data: dict[str, Any],
        updated_by: str,
    ) -> dict[str, Any]:
        """整页替换：revision 自增后写回。"""
        db = self._require()
        now = datetime.now(UTC).isoformat()
        data_json = json.dumps(data, ensure_ascii=False)
        async with self._lock:
            async with db.execute(
                "SELECT revision FROM page_state WHERE project_id=? AND page_id=?",
                (self._project_id, page_id),
            ) as cursor:
                row = await cursor.fetchone()
            revision = 1 if row is None else int(row["revision"]) + 1
            await db.execute(
                "INSERT INTO page_state"
                " (project_id, page_id, title, html, data_json, revision, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(project_id, page_id) DO UPDATE SET"
                " title=excluded.title, html=excluded.html, data_json=excluded.data_json,"
                " revision=excluded.revision, updated_at=excluded.updated_at,"
                " updated_by=excluded.updated_by",
                (self._project_id, page_id, title, html, data_json, revision, now, updated_by),
            )
        return {
            "project_id": self._project_id,
            "page_id": page_id,
            "title": title,
            "html": html,
            "data": data,
            "revision": revision,
            "updated_at": now,
            "updated_by": updated_by,
        }

    async def patch_data(
        self,
        *,
        page_id: str,
        data: dict[str, Any],
        updated_by: str,
    ) -> dict[str, Any]:
        """合并页面 data 并自增 revision；页面不存在时报错。"""
        db = self._require()
        async with self._lock:
            async with db.execute(
                "SELECT * FROM page_state WHERE project_id=? AND page_id=?",
                (self._project_id, page_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("page_not_found")
            merged = {**json.loads(row["data_json"]), **data}
            now = datetime.now(UTC).isoformat()
            revision = int(row["revision"]) + 1
            await db.execute(
                "UPDATE page_state SET data_json=?, revision=?, updated_at=?, updated_by=?"
                " WHERE project_id=? AND page_id=?",
                (
                    json.dumps(merged, ensure_ascii=False),
                    revision,
                    now,
                    updated_by,
                    self._project_id,
                    page_id,
                ),
            )
        return {
            "project_id": self._project_id,
            "page_id": page_id,
            "revision": revision,
            "updated_at": now,
            "updated_by": updated_by,
        }
