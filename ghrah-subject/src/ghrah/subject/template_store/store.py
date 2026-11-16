# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""TemplateStore：``agent_templates`` 与 ``agent_template_revisions`` 的持久化。

独立 aiosqlite 连接（同一 ``config.persistence.db_path``），schema component
``agent_template_store`` v1。**权威边界**：``agent_templates`` 表是本主题的
唯一权威；``ManifestStore`` 的 YAML 与协议层 ``AgentTemplateSpec`` 都不是，
同一模板事实不得双写。

实现约束（照实况）：

* DDL 第一条自建 ``subject_schema_versions``：本 store 是独立连接，不能假设
  ``ProjectStore`` 先启动（coexistence profile 不挂 ProjectUnit）。
* 版本写入用 upsert + ``current > 1`` fail-fast，与 ``project/store.py`` 同形。
* 同库多连接有锁风险：本实例用 ``asyncio.Lock`` 串行化读写；不要并发
  ``start()`` 两个指向同一库文件的 store。
* 创建/成功编辑把主表行与修订快照写在**同一事务**；归档只写
  ``archived_at``（不改 revision、不写快照、不可撤销，重复归档幂等）。
* CAS 用条件 ``UPDATE ... WHERE template_id = ? AND revision = ?`` 的
  ``rowcount`` 判定，不做「先读后写」。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from ghrah.subject.template_store.models import (
    AgentTemplateAbilityRequest,
    AgentTemplateConfig,
    AgentTemplateContextPolicy,
    AgentTemplateMetadata,
    AgentTemplateModel,
    AgentTemplatePathRequest,
    AgentTemplatePermissionRequest,
    AgentTemplatePrompt,
    AgentTemplateRecord,
    AgentTemplateSnapshotSummary,
    snapshot_digest,
)

__all__ = [
    "TemplateConflictError",
    "TemplateIdentityConflictError",
    "TemplateNotFoundError",
    "TemplateStore",
    "TemplateStoreError",
]

_SCHEMA_COMPONENT = "agent_template_store"
_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS subject_schema_versions (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_templates (
    template_id        TEXT PRIMARY KEY,
    revision           INTEGER NOT NULL DEFAULT 1,
    namespace          TEXT NOT NULL,
    name               TEXT NOT NULL,
    title              TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    tags               TEXT NOT NULL DEFAULT '[]',
    icon               TEXT NOT NULL DEFAULT '',
    deprecated         INTEGER NOT NULL DEFAULT 0,
    model              TEXT NOT NULL DEFAULT '{}',
    prompt             TEXT NOT NULL DEFAULT '{}',
    context_policy     TEXT NOT NULL DEFAULT '{}',
    ability_request    TEXT NOT NULL DEFAULT '{}',
    permission_request TEXT NOT NULL DEFAULT '{}',
    path_request       TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    archived_at        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_templates_identity
    ON agent_templates(namespace, name) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_templates_namespace ON agent_templates(namespace);
CREATE INDEX IF NOT EXISTS idx_agent_templates_archived ON agent_templates(archived_at);
CREATE TABLE IF NOT EXISTS agent_template_revisions (
    template_id TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    snapshot    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (template_id, revision)
);
"""

_COLUMNS = (
    "template_id",
    "revision",
    "namespace",
    "name",
    "title",
    "description",
    "tags",
    "icon",
    "deprecated",
    "model",
    "prompt",
    "context_policy",
    "ability_request",
    "permission_request",
    "path_request",
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
    """``(namespace, name)`` 在未归档范围内已被占用。"""

    def __init__(self, namespace: str, name: str) -> None:
        self.namespace = namespace
        self.name = name
        super().__init__(f"模板身份已存在：{namespace}/{name}")


class TemplateConflictError(TemplateStoreError):
    """``expected_revision`` 与当前 revision 不一致，或模板已归档。"""

    def __init__(self, template_id: str, current_revision: int, *, archived: bool = False) -> None:
        self.template_id = template_id
        self.current_revision = current_revision
        self.archived = archived
        reason = "模板已归档，不能再编辑" if archived else "模板 revision 冲突"
        super().__init__(f"{reason}：{template_id}（current_revision={current_revision}）")


def _now() -> str:
    """当前时间（RFC 3339 UTC，``...Z``；列与 wire 同一形状）。"""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _record_to_row(record: AgentTemplateRecord) -> dict[str, Any]:
    """记录 → 列 dict（JSON 列 json.dumps；``deprecated`` 存 0/1）。"""
    return {
        "template_id": record.template_id,
        "revision": record.revision,
        "namespace": record.metadata.namespace,
        "name": record.metadata.name,
        "title": record.metadata.title,
        "description": record.metadata.description,
        "tags": json.dumps(record.metadata.tags, ensure_ascii=False),
        "icon": record.metadata.icon,
        "deprecated": 1 if record.metadata.deprecated else 0,
        "model": json.dumps(record.model.model_dump(mode="json"), ensure_ascii=False),
        "prompt": json.dumps(record.prompt.model_dump(mode="json"), ensure_ascii=False),
        "context_policy": json.dumps(
            record.context_policy.model_dump(mode="json"), ensure_ascii=False
        ),
        "ability_request": json.dumps(
            record.ability_request.model_dump(mode="json"), ensure_ascii=False
        ),
        "permission_request": json.dumps(
            record.permission_request.model_dump(mode="json"), ensure_ascii=False
        ),
        "path_request": json.dumps(
            [item.model_dump(mode="json") for item in record.path_request],
            ensure_ascii=False,
        ),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "archived_at": record.archived_at,
    }


def _row_to_record(row: aiosqlite.Row) -> AgentTemplateRecord:
    """列 dict → 记录（JSON 列 json.loads；``deprecated`` 还原为 bool）。"""
    return AgentTemplateRecord(
        template_id=str(row["template_id"]),
        revision=int(row["revision"]),
        metadata=AgentTemplateMetadata(
            namespace=str(row["namespace"]),
            name=str(row["name"]),
            title=str(row["title"]),
            description=str(row["description"]),
            tags=json.loads(row["tags"]),
            icon=str(row["icon"]),
            deprecated=bool(row["deprecated"]),
        ),
        model=AgentTemplateModel.model_validate(json.loads(row["model"])),
        prompt=AgentTemplatePrompt.model_validate(json.loads(row["prompt"])),
        context_policy=AgentTemplateContextPolicy.model_validate(json.loads(row["context_policy"])),
        ability_request=AgentTemplateAbilityRequest.model_validate(
            json.loads(row["ability_request"])
        ),
        permission_request=AgentTemplatePermissionRequest.model_validate(
            json.loads(row["permission_request"])
        ),
        path_request=[
            AgentTemplatePathRequest.model_validate(item)
            for item in json.loads(row["path_request"])
        ],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=row["archived_at"],
    )


class TemplateStore:
    """SQLite TemplateStore：独立连接、幂等 DDL、CAS 与修订快照。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ─── 连接管理 ───

    async def start(self) -> None:
        """建连接、执行幂等 DDL 并登记 component 版本。"""
        async with self._lock:
            if self._db is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(str(self._db_path), isolation_level=None)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(_DDL)
            await self._migrate(db)
            self._db = db

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """按 component version 幂等登记；库比代码新时 fail-fast。"""
        cursor = await db.execute(
            "SELECT version FROM subject_schema_versions WHERE component = ?",
            (_SCHEMA_COMPONENT,),
        )
        row = await cursor.fetchone()
        current = int(row["version"]) if row is not None else 0
        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"TemplateStore schema {current} is newer than supported {_SCHEMA_VERSION}"
            )
        if current < _SCHEMA_VERSION:
            await db.execute(
                "INSERT INTO subject_schema_versions(component, version) VALUES (?, ?) "
                "ON CONFLICT(component) DO UPDATE SET version = excluded.version",
                (_SCHEMA_COMPONENT, _SCHEMA_VERSION),
            )

    async def stop(self) -> None:
        """关闭连接（幂等）。"""
        async with self._lock:
            db = self._db
            if db is None:
                return
            self._db = None
            await db.close()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TemplateStore not started. Call start() first.")
        return self._db

    async def _rollback(self, db: aiosqlite.Connection) -> None:
        try:
            await db.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - 事务已结束时无副作用
            pass

    async def _row(self, db: aiosqlite.Connection, template_id: str) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM agent_templates WHERE template_id = ?", (template_id,)
        )
        return await cursor.fetchone()

    # ─── 写入 ───

    async def create(self, config: AgentTemplateConfig) -> AgentTemplateRecord:
        """新建模板：主表行 + revision 1 快照同事务写入。

        Raises:
            TemplateIdentityConflictError: ``(namespace, name)`` 未归档范围已占用。
        """
        async with self._lock:
            db = self._require_db()
            now = _now()
            record = AgentTemplateRecord(
                template_id=uuid4().hex,
                revision=1,
                metadata=config.metadata,
                model=config.model,
                prompt=config.prompt,
                context_policy=config.context_policy,
                ability_request=config.ability_request,
                permission_request=config.permission_request,
                path_request=config.path_request,
                created_at=now,
                updated_at=now,
                archived_at=None,
            )
            row = _record_to_row(record)
            placeholders = ", ".join(["?"] * len(_COLUMNS))
            columns = ", ".join(_COLUMNS)
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    f"INSERT INTO agent_templates ({columns}) VALUES ({placeholders})",  # noqa: S608
                    tuple(row[column] for column in _COLUMNS),
                )
                await db.execute(
                    "INSERT INTO agent_template_revisions(template_id, revision, snapshot, "
                    "created_at) VALUES (?, ?, ?, ?)",
                    (record.template_id, record.revision, config.snapshot_json(), now),
                )
                await db.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                await self._rollback(db)
                message = str(error)
                if (
                    "uq_agent_templates_identity" in message
                    or "agent_templates.namespace" in message
                ):
                    raise TemplateIdentityConflictError(
                        config.metadata.namespace, config.metadata.name
                    ) from error
                raise
            except BaseException:
                await self._rollback(db)
                raise
            return record

    async def update(
        self, template_id: str, expected_revision: int, config: AgentTemplateConfig
    ) -> AgentTemplateRecord:
        """按 CAS 更新模板：revision+1、写快照、刷新 ``updated_at``。

        Raises:
            TemplateNotFoundError: ``template_id`` 不存在。
            TemplateConflictError: revision 不一致或模板已归档。
        """
        async with self._lock:
            db = self._require_db()
            now = _now()
            existing = await self._row(db, template_id)
            if existing is None:
                raise TemplateNotFoundError(template_id)
            if existing["archived_at"] is not None:
                raise TemplateConflictError(template_id, int(existing["revision"]), archived=True)
            row = _record_to_row(
                AgentTemplateRecord(
                    template_id=template_id,
                    revision=expected_revision + 1,
                    metadata=config.metadata,
                    model=config.model,
                    prompt=config.prompt,
                    context_policy=config.context_policy,
                    ability_request=config.ability_request,
                    permission_request=config.permission_request,
                    path_request=config.path_request,
                    created_at=str(existing["created_at"]),
                    updated_at=now,
                    archived_at=None,
                )
            )
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "UPDATE agent_templates SET revision = ?, namespace = ?, name = ?, "
                    "title = ?, description = ?, tags = ?, icon = ?, deprecated = ?, "
                    "model = ?, prompt = ?, context_policy = ?, ability_request = ?, "
                    "permission_request = ?, path_request = ?, updated_at = ? "
                    "WHERE template_id = ? AND revision = ?",
                    (
                        expected_revision + 1,
                        row["namespace"],
                        row["name"],
                        row["title"],
                        row["description"],
                        row["tags"],
                        row["icon"],
                        row["deprecated"],
                        row["model"],
                        row["prompt"],
                        row["context_policy"],
                        row["ability_request"],
                        row["permission_request"],
                        row["path_request"],
                        now,
                        template_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    await self._rollback(db)
                    fresh = await self._row(db, template_id)
                    if fresh is None:
                        raise TemplateNotFoundError(template_id)
                    raise TemplateConflictError(template_id, int(fresh["revision"]))
                await db.execute(
                    "INSERT INTO agent_template_revisions(template_id, revision, snapshot, "
                    "created_at) VALUES (?, ?, ?, ?)",
                    (template_id, expected_revision + 1, config.snapshot_json(), now),
                )
                await db.execute("COMMIT")
            except TemplateConflictError, TemplateNotFoundError:
                raise
            except BaseException:
                await self._rollback(db)
                raise
            updated = await self._row(db, template_id)
            if updated is None:  # pragma: no cover - 同一事务内刚写入
                raise TemplateNotFoundError(template_id)
            return _row_to_record(updated)

    async def archive(self, template_id: str, expected_revision: int) -> AgentTemplateRecord:
        """归档：只写 ``archived_at``（不改 revision、不写快照）。

        已归档且 revision 匹配时幂等返回当前视图。

        Raises:
            TemplateNotFoundError: ``template_id`` 不存在。
            TemplateConflictError: revision 不一致。
        """
        async with self._lock:
            db = self._require_db()
            existing = await self._row(db, template_id)
            if existing is None:
                raise TemplateNotFoundError(template_id)
            if existing["archived_at"] is not None:
                if int(existing["revision"]) == expected_revision:
                    return _row_to_record(existing)
                raise TemplateConflictError(template_id, int(existing["revision"]))
            cursor = await db.execute(
                "UPDATE agent_templates SET archived_at = ? "
                "WHERE template_id = ? AND revision = ? AND archived_at IS NULL",
                (_now(), template_id, expected_revision),
            )
            if cursor.rowcount != 1:
                fresh = await self._row(db, template_id)
                if fresh is None:
                    raise TemplateNotFoundError(template_id)
                raise TemplateConflictError(template_id, int(fresh["revision"]))
            archived = await self._row(db, template_id)
            if archived is None:  # pragma: no cover - 刚更新成功，行必在
                raise TemplateNotFoundError(template_id)
            return _row_to_record(archived)

    # ─── 读取 ───

    async def get(
        self, template_id: str, *, include_archived: bool = True
    ) -> AgentTemplateRecord | None:
        """按 ``template_id`` 取记录（默认含归档：``template_origin`` 可悬空读取）。"""
        async with self._lock:
            db = self._require_db()
            query = "SELECT * FROM agent_templates WHERE template_id = ?"
            if not include_archived:
                query += " AND archived_at IS NULL"
            cursor = await db.execute(query, (template_id,))
            row = await cursor.fetchone()
            return _row_to_record(row) if row is not None else None

    async def get_by_identity(
        self, namespace: str, name: str, *, include_archived: bool = False
    ) -> AgentTemplateRecord | None:
        """按自然键 ``(namespace, name)`` 取记录；默认只看未归档。"""
        async with self._lock:
            db = self._require_db()
            query = "SELECT * FROM agent_templates WHERE namespace = ? AND name = ?"
            if not include_archived:
                query += " AND archived_at IS NULL"
            query += " ORDER BY created_at DESC LIMIT 1"
            cursor = await db.execute(query, (namespace, name))
            row = await cursor.fetchone()
            return _row_to_record(row) if row is not None else None

    async def get_revision(self, template_id: str, revision: int) -> dict[str, Any] | None:
        """取指定 revision 的规范化快照对象；不存在返回 None。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT snapshot FROM agent_template_revisions "
                "WHERE template_id = ? AND revision = ?",
                (template_id, revision),
            )
            row = await cursor.fetchone()
            return json.loads(row["snapshot"]) if row is not None else None

    async def list(
        self,
        *,
        namespace: str | None = None,
        name: str | None = None,
        tag: str | None = None,
        deprecated: bool | None = None,
        include_archived: bool = False,
    ) -> list[AgentTemplateRecord]:
        """列模板；``tag`` 在 Python 侧过滤（v0 模板数是两位数）。"""
        async with self._lock:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if not include_archived:
                clauses.append("archived_at IS NULL")
            if namespace is not None:
                clauses.append("namespace = ?")
                params.append(namespace)
            if name is not None:
                clauses.append("name = ?")
                params.append(name)
            if deprecated is not None:
                clauses.append("deprecated = ?")
                params.append(1 if deprecated else 0)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM agent_templates {where} ORDER BY created_at, template_id",  # noqa: S608
                tuple(params),
            )
            rows = await cursor.fetchall()
            records = [_row_to_record(row) for row in rows]
        if tag is not None:
            records = [record for record in records if tag in record.metadata.tags]
        return records

    async def history(self, template_id: str) -> list[AgentTemplateSnapshotSummary]:
        """修订历史（按 revision 升序，只给摘要不给快照正文）。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT revision, snapshot, created_at FROM agent_template_revisions "
                "WHERE template_id = ? ORDER BY revision",
                (template_id,),
            )
            rows = await cursor.fetchall()
        return [
            AgentTemplateSnapshotSummary(
                revision=int(row["revision"]),
                created_at=str(row["created_at"]),
                digest=snapshot_digest(str(row["snapshot"])),
                size_bytes=len(str(row["snapshot"]).encode("utf-8")),
            )
            for row in rows
        ]
