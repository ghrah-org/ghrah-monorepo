# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""DesiredStateRecord + DesiredStateStore：ProjectStore desired-state 的派生缓存。

单行全量快照（单 subject 单行覆盖，无乐观锁），镜像 TaskStore/ProjectStore 的
aiosqlite 连接管理范式。``save`` 时由 ``projects`` 派生 ``agents``（跨 project
汇总去重），简化调用方（ProjectUnit 仅需传 projects）。

写入触发：ProjectUnit 每次成功变更命令后调 ``save`` 全量快照。
RecoveryUnit 启动时从 ProjectStore 重建；本缓存不参与反向恢复覆盖。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field, field_serializer, field_validator

from ghrah.subject.project.models import AgentSpec, ProjectRecord

logger = logging.getLogger(__name__)

__all__ = ["DesiredStateRecord", "DesiredStateStore", "UnitSpec"]


class UnitSpec(BaseModel):
    """应启动的 unit 摘要（MVP：内置 unit 固定，仅记录第三方 enabled 占位）。"""

    name: str
    enabled: bool = True


class DesiredStateRecord(BaseModel):
    """Subject 全栈 desired-state 快照（单 subject 单行全量）。

    Attributes:
        subject_id: 对账归属 subject 标识（MVP 单 subject，固定 "default"）。
        units: 应启动的第三方 unit（MVP 空列表，内置 unit 固定）。
        projects: 应存活的 project + 其 agent desired-state（全量）。
        agents: 跨 project 汇总的 agent desired-state（save 时由 projects 派生）。
        updated_at: 快照时间。
    """

    subject_id: str
    units: list[UnitSpec] = Field(default_factory=list)
    projects: list[ProjectRecord] = Field(default_factory=list)
    agents: list[AgentSpec] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_serializer("updated_at")
    def _ser_dt(self, value: datetime) -> str:
        return value.isoformat()

    @field_validator("updated_at", mode="before")
    @classmethod
    def _coerce_dt(cls, value: Any) -> Any:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    def derive_agents(self) -> list[AgentSpec]:
        """跨 project 扁平化 agent；优先按 UUID，旧记录按 project/cluster/name。"""
        seen: set[str] = set()
        result: list[AgentSpec] = []
        for project in self.projects:
            for agent in project.agents:
                identity = agent.agent_id or (f"legacy:{project.project_id}:{agent.name}")
                if identity in seen:
                    continue
                seen.add(identity)
                result.append(agent)
        return result


_DDL = """
CREATE TABLE IF NOT EXISTS subject_desired_state (
    subject_id  TEXT PRIMARY KEY,
    snapshot    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _record_to_snapshot(record: DesiredStateRecord) -> tuple[str, str, str]:
    """DesiredStateRecord → (subject_id, snapshot json, updated_at ISO str)。

    save 时由 projects 派生 agents（决策：调用方仅传 projects，派生在此完成）。
    """
    record.agents = record.derive_agents()
    snapshot = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
    return record.subject_id, snapshot, record.updated_at.isoformat()


def _row_to_record(row: aiosqlite.Row) -> DesiredStateRecord:
    """列 dict → DesiredStateRecord（snapshot json.loads，updated_at 经 validator 还原）。"""
    d = dict(row)
    snapshot = d.get("snapshot")
    if isinstance(snapshot, str):
        parsed = json.loads(snapshot)
        return DesiredStateRecord.model_validate(parsed)
    return DesiredStateRecord.model_validate(d)


class DesiredStateStore:
    """SQLite DesiredStateStore：独立 aiosqlite 连接，单行全量快照，幂等 DDL。

    无乐观锁（单 subject 单行覆盖）。复用 ProjectStore 的连接管理骨架。
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
            self._db = db
        logger.debug("DesiredStateStore started (db=%s)", self._db_path)

    async def stop(self) -> None:
        async with self._lock:
            db = self._db
            if db is None:
                return
            self._db = None
            await db.close()
        logger.debug("DesiredStateStore stopped (db=%s)", self._db_path)

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("DesiredStateStore not started. Call start() first.")
        return self._db

    # ─── 读写 ───

    async def load(self, subject_id: str) -> DesiredStateRecord | None:
        """加载某 subject 的 desired-state 快照；不存在返回 None。"""
        async with self._lock:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT * FROM subject_desired_state WHERE subject_id = ?",
                (subject_id,),
            )
            row = await cursor.fetchone()
            return _row_to_record(row) if row is not None else None

    async def save(self, record: DesiredStateRecord) -> None:
        """全量覆盖某 subject 的 desired-state 快照（INSERT OR REPLACE）。

        save 时由 ``projects`` 派生 ``agents``（跨 project 汇总去重）。
        """
        subject_id, snapshot, updated_at = _record_to_snapshot(record)
        async with self._lock:
            db = self._require_db()
            await db.execute(
                "INSERT OR REPLACE INTO subject_desired_state "
                "(subject_id, snapshot, updated_at) VALUES (?, ?, ?)",
                (subject_id, snapshot, updated_at),
            )

    async def clear(self, subject_id: str) -> None:
        """删除某 subject 的 desired-state 快照。"""
        async with self._lock:
            db = self._require_db()
            await db.execute(
                "DELETE FROM subject_desired_state WHERE subject_id = ?",
                (subject_id,),
            )
