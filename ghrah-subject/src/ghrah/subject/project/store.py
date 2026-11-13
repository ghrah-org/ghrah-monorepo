# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ProjectStore：SQLite 持久化 subject_projects 元数据表。

独立 aiosqlite 连接到主库（``ctx.config.persistence.db_path``，由调用方传入）。
``subject_projects`` 存全局 project 注册表；本 store 不连接 Project Root 内业务 DB。
JSON 列保存复数资源（workspaces/agents/task_ids/isolation/config）；
``recovery`` 存单值字符串（``RecoveryAction.value``，利于 SQL 过滤）。

模式镜像 ``ghrah.subject.task.store.TaskStore``：乐观锁 + 显式归档 + 幂等 DDL。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from ghrah.protocol.types import ProjectStatus

from ghrah.subject.project.models import ProjectRecord

logger = logging.getLogger(__name__)

__all__ = [
    "ConcurrentModificationError",
    "ProjectNotFoundError",
    "ProjectRecordList",
    "ProjectStore",
]

# 模块级类型别名：避免实例方法 `list` 遮蔽内置 list 在注解中报 valid-type。
ProjectRecordList = list[ProjectRecord]


class ConcurrentModificationError(Exception):
    """乐观锁：expected_version 与当前 version 不匹配。"""


class ProjectNotFoundError(Exception):
    """project_id 不存在。"""


_DDL = """
CREATE TABLE IF NOT EXISTS subject_schema_versions (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS subject_projects (
    project_id            TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT NOT NULL DEFAULT '',
    project_root_locator  TEXT NOT NULL DEFAULT '',
    shell_cwd             TEXT NOT NULL DEFAULT '',
    manifest_ref          TEXT NOT NULL DEFAULT '',
    workspaces            TEXT NOT NULL DEFAULT '[]',
    agents                TEXT NOT NULL DEFAULT '[]',
    task_ids              TEXT NOT NULL DEFAULT '[]',
    isolation             TEXT NOT NULL DEFAULT '{}',
    status                TEXT NOT NULL,
    recovery              TEXT NOT NULL DEFAULT 'resume',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    version               INTEGER NOT NULL DEFAULT 1,
    archived_at           TEXT,
    deleted_at            TEXT,
    config                TEXT
);
CREATE INDEX IF NOT EXISTS idx_subject_projects_status  ON subject_projects(status);
CREATE INDEX IF NOT EXISTS idx_subject_projects_deleted ON subject_projects(deleted_at);
"""

_COLUMNS = (
    "project_id",
    "name",
    "description",
    "project_root_locator",
    "shell_cwd",
    "manifest_ref",
    "workspaces",
    "agents",
    "task_ids",
    "isolation",
    "status",
    "recovery",
    "created_at",
    "updated_at",
    "version",
    "archived_at",
    "deleted_at",
    "config",
)
# JSON 列：复数资源与结构化字段经 json.dumps/json.loads 往返。
# recovery 不在此列（单值字符串列）。
_JSON_COLUMNS = ("workspaces", "agents", "task_ids", "isolation", "config")
_SCHEMA_COMPONENT = "project_store"
_SCHEMA_VERSION = 5
_MIGRATION_COLUMNS: dict[int, tuple[str, str]] = {
    1: ("description", "TEXT NOT NULL DEFAULT ''"),
    2: ("project_root_locator", "TEXT NOT NULL DEFAULT ''"),
    3: ("archived_at", "TEXT"),
    4: ("config", "TEXT"),
    5: ("shell_cwd", "TEXT NOT NULL DEFAULT ''"),
}


def _record_to_row(record: ProjectRecord) -> dict[str, Any]:
    """ProjectRecord → 列 dict（JSON 字段 json.dumps，recovery 取 on_restart.value，
    其余直存）。"""
    d = record.model_dump(mode="json")
    # model_dump(mode="json") 经 field_serializer 已将 recovery 序列为 str；
    # 显式取值以兜底序列化路径变更后漂移。
    d["recovery"] = record.recovery.on_restart.value
    for col in _JSON_COLUMNS:
        d[col] = json.dumps(d[col])
    # 时间戳经 field_serializer 已为 ISO str（model_dump(mode="json")）；
    # 时间字段为 str | None，直接存。
    return d


def _row_to_record(row: aiosqlite.Row) -> ProjectRecord:
    """列 dict → ProjectRecord（JSON 字段 json.loads，时间戳经 validator 还原，
    recovery 经 validator 还原为 RecoverySpec）。"""
    d = dict(row)
    for col in _JSON_COLUMNS:
        if d[col] is not None:
            d[col] = json.loads(d[col])
    # recovery：str → RecoverySpec（field_validator mode="before" 处理）
    # status：str → ProjectStatus（ProjectInfoPayload.status 字段为 ProjectStatus，
    #   Pydantic 自动 coerce str → StrEnum）
    return ProjectRecord.model_validate(d)


class ProjectStore:
    """SQLite ProjectStore：独立 aiosqlite 连接，幂等 DDL，乐观锁 + 软删。

    store 仅负责持久化与 CRUD；跨 project 校验（workspace locator 禁嵌套、
    default mount 唯一）由 manager 层在写入前完成。``exists_workspace_mounted``
    提供跨 project workspace 复用检测供 manager 复用。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ─── 连接管理 ───

    async def start(self) -> None:
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
        logger.debug("ProjectStore started (db=%s)", self._db_path)

    async def _migrate(self, db: aiosqlite.Connection) -> None:
        """按 component version 幂等补齐 ProjectStore schema。"""
        cursor = await db.execute(
            "SELECT version FROM subject_schema_versions WHERE component = ?",
            (_SCHEMA_COMPONENT,),
        )
        row = await cursor.fetchone()
        current = int(row["version"]) if row is not None else 0
        if current > _SCHEMA_VERSION:
            raise RuntimeError(
                f"ProjectStore schema {current} is newer than supported {_SCHEMA_VERSION}"
            )
        columns_cursor = await db.execute("PRAGMA table_info(subject_projects)")
        known_columns = {str(r["name"]) for r in await columns_cursor.fetchall()}
        for version in range(1, _SCHEMA_VERSION + 1):
            column, declaration = _MIGRATION_COLUMNS[version]
            if column not in known_columns:
                await db.execute(
                    f"ALTER TABLE subject_projects ADD COLUMN {column} {declaration}"  # noqa: S608
                )
                known_columns.add(column)
            if version <= current:
                continue
            await db.execute(
                "INSERT INTO subject_schema_versions(component, version) VALUES (?, ?) "
                "ON CONFLICT(component) DO UPDATE SET version = excluded.version",
                (_SCHEMA_COMPONENT, version),
            )
        # 旧 soft-delete 即归档。迁移后新代码只写 archived_at；清空
        # deleted_at，避免同一记录同时落入“旧删除”和“新归档”两套过滤轴。
        await db.execute(
            "UPDATE subject_projects SET archived_at = deleted_at, deleted_at = NULL, "
            "status = 'stopped' "
            "WHERE deleted_at IS NOT NULL AND archived_at IS NULL"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subject_projects_archived "
            "ON subject_projects(archived_at)"
        )

    async def stop(self) -> None:
        async with self._lock:
            db = self._db
            if db is None:
                return
            self._db = None
            await db.close()
        logger.debug("ProjectStore stopped (db=%s)", self._db_path)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ProjectStore not started. Call start() first.")
        return self._db

    # ─── 写入 ───

    async def upsert(self, record: ProjectRecord) -> None:
        """INSERT OR REPLACE（新 project 或全量覆盖，version 由调用方控制）。"""
        async with self._lock:
            db = self._require_db()
            row = _record_to_row(record)
            placeholders = ", ".join(["?"] * len(_COLUMNS))
            cols = ", ".join(_COLUMNS)
            await db.execute(
                f"INSERT OR REPLACE INTO subject_projects ({cols}) "  # noqa: S608
                f"VALUES ({placeholders})",
                tuple(row[c] for c in _COLUMNS),
            )

    async def update(
        self,
        project_id: str,
        expected_version: int,
        mutator: Callable[[ProjectRecord], ProjectRecord],
    ) -> ProjectRecord:
        """读出 → 校验 expected_version → 应用 mutator → version+1 → 写回。

        store 强制覆盖 ``version``（= original + 1）与 ``updated_at``（= now），
        mutator 不得自行设置这两个字段（即使设置也会被覆盖）。mutator 应使用
        ``model_copy(update=...)`` 非突变风格返回新记录，避免共享可变状态。

        Raises:
            ProjectNotFoundError: project_id 不存在。
            ConcurrentModificationError: expected_version 与当前 version 不匹配，
                或写回时并发已被改（行数 != 1）。
        """
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT * FROM subject_projects WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ProjectNotFoundError(f"Project {project_id} not found")
            current = _row_to_record(row)
            if current.version != expected_version:
                raise ConcurrentModificationError(
                    f"Project {project_id} modified: expected version "
                    f"{expected_version}, got {current.version}"
                )
            original_version = current.version
            updated = mutator(current)
            updated.version = original_version + 1
            updated.updated_at = datetime.now(UTC)
            new_row = _record_to_row(updated)
            placeholders = ", ".join([f"{c} = ?" for c in _COLUMNS])
            cursor = await db.execute(
                f"UPDATE subject_projects SET {placeholders} "  # noqa: S608
                "WHERE project_id = ? AND version = ?",
                tuple(new_row[c] for c in _COLUMNS) + (project_id, original_version),
            )
            if cursor.rowcount != 1:
                raise ConcurrentModificationError(
                    f"Project {project_id} concurrent modification during update"
                )
            return updated

    async def soft_delete(self, project_id: str, expected_version: int) -> None:
        """旧调用兼容：写 deleted_at；仅供迁移测试，不用于新生命周期。

        Raises:
            ProjectNotFoundError / ConcurrentModificationError: 同 update。
        """

        def _mark(record: ProjectRecord) -> ProjectRecord:
            return record.model_copy(update={"deleted_at": datetime.now(UTC)})

        await self.update(project_id, expected_version, _mark)

    async def archive(self, project_id: str, expected_version: int) -> ProjectRecord:
        """设置 archived_at 并固定运行状态为 stopped。"""

        def _archive(record: ProjectRecord) -> ProjectRecord:
            return record.model_copy(
                update={
                    "archived_at": datetime.now(UTC),
                    "status": ProjectStatus.STOPPED,
                    "deleted_at": None,
                }
            )

        return await self.update(project_id, expected_version, _archive)

    async def restore(self, project_id: str, expected_version: int) -> ProjectRecord:
        """清除 archived_at；恢复后保持 stopped，不自动启动运行资源。"""

        def _restore(record: ProjectRecord) -> ProjectRecord:
            return record.model_copy(update={"archived_at": None, "status": ProjectStatus.STOPPED})

        return await self.update(project_id, expected_version, _restore)

    async def hard_delete(self, project_id: str, expected_version: int) -> bool:
        """按版本锁永久删除 catalog 记录；Project Root 由 manager 先行 purge。"""

        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT version FROM subject_projects WHERE project_id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            current_version = int(row["version"])
            if current_version != expected_version:
                raise ConcurrentModificationError(
                    f"Project {project_id} modified: expected version "
                    f"{expected_version}, got {current_version}"
                )
            cursor = await db.execute(
                "DELETE FROM subject_projects WHERE project_id = ? AND version = ?",
                (project_id, expected_version),
            )
            return cursor.rowcount == 1

    async def delete_uncommitted(self, project_id: str) -> None:
        """创建事务补偿：硬删除尚未对外提交的 ProjectRecord。"""
        async with self._lock:
            db = self._require_db()
            await db.execute("DELETE FROM subject_projects WHERE project_id = ?", (project_id,))

    # ─── 读取 ───

    async def get(
        self,
        project_id: str,
        *,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ProjectRecord | None:
        """按 project_id 取记录；普通业务默认排除 archived/旧 deleted。"""
        async with self._lock:
            db = self._require_db()
            query = "SELECT * FROM subject_projects WHERE project_id = ?"
            params: list[Any] = [project_id]
            if not include_archived:
                query += " AND archived_at IS NULL"
            if not include_deleted:
                query += " AND deleted_at IS NULL"
            cursor = await db.execute(query, tuple(params))
            row = await cursor.fetchone()
            return _row_to_record(row) if row is not None else None

    async def list(
        self,
        *,
        status: ProjectStatus | None = None,
        archived: bool | None = False,
        include_deleted: bool = False,
    ) -> ProjectRecordList:
        """列出 Project；archived=False/True/None 对应 active/archived/all。"""
        async with self._lock:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if not include_deleted:
                clauses.append("deleted_at IS NULL")
            if archived is False:
                clauses.append("archived_at IS NULL")
            elif archived is True:
                clauses.append("archived_at IS NOT NULL")
            if status is not None:
                clauses.append("status = ?")
                params.append(status.value)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM subject_projects {where} ORDER BY created_at",  # noqa: S608
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [_row_to_record(r) for r in rows]

    async def exists(self, project_id: str) -> bool:
        """project_id 是否存在（排除软删）。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT 1 FROM subject_projects WHERE project_id = ? "
                "AND archived_at IS NULL AND deleted_at IS NULL",
                (project_id,),
            )
            return await cursor.fetchone() is not None

    async def exists_workspace_mounted(self, workspace_id: str) -> bool:
        """workspace_id 是否被任一未软删 project 挂载。

        扫所有未软删 record 的 workspaces JSON 列，判断 workspace_id 是否出现
        在任一 project 的挂载列表中。供 manager 跨 project workspace 复用检测。
        """
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT workspaces FROM subject_projects WHERE deleted_at IS NULL"
            )
            rows = await cursor.fetchall()
            for r in rows:
                mounts = json.loads(r["workspaces"])
                for m in mounts:
                    if m.get("workspace_id") == workspace_id:
                        return True
            return False
