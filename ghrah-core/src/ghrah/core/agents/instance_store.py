# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 实例配置快照：Core 的运行期配置权威。

位置的唯一来源是 ``<project_root>/db/agents.sqlite3``（由装配层按项目根派生）。
本模块只负责持久化事实，不解释权限、不复制授权行——授权仍在项目的
``action-chains.sqlite3`` 关系库里。

为什么需要它：模板是「创建时的填充输入」，Agent 启动后配置以 Core 为准。
没有这份快照，重启就只能回读模板，等于把配置权威又交回 Subject。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

__all__ = [
    "AgentInstanceRecord",
    "AgentInstanceStore",
    "AgentInstanceStoreError",
    "SCHEMA_COMPONENT",
    "SCHEMA_VERSION",
]

logger = logging.getLogger(__name__)

SCHEMA_COMPONENT = "agent_instance_store"
"""本库独立的 schema 组件名；与 subject 的 ``project_store`` 版本互不影响。"""

SCHEMA_VERSION = 1
"""当前 schema 版本；读到更高版本一律 fail-fast，不做降级猜测。"""

_DDL = """
CREATE TABLE IF NOT EXISTS agent_schema_versions (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_instances (
    agent_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    config         TEXT NOT NULL,
    members        TEXT NOT NULL DEFAULT '[]',
    surface        TEXT,
    mcp_refs       TEXT NOT NULL DEFAULT '[]',
    incarnation_id TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
"""


class AgentInstanceStoreError(RuntimeError):
    """实例快照存储错误：版本不兼容或库未打开。"""


@dataclass(frozen=True)
class AgentInstanceRecord:
    """一个实例的持久配置。

    ``members`` 是创建时冻结的 L2 申请（逐成员权限），不是授权行；
    授权事实在关系库里，两者不互相复制。
    """

    agent_id: str
    name: str
    config: dict[str, Any]
    members: list[dict[str, Any]] = field(default_factory=list)
    surface: dict[str, Any] | None = None
    mcp_refs: list[str] = field(default_factory=list)
    incarnation_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_wire(self) -> dict[str, Any]:
        """快照的可序列化形态（读面之外不参与 wire 契约）。"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "config": dict(self.config),
            "members": [dict(item) for item in self.members],
            "surface": dict(self.surface) if self.surface is not None else None,
            "mcp_refs": list(self.mcp_refs),
            "incarnation_id": self.incarnation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentInstanceStore:
    """``agents.sqlite3`` 的单表存储；独立连接、幂等 DDL、版本 fail-fast。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        """快照库路径（诊断用；调用方不据此写别的表）。"""
        return self._path

    async def start(self) -> None:
        """建库建表并核对 schema 版本。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        try:
            await self._db.executescript(_DDL)
            await self._db.commit()
            await self._check_version()
        except BaseException:
            await self._db.close()
            self._db = None
            raise
        logger.debug("AgentInstanceStore started (db=%s)", self._path)

    async def close(self) -> None:
        """关闭连接；幂等。"""
        db = self._db
        self._db = None
        if db is not None:
            await db.close()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise AgentInstanceStoreError(f"agent instance store not started: {self._path}")
        return self._db

    async def _check_version(self) -> None:
        db = self._require_db()
        cursor = await db.execute(
            "SELECT version FROM agent_schema_versions WHERE component=?",
            (SCHEMA_COMPONENT,),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO agent_schema_versions(component, version) VALUES(?, ?)",
                (SCHEMA_COMPONENT, SCHEMA_VERSION),
            )
            await db.commit()
            return
        current = int(row["version"])
        if current > SCHEMA_VERSION:
            raise AgentInstanceStoreError(
                f"{SCHEMA_COMPONENT} schema version {current} newer than supported "
                f"{SCHEMA_VERSION}: {self._path}"
            )

    async def save(
        self,
        *,
        agent_id: str,
        name: str,
        config: dict[str, Any],
        members: list[dict[str, Any]] | None = None,
        surface: dict[str, Any] | None = None,
        mcp_refs: list[str] | None = None,
        incarnation_id: str | None = None,
    ) -> AgentInstanceRecord:
        """插入或更新一个实例快照；``created_at`` 保留首次写入时间。"""
        db = self._require_db()
        now = _now()
        cursor = await db.execute(
            "SELECT created_at FROM agent_instances WHERE agent_id=?",
            (agent_id,),
        )
        existing = await cursor.fetchone()
        created_at = str(existing["created_at"]) if existing is not None else now
        await db.execute(
            "INSERT INTO agent_instances("
            "agent_id, name, config, members, surface, mcp_refs, incarnation_id,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(agent_id) DO UPDATE SET"
            " name=excluded.name, config=excluded.config, members=excluded.members,"
            " surface=excluded.surface, mcp_refs=excluded.mcp_refs,"
            " incarnation_id=excluded.incarnation_id, updated_at=excluded.updated_at",
            (
                agent_id,
                name,
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                json.dumps(list(members or []), ensure_ascii=False, sort_keys=True),
                json.dumps(surface, ensure_ascii=False, sort_keys=True)
                if surface is not None
                else None,
                json.dumps(list(mcp_refs or []), ensure_ascii=False),
                incarnation_id,
                created_at,
                now,
            ),
        )
        await db.commit()
        return AgentInstanceRecord(
            agent_id=agent_id,
            name=name,
            config=dict(config),
            members=list(members or []),
            surface=dict(surface) if surface is not None else None,
            mcp_refs=list(mcp_refs or []),
            incarnation_id=incarnation_id,
            created_at=created_at,
            updated_at=now,
        )

    async def set_member_bindings(
        self, agent_id: str, bound: Collection[str]
    ) -> AgentInstanceRecord | None:
        """改写某实例成员快照的绑定轴；其余字段（含配置与表面）原样保留。

        绑定状态的权威就是这张表：成员条目里的 ``axes.available`` 承载
        "绑定为工具"（Core 内部按 ``MemberAxes.bind`` 读取），``visible`` 不动。
        成员不在快照里时忽略——绑定不能凭空创造成员。

        Args:
            agent_id: 实例标识。
            bound: 应处于绑定状态的成员全名集合。

        Returns:
            更新后的记录；实例不存在时返回 ``None``。
        """
        record = await self.get(agent_id)
        if record is None:
            return None
        wanted = set(bound)
        members: list[dict[str, Any]] = []
        for item in record.members:
            entry = dict(item)
            axes = dict(entry.get("axes") or {})
            name = str(entry.get("member") or "")
            axes["available"] = name in wanted
            entry["axes"] = axes
            members.append(entry)
        return await self.save(
            agent_id=agent_id,
            name=record.name,
            config=record.config,
            members=members,
            surface=record.surface,
            mcp_refs=record.mcp_refs,
            incarnation_id=record.incarnation_id,
        )

    async def get(self, agent_id: str) -> AgentInstanceRecord | None:
        """按 ``agent_id`` 读快照；不存在返回 ``None``（由调用方映射 NOT_FOUND）。"""
        db = self._require_db()
        cursor = await db.execute("SELECT * FROM agent_instances WHERE agent_id=?", (agent_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        surface = json.loads(row["surface"]) if row["surface"] else None
        return AgentInstanceRecord(
            agent_id=str(row["agent_id"]),
            name=str(row["name"]),
            config=json.loads(row["config"]),
            members=json.loads(row["members"]),
            surface=surface,
            mcp_refs=json.loads(row["mcp_refs"]),
            incarnation_id=row["incarnation_id"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    async def delete(self, agent_id: str) -> bool:
        """删除快照；返回是否真的删掉了一行（幂等）。"""
        db = self._require_db()
        cursor = await db.execute("DELETE FROM agent_instances WHERE agent_id=?", (agent_id,))
        await db.commit()
        return int(cursor.rowcount or 0) > 0
