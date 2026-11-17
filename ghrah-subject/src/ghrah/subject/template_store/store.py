# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""模板持久化：``agent_templates`` 单表，一行一个版本。

模型与约定（与模板 JSON 对象文档 §九 对齐）：

- ``template_id`` 是**模板身份**，创建时生成一次，跨版本不变；
- ``(template_id, revision)`` 是版本主键，``revision`` 同一模板内自增；
- ``is_latest`` 标记该模板的最新版本，同一 ``template_id`` 有且仅有一行为 1；
- ``format`` 决定七个 JSON 列按哪套策略解析（见 ``formats`` 子包）；
- ``reference_count`` 记录"从这一版实例化过多少次"，由实例化路径写；
- ``archived_at`` 是预留的软删除标记，非空即已归档。

更新语义：**插入新行 + 翻转旧行**，不改写历史行（历史版本可回放）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from ghrah.subject.template_store.formats import (
    DEFAULT_FORMAT,
    UnsupportedTemplateFormatError,
    resolve,
)
from ghrah.subject.template_store.models import (
    AgentTemplateConfig,
    AgentTemplateRecord,
)

__all__ = [
    "TemplateConflictError",
    "TemplateIdentityConflictError",
    "TemplateNotFoundError",
    "TemplateStore",
    "TemplateStoreError",
    "UnsupportedTemplateFormatError",
]

_SCHEMA_COMPONENT = "agent_template_store"

#: v2：单表 + 行级版本（v1 的「current 行 + revisions 表」未发布，直接换代）。
_SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS subject_schema_versions (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_templates (
    template_id        TEXT NOT NULL,
    revision           INTEGER NOT NULL,
    is_latest          INTEGER NOT NULL DEFAULT 1,
    format             TEXT NOT NULL,
    metadata           TEXT NOT NULL,
    model              TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    context_policy     TEXT NOT NULL,
    ability_request    TEXT NOT NULL,
    permission_request TEXT NOT NULL,
    path_request       TEXT NOT NULL,
    reference_count    INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    archived_at        TEXT,
    PRIMARY KEY (template_id, revision),
    CHECK (is_latest IN (0, 1)),
    CHECK (revision >= 1),
    CHECK (reference_count >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_templates_latest
    ON agent_templates(template_id) WHERE is_latest = 1;
CREATE INDEX IF NOT EXISTS idx_agent_templates_template ON agent_templates(template_id);
CREATE INDEX IF NOT EXISTS idx_agent_templates_archived ON agent_templates(archived_at);
"""

#: 业务身份查询：``metadata`` 是 JSON 列，用 json_extract 取两段。
_IDENTITY_EXPR = "json_extract(metadata, '$.namespace'), json_extract(metadata, '$.name')"

_COLUMNS = (
    "template_id",
    "revision",
    "is_latest",
    "format",
    "metadata",
    "model",
    "prompt",
    "context_policy",
    "ability_request",
    "permission_request",
    "path_request",
    "reference_count",
    "created_at",
    "updated_at",
    "archived_at",
)


class TemplateStoreError(Exception):
    """模板 store 的错误基类。"""


class TemplateNotFoundError(TemplateStoreError):
    """``template_id`` 不存在。"""

    def __init__(self, template_id: str) -> None:
        self.template_id = template_id
        super().__init__(f"模板不存在：{template_id}")


class TemplateIdentityConflictError(TemplateStoreError):
    """``(namespace, name)`` 已被某个模板占用（任何 revision）。"""

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        super().__init__(f"模板身份已存在：{namespace}/{name}")


class TemplateConflictError(TemplateStoreError):
    """revision 不一致，或模板已归档。"""

    def __init__(self, template_id: str, current_revision: int, *, archived: bool = False) -> None:
        self.template_id = template_id
        self.current_revision = current_revision
        self.archived = archived
        reason = "已归档" if archived else "revision 不一致"
        super().__init__(f"模板冲突（{reason}）：{template_id}#{current_revision}")


def _now() -> str:
    """RFC 3339 UTC 秒级时间戳。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _row_to_record(row: aiosqlite.Row) -> AgentTemplateRecord:
    """列 → 记录；七个 JSON 列按该行的 ``format`` 解析。"""
    fmt = row["format"]
    config = resolve(str(fmt)).from_columns(row)
    return AgentTemplateRecord(
        template_id=str(row["template_id"]),
        revision=int(row["revision"]),
        is_latest=int(row["is_latest"]),
        format=str(fmt),
        reference_count=int(row["reference_count"]),
        metadata=config.metadata,
        model=config.model,
        prompt=config.prompt,
        context_policy=config.context_policy,
        ability_request=config.ability_request,
        permission_request=config.permission_request,
        path_request=config.path_request,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=None if row["archived_at"] is None else str(row["archived_at"]),
    )


def _new_row(
    *,
    template_id: str,
    revision: int,
    config: AgentTemplateConfig,
    fmt: str,
    now: str,
) -> dict[str, Any]:
    """组装一行（新版本恒 ``is_latest=1``、``archived_at=None``、计数从 0 起）。"""
    columns: dict[str, Any] = {
        "template_id": template_id,
        "revision": revision,
        "is_latest": 1,
        "format": fmt,
        "reference_count": 0,
        "created_at": now,
        "updated_at": now,
        "archived_at": None,
    }
    columns.update(resolve(fmt).to_columns(config))
    return columns


class TemplateStore:
    """``agent_templates`` 单表的读写门面。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """打开连接并建表/换代。"""
        if self._db is not None:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._migrate(self._db)

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """建表；检测到 v1 结构（未发布）时整表换代。"""
        await db.execute(
            "CREATE TABLE IF NOT EXISTS subject_schema_versions ("
            "component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        cursor = await db.execute(
            "SELECT version FROM subject_schema_versions WHERE component = ?",
            (_SCHEMA_COMPONENT,),
        )
        row = await cursor.fetchone()
        current = int(row["version"]) if row is not None else 0
        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"agent_template_store schema version {current} is newer than supported "
                f"{_SCHEMA_VERSION}"
            )
        if 0 < current < _SCHEMA_VERSION:
            # v1 从未发布，无数据迁移义务：直接丢弃旧结构与旧行。
            await db.execute("DROP TABLE IF EXISTS agent_template_revisions")
            await db.execute("DROP TABLE IF EXISTS agent_templates")
        await db.executescript(_DDL)
        await db.execute(
            "INSERT INTO subject_schema_versions(component, version) VALUES (?, ?) "
            "ON CONFLICT(component) DO UPDATE SET version = excluded.version",
            (_SCHEMA_COMPONENT, _SCHEMA_VERSION),
        )
        await db.commit()

    async def stop(self) -> None:
        """关闭连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TemplateStore 未启动：先调用 start()")
        return self._db

    async def _rollback(self, db: aiosqlite.Connection) -> None:
        try:
            await db.execute("ROLLBACK")
        except sqlite3.OperationalError:  # pragma: no cover - 事务已结束
            pass

    # ─── 写入 ───

    async def create(self, config: AgentTemplateConfig) -> AgentTemplateRecord:
        """新建模板：身份查重后写入 revision 1。

        Raises:
            TemplateIdentityConflictError: ``(namespace, name)`` 已被占用。
        """
        async with self._lock:
            db = self._require_db()
            now = _now()
            row = _new_row(
                template_id=_new_template_id(),
                revision=1,
                config=config,
                fmt=DEFAULT_FORMAT,
                now=now,
            )
            await db.execute("BEGIN IMMEDIATE")
            try:
                existing = await self._row_by_identity(
                    db, config.metadata.namespace, config.metadata.name
                )
                if existing is not None:
                    await self._rollback(db)
                    raise TemplateIdentityConflictError(
                        config.metadata.namespace, config.metadata.name
                    )
                await db.execute(
                    f"INSERT INTO agent_templates ({', '.join(_COLUMNS)}) "  # noqa: S608
                    f"VALUES ({', '.join(['?'] * len(_COLUMNS))})",
                    tuple(row[column] for column in _COLUMNS),
                )
                await db.execute("COMMIT")
            except BaseException:
                await self._rollback(db)
                raise
            return _row_to_record(await self._require_row(db, row["template_id"], 1))

    async def update(
        self, template_id: str, expected_revision: int, config: AgentTemplateConfig
    ) -> AgentTemplateRecord:
        """按 CAS 追加新版本：旧行置 ``is_latest=0``，新行 ``revision+1``。

        Raises:
            TemplateNotFoundError: ``template_id`` 不存在。
            TemplateConflictError: revision 不一致，或最新版本已归档。
        """
        async with self._lock:
            db = self._require_db()
            now = _now()
            await db.execute("BEGIN IMMEDIATE")
            try:
                latest = await self._row_latest(db, template_id)
                if latest is None:
                    await self._rollback(db)
                    raise TemplateNotFoundError(template_id)
                if latest["archived_at"] is not None:
                    revision = int(latest["revision"])
                    await self._rollback(db)
                    raise TemplateConflictError(template_id, revision, archived=True)
                if int(latest["revision"]) != expected_revision:
                    revision = int(latest["revision"])
                    await self._rollback(db)
                    raise TemplateConflictError(template_id, revision)
                row = _new_row(
                    template_id=template_id,
                    revision=expected_revision + 1,
                    config=config,
                    fmt=str(latest["format"]),
                    now=now,
                )
                await db.execute(
                    "UPDATE agent_templates SET is_latest = 0, updated_at = ? "
                    "WHERE template_id = ? AND is_latest = 1",
                    (now, template_id),
                )
                await db.execute(
                    f"INSERT INTO agent_templates ({', '.join(_COLUMNS)}) "  # noqa: S608
                    f"VALUES ({', '.join(['?'] * len(_COLUMNS))})",
                    tuple(row[column] for column in _COLUMNS),
                )
                await db.execute("COMMIT")
            except BaseException:
                await self._rollback(db)
                raise
            return _row_to_record(await self._require_row(db, template_id, expected_revision + 1))

    async def archive(self, template_id: str, expected_revision: int) -> AgentTemplateRecord:
        """归档整个模板（该 ``template_id`` 的所有版本写同一时间戳）。

        已归档且 revision 匹配时幂等返回最新行。

        Raises:
            TemplateNotFoundError: ``template_id`` 不存在。
            TemplateConflictError: revision 不一致。
        """
        async with self._lock:
            db = self._require_db()
            latest = await self._row_latest(db, template_id)
            if latest is None:
                raise TemplateNotFoundError(template_id)
            if int(latest["revision"]) != expected_revision:
                raise TemplateConflictError(template_id, int(latest["revision"]))
            if latest["archived_at"] is None:
                await db.execute(
                    "UPDATE agent_templates SET archived_at = ? "
                    "WHERE template_id = ? AND archived_at IS NULL",
                    (_now(), template_id),
                )
                await db.commit()
            return _row_to_record(await self._require_row(db, template_id, expected_revision))

    async def increment_reference(self, template_id: str, revision: int) -> None:
        """把``(template_id, revision)`` 这一行的 ``reference_count`` 加一。

        只由实例化路径在 Core 创建成功后调用；不做上限与去重。
        """
        async with self._lock:
            db = self._require_db()
            await db.execute(
                "UPDATE agent_templates SET reference_count = reference_count + 1 "
                "WHERE template_id = ? AND revision = ?",
                (template_id, revision),
            )
            await db.commit()

    # ─── 读取 ───

    async def get(
        self, template_id: str, *, include_archived: bool = True
    ) -> AgentTemplateRecord | None:
        """取该模板的**最新版本**（默认含归档：``template_origin`` 可悬空读取）。"""
        async with self._lock:
            db = self._require_db()
            row = await self._row_latest(db, template_id)
        if row is None:
            return None
        if not include_archived and row["archived_at"] is not None:
            return None
        return _row_to_record(row)

    async def get_version(self, template_id: str, revision: int) -> AgentTemplateRecord | None:
        """按 ``(template_id, revision)`` 取指定版本。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT * FROM agent_templates WHERE template_id = ? AND revision = ?",
                (template_id, revision),
            )
            row = await cursor.fetchone()
        return _row_to_record(row) if row is not None else None

    async def get_by_identity(
        self, namespace: str, name: str, *, include_archived: bool = True
    ) -> AgentTemplateRecord | None:
        """按业务身份 ``(namespace, name)`` 取最新版本。"""
        async with self._lock:
            db = self._require_db()
            row = await self._row_by_identity(db, namespace, name)
        if row is None:
            return None
        if not include_archived and row["archived_at"] is not None:
            return None
        return _row_to_record(row)

    async def versions(self, template_id: str) -> list[AgentTemplateRecord]:
        """该模板的全部版本（按 ``revision`` 升序）。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT * FROM agent_templates WHERE template_id = ? ORDER BY revision",
                (template_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]

    async def list(
        self,
        *,
        namespace: str | None = None,
        name: str | None = None,
        tag: str | None = None,
        deprecated: bool | None = None,
        include_archived: bool = False,
        include_history: bool = False,
    ) -> list[AgentTemplateRecord]:
        """列模板；默认只返回每个模板的最新版本。"""
        async with self._lock:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if not include_history:
                clauses.append("is_latest = 1")
            if not include_archived:
                clauses.append("archived_at IS NULL")
            if namespace is not None:
                clauses.append("json_extract(metadata, '$.namespace') = ?")
                params.append(namespace)
            if name is not None:
                clauses.append("json_extract(metadata, '$.name') = ?")
                params.append(name)
            if deprecated is not None:
                clauses.append("json_extract(metadata, '$.deprecated') = ?")
                params.append(1 if deprecated else 0)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM agent_templates {where} "  # noqa: S608
                "ORDER BY json_extract(metadata, '$.name'), "
                "json_extract(metadata, '$.namespace'), revision",
                tuple(params),
            )
            rows = await cursor.fetchall()
            records = [_row_to_record(row) for row in rows]
        if tag is not None:
            records = [record for record in records if tag in record.metadata.tags]
        return records

    # ─── 内部 ───

    async def _row_latest(self, db: aiosqlite.Connection, template_id: str) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM agent_templates WHERE template_id = ? AND is_latest = 1",
            (template_id,),
        )
        return await cursor.fetchone()

    async def _row_by_identity(
        self, db: aiosqlite.Connection, namespace: str, name: str
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM agent_templates "
            f"WHERE ({_IDENTITY_EXPR}) = (?, ?) "  # noqa: S608
            "ORDER BY revision DESC LIMIT 1",
            (namespace, name),
        )
        return await cursor.fetchone()

    async def _require_row(
        self, db: aiosqlite.Connection, template_id: str, revision: int
    ) -> aiosqlite.Row:
        cursor = await db.execute(
            "SELECT * FROM agent_templates WHERE template_id = ? AND revision = ?",
            (template_id, revision),
        )
        row = await cursor.fetchone()
        if row is None:  # pragma: no cover - 同一事务内刚写入
            raise TemplateNotFoundError(f"{template_id}#{revision}")
        return row


def _new_template_id() -> str:
    """模板身份（uuid4 hex）；跨版本复用同一值。"""
    from uuid import uuid4

    return uuid4().hex
