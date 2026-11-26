# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""项目业务当前值的唯一写入存储；与消息库、页面动作库分别持有不同事实。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite
from ghrah.protocol.enums import RoomSubjectType
from ghrah.protocol.payloads.material import MaterialStateRegisterPayload
from jsonschema.exceptions import ValidationError as SchemaValidationError

from ghrah.subject.material.contract import MaterialContract, json_text
from ghrah.subject.room.models import RoomLogRecord


class MaterialStateStore:
    """在同一事务中写当前值、执行回执和 Room 消费水位。"""

    _DDL = """
    CREATE TABLE IF NOT EXISTS material_state (
        project_id TEXT PRIMARY KEY,
        material_id TEXT NOT NULL,
        material_version TEXT NOT NULL,
        title TEXT NOT NULL,
        contract_json TEXT NOT NULL,
        data_json TEXT NOT NULL,
        revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS material_patch_events (
        event_id TEXT PRIMARY KEY,
        room_log_id TEXT NOT NULL UNIQUE,
        room_id TEXT NOT NULL,
        patch_json TEXT NOT NULL,
        status TEXT NOT NULL,
        revision INTEGER NOT NULL,
        fields_json TEXT NOT NULL,
        error TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS material_sync_cursors (
        room_id TEXT PRIMARY KEY,
        seq INTEGER NOT NULL
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
            try:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA journal_mode=WAL")
                await db.executescript(self._DDL)
                # The earlier local prototype had durable values and log IDs,
                # but no Room binding or display fields. Add metadata only.
                async with db.execute("PRAGMA table_info(material_patch_events)") as cursor:
                    columns = {row["name"] for row in await cursor.fetchall()}
                for name, declaration in (
                    ("room_id", "TEXT NOT NULL DEFAULT ''"),
                    ("fields_json", "TEXT NOT NULL DEFAULT '[]'"),
                ):
                    if name not in columns:
                        await db.execute(
                            f"ALTER TABLE material_patch_events ADD COLUMN {name} {declaration}"
                        )
            except BaseException:
                await db.close()
                raise
            self._db = db

    async def stop(self) -> None:
        """关闭连接，归档和删除项目时也使用此入口。"""
        async with self._lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("material state store is closed")
        return self._db

    async def _row(self, db: aiosqlite.Connection) -> Any:
        async with db.execute(
            "SELECT * FROM material_state WHERE project_id = ?", (self._project_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def _result(self, db: aiosqlite.Connection) -> dict[str, Any]:
        row = await self._row(db)
        state = None
        if row is not None:
            state = {
                key: row[key]
                for key in (
                    "project_id",
                    "material_id",
                    "material_version",
                    "title",
                    "revision",
                    "updated_at",
                )
            }
            state["data"] = json.loads(row["data_json"])
        async with db.execute(
            "SELECT * FROM material_patch_events ORDER BY rowid DESC LIMIT 200"
        ) as cursor:
            rows = await cursor.fetchall()
        events = [
            {
                "room_log_id": item["room_log_id"],
                "room_id": item["room_id"],
                "status": item["status"],
                "revision": item["revision"] if item["revision"] is not None else 0,
                "fields": json.loads(item["fields_json"]),
                "error": item["error"],
            }
            for item in reversed(rows)
        ]
        if row is not None:
            properties = json.loads(row["contract_json"])["data_schema"]["properties"]
            for event, stored in zip(events, reversed(rows), strict=True):
                if not event["fields"] and event["status"] != "rejected":
                    event["fields"] = [
                        {
                            "key": key,
                            "label": properties.get(key, {}).get("title", key),
                            "value": value,
                        }
                        for key, value in json.loads(stored["patch_json"]).items()
                    ]
        return {"state": state, "applications": events, "has_more": False}

    async def get(self) -> dict[str, Any]:
        """读取权威快照；尚未登记是合法空态。"""
        async with self._lock:
            return await self._result(self._connection())

    async def register(
        self, request: MaterialStateRegisterPayload, *, room_cursors: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """相同版本与契约幂等；拒绝以新模板覆盖既有项目数据。"""
        contract = MaterialContract(request.data_schema, request.patch_schema)
        contract.validate_data(request.initial_data)
        definition = json_text(
            {
                "initial_data": request.initial_data,
                "data_schema": request.data_schema,
                "patch_schema": request.patch_schema,
            }
        )
        async with self._lock:
            db = self._connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._row(db)
                if existing is not None:
                    old_contract = json.loads(existing["contract_json"])
                    same_contract = existing["contract_json"] == definition
                    legacy_contract = "initial_data" not in old_contract and old_contract == {
                        "data_schema": request.data_schema,
                        "patch_schema": request.patch_schema,
                    }
                    if (
                        existing["material_id"] != request.material_id
                        or existing["material_version"] != request.material_version
                        or not (same_contract or legacy_contract)
                    ):
                        raise ValueError("material_state_contract_conflict")
                    if legacy_contract:
                        await db.execute(
                            "UPDATE material_state SET contract_json=? WHERE project_id=?",
                            (definition, self._project_id),
                        )
                else:
                    await db.execute(
                        "INSERT INTO material_state "
                        "(project_id, material_id, material_version, title, "
                        "contract_json, data_json, revision, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                        (
                            self._project_id,
                            request.material_id,
                            request.material_version,
                            request.title,
                            definition,
                            json_text(request.initial_data),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    # A newly bound data source starts at the current Room
                    # watermarks. Earlier chat proposals are not initialization
                    # data and must never overwrite the explicit seed.
                    for room_id, seq in (room_cursors or {}).items():
                        await db.execute(
                            "INSERT INTO material_sync_cursors VALUES (?, ?)", (room_id, seq)
                        )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            return await self._result(db)

    async def legacy_log_ids(self) -> list[str]:
        """查询旧实验库尚未归属到 Room 的回执引用。"""
        async with self._lock:
            async with self._connection().execute(
                "SELECT room_log_id FROM material_patch_events WHERE room_id=''"
            ) as cursor:
                return [row["room_log_id"] for row in await cursor.fetchall()]

    async def bind_legacy_room(self, room_id: str, entries: list[RoomLogRecord]) -> None:
        """用消息权威补齐旧回执的 Room 和消费位置，保持当前业务值。"""
        if not entries:
            return
        async with self._lock:
            db = self._connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                for entry in entries:
                    await db.execute(
                        "UPDATE material_patch_events SET room_id=? "
                        "WHERE room_log_id=? AND room_id=''",
                        (room_id, entry.id),
                    )
                await db.execute(
                    "INSERT INTO material_sync_cursors VALUES (?, ?) ON CONFLICT(room_id) "
                    "DO UPDATE SET seq=MAX(seq, excluded.seq)",
                    (room_id, max(entry.seq for entry in entries)),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def cursor(self, room_id: str) -> int:
        """读取增量消费位置；不依赖客户端最近消息窗口。"""
        async with self._lock:
            async with self._connection().execute(
                "SELECT seq FROM material_sync_cursors WHERE room_id = ?", (room_id,)
            ) as cursor:
                row = await cursor.fetchone()
            return 0 if row is None else int(row["seq"])

    async def sync(self, room_id: str, entries: list[RoomLogRecord]) -> dict[str, Any]:
        """顺序消费已提交消息，非法 patch 记失败回执且不改变业务数据。"""
        async with self._lock:
            db = self._connection()
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await self._row(db)
                if row is None:
                    raise ValueError("material_state_not_registered")
                definition = json.loads(row["contract_json"])
                contract = MaterialContract(definition["data_schema"], definition["patch_schema"])
                data = json.loads(row["data_json"])
                revision = int(row["revision"])
                async with db.execute(
                    "SELECT seq FROM material_sync_cursors WHERE room_id = ?", (room_id,)
                ) as cursor:
                    saved = await cursor.fetchone()
                seq = 0 if saved is None else int(saved["seq"])
                for entry in entries:
                    if entry.room_id != room_id:
                        raise ValueError("material_state_room_mismatch")
                    if entry.seq <= seq:
                        continue
                    seq = entry.seq
                    async with db.execute(
                        "SELECT 1 FROM material_patch_events WHERE room_log_id=?", (entry.id,)
                    ) as cursor:
                        if await cursor.fetchone() is not None:
                            continue
                    text = entry.data.get("message")
                    if (
                        entry.author_type != RoomSubjectType.AGENT
                        or not isinstance(text, str)
                        or "```requirement.patch" not in text.lower()
                    ):
                        continue
                    status, error, patch, fields = "rejected", "", {}, []
                    try:
                        patch, updated = contract.apply(data, text)
                        fields = [
                            {
                                "key": key,
                                "label": str(
                                    contract.data_schema["properties"][key].get("title", key)
                                ),
                                "value": value,
                            }
                            for key, value in patch.items()
                        ]
                    except (ValueError, TypeError, SchemaValidationError) as exc:
                        error = exc.message if isinstance(exc, SchemaValidationError) else str(exc)
                    else:
                        status = "unchanged" if json_text(updated) == json_text(data) else "applied"
                        if status == "applied":
                            data = updated
                            revision += 1
                            await db.execute(
                                "UPDATE material_state SET data_json=?, revision=?, updated_at=? "
                                "WHERE project_id=?",
                                (
                                    json_text(data),
                                    revision,
                                    datetime.now(UTC).isoformat(),
                                    self._project_id,
                                ),
                            )
                    await db.execute(
                        "INSERT INTO material_patch_events "
                        "(event_id, room_log_id, room_id, patch_json, status, revision, "
                        "fields_json, error, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            uuid4().hex,
                            entry.id,
                            room_id,
                            json_text(patch),
                            status,
                            revision,
                            json_text(fields),
                            error,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                await db.execute(
                    "INSERT INTO material_sync_cursors VALUES (?, ?) "
                    "ON CONFLICT(room_id) DO UPDATE SET seq=excluded.seq",
                    (room_id, seq),
                )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            return await self._result(db)
