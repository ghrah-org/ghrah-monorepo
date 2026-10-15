# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SqliteBackend：基于 SQLite 的持久化后端。

使用 aiosqlite 实现异步 SQLite 操作，WAL 模式支持并发读。
所有写入操作在事务中执行，保证原子性。

设计要点：
    - WAL 模式：支持并发读写，适合 Subject 单写多读场景
    - 事务保证：批量操作使用显式事务
    - 序列化兼容：复用 serialization.py 中的函数
    - 连接管理：支持 async with 上下文管理器
    - run_id：进程级运行标识
    - sessions/branches/nodes：独立 Root、Head 引用与不可变节点
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from ghrah.context.persistence.backend import PersistenceBackend
from ghrah.context.persistence.changes import ContextChanges
from ghrah.context.persistence.checkpoint import ContextCheckpoint
from ghrah.context.persistence.serialization import (
    deserialize_action_session,
    deserialize_branch,
    deserialize_node,
    serialize_action_session,
    serialize_branch,
    serialize_node,
)

logger = logging.getLogger(__name__)

__all__ = ["SqliteBackend"]

# 节点表查询的列名列表，保持 SELECT 和 _row_to_dict 一致
_NODE_COLUMNS = (
    "id, parent_id, agent_name, session_id, created_on_branch_id, timestamp, iteration, "
    "is_snapshot, ability_names, agent_state, "
    "messages_delta, messages_snapshot, action_results, metadata"
)

# 数据库建表 DDL
_SCHEMA_SQL = """
CREATE TABLE context_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version   INTEGER NOT NULL
);
INSERT INTO context_schema (singleton, version) VALUES (1, 3);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata      TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS agents (
    agent_name   TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    agent_name        TEXT NOT NULL,
    name              TEXT NOT NULL DEFAULT 'Session 1',
    root_node_id      TEXT NOT NULL,
    active_branch_id TEXT NOT NULL,
    lifecycle         TEXT NOT NULL DEFAULT 'open',
    origin_agent_name TEXT,
    origin_session_id TEXT,
    origin_node_id   TEXT,
    system_prompt     TEXT DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata          TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id         TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    head_node_id      TEXT NOT NULL,
    lifecycle         TEXT NOT NULL DEFAULT 'open',
    parent_branch_id  TEXT,
    fork_point_node_id TEXT,
    created_at        TEXT NOT NULL,
    metadata          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS nodes (
    id                TEXT PRIMARY KEY,
    parent_id         TEXT,
    agent_name        TEXT NOT NULL REFERENCES agents(agent_name),
    session_id        TEXT NOT NULL DEFAULT '',
    timestamp         TEXT NOT NULL,
    iteration         INTEGER NOT NULL DEFAULT 0,
    created_on_branch_id TEXT NOT NULL,
    is_snapshot       INTEGER NOT NULL DEFAULT 0,
    ability_names     TEXT NOT NULL DEFAULT '[]',
    agent_state       TEXT NOT NULL DEFAULT '{}',
    messages_delta    TEXT NOT NULL DEFAULT '[]',
    messages_snapshot TEXT,
    action_results    TEXT NOT NULL DEFAULT '[]',
    metadata          TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL DEFAULT CURRENT_timestamp
);

CREATE TABLE IF NOT EXISTS chain_meta (
    agent_name        TEXT PRIMARY KEY REFERENCES agents(agent_name),
    active_session_id TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nodes_agent ON nodes(agent_name);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_branch ON nodes(session_id, created_on_branch_id);
CREATE INDEX IF NOT EXISTS idx_nodes_iteration ON nodes(agent_name, iteration);
CREATE INDEX IF NOT EXISTS idx_nodes_session ON nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_agents_run ON agents(run_id);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_name);
CREATE INDEX IF NOT EXISTS idx_branches_session ON branches(session_id);
"""


def _generate_run_id() -> str:
    """生成基于当前时间的 run ID。

    格式：run_{ISO8601 时间戳}
    示例：run_2026-04-21T16-30-00
    """
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    return f"run_{ts}"


class SqliteBackend(PersistenceBackend):
    """基于 SQLite 的持久化后端，使用 aiosqlite 异步操作。

    适用于 Subject 层的本地持久化，使用 WAL 模式支持并发读。
    所有写入操作在事务中执行，保证原子性。

    数据按 run_id 隔离，每个 run 创建独立的 agents 记录。

    Args:
        db_path: SQLite 数据库文件路径，默认为 ~/.ghrah/data/ghrah.db
        run_id: 运行 ID，默认自动生成（格式：run_{ISO8601}）
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        run_id: str | None = None,
    ) -> None:
        if db_path is None:
            db_path = Path.home() / ".ghrah" / "data" / "ghrah.db"
        self._db_path = Path(db_path)
        self._run_id = run_id or _generate_run_id()
        self._db: aiosqlite.Connection | None = None
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    @property
    def db_path(self) -> Path:
        """数据库文件路径。"""
        return self._db_path

    @property
    def connected(self) -> bool:
        """连接是否已建立。"""
        return self._db is not None

    @property
    def run_id(self) -> str:
        """当前运行 ID。"""
        return self._run_id

    # ----------------------------------------------------------------
    # 连接管理
    # ----------------------------------------------------------------

    async def _ensure_db(self) -> aiosqlite.Connection:
        """确保数据库连接已建立并初始化。

        Returns:
            aiosqlite 连接对象

        Raises:
            RuntimeError: 数据库未通过 connect() 打开
        """
        if self._db is None:
            raise RuntimeError(
                "Database not connected. Use 'async with backend:' or call connect() first."
            )
        if not self._initialized:
            async with self._initialize_lock:
                if not self._initialized:
                    await self._initialize_schema()
                    self._initialized = True
        return self._db

    async def connect(self) -> None:
        """打开数据库连接并启用 WAL 模式。

        Raises:
            RuntimeError: 数据库连接已建立
        """
        if self._db is not None:
            raise RuntimeError("Database already connected.")

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None 启用手动事务控制，避免隐式事务冲突
        self._db = await aiosqlite.connect(str(self._db_path), isolation_level=None)
        # 启用 Row 工厂，支持列名访问
        self._db.row_factory = aiosqlite.Row
        # 启用 WAL 模式，支持并发读写
        await self._db.execute("PRAGMA journal_mode=WAL")
        # 多 agent 各持独立连接写同一 db：WAL 单写者下并发写等待而非立即 SQLITE_BUSY
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA foreign_keys=ON")
        logger.debug("SQLite database connected: %s", self._db_path)

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.debug("SQLite database closed: %s", self._db_path)

    async def _initialize_schema(self) -> None:
        """初始化数据库表结构和索引。"""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        existing_tables = {row["name"] for row in await cursor.fetchall()}
        if existing_tables:
            if "context_schema" not in existing_tables:
                raise RuntimeError(
                    "Legacy context database schema detected; delete it and rebuild explicitly"
                )
            version_cursor = await self._db.execute(
                "SELECT version FROM context_schema WHERE singleton = 1"
            )
            version_row = await version_cursor.fetchone()
            if version_row is None or version_row["version"] != 3:
                raise RuntimeError("Unsupported context database schema; rebuild explicitly")
        else:
            await self._db.executescript(_SCHEMA_SQL)
        # 确保当前 run 存在
        await self._db.execute(
            "INSERT OR IGNORE INTO runs (run_id) VALUES (?)",
            (self._run_id,),
        )
        await self._db.commit()
        logger.debug("Database schema initialized for run: %s", self._run_id)

    # ----------------------------------------------------------------
    # 上下文管理器
    # ----------------------------------------------------------------

    async def __aenter__(self) -> SqliteBackend:
        """异步上下文管理器入口：打开连接。"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口：关闭连接。"""
        await self.close()

    # ----------------------------------------------------------------
    # 辅助方法
    # ----------------------------------------------------------------

    async def _ensure_agent(self, db: aiosqlite.Connection, agent_name: str) -> None:
        """确保 agent 记录存在。

        Args:
            db: 数据库连接
            agent_name: Agent 名称
        """
        await db.execute(
            "INSERT OR IGNORE INTO agents (agent_name, run_id) VALUES (?, ?)",
            (agent_name, self._run_id),
        )

    @staticmethod
    def _serialize_node_params(serialized: dict[str, Any]) -> tuple[Any, ...]:
        """将序列化后的节点 dict 转换为 SQL 参数元组。

        Args:
            serialized: serialize_node() 的输出 dict

        Returns:
            INSERT OR REPLACE 语句的参数元组
        """
        return (
            serialized["id"],
            serialized["parent_id"],
            serialized["agent_name"],
            serialized["session_id"],
            serialized["created_on_branch_id"],
            serialized["timestamp"],
            serialized["iteration"],
            1 if serialized["is_snapshot"] else 0,
            json.dumps(serialized["ability_names"], ensure_ascii=False),
            json.dumps(serialized["agent_state"], ensure_ascii=False),
            json.dumps(serialized["messages_delta"], ensure_ascii=False),
            json.dumps(serialized["messages_snapshot"], ensure_ascii=False)
            if serialized["messages_snapshot"] is not None
            else None,
            json.dumps(serialized["action_results"], ensure_ascii=False),
            json.dumps(serialized["metadata"], ensure_ascii=False),
        )

    # ----------------------------------------------------------------
    # PersistenceBackend 接口实现
    # ----------------------------------------------------------------

    async def apply_changes(self, changes: ContextChanges) -> None:
        """在单个 SQLite 事务中应用最小增量变更。"""
        async with self._write_lock:
            await self._apply_changes(changes)

    async def _apply_changes(self, changes: ContextChanges) -> None:
        """持有后端写锁时应用增量变更。"""
        db = await self._ensure_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self.apply_changes_in_transaction(db, changes)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def apply_changes_in_transaction(
        self, db: aiosqlite.Connection, changes: ContextChanges
    ) -> None:
        """在同文件的既有事务中写上下文变更，由事务宿主负责提交或回滚。"""
        if not db.in_transaction:
            raise RuntimeError("An active transaction is required")
        databases = await (await db.execute("PRAGMA database_list")).fetchall()
        main = next(row for row in databases if row[1] == "main")
        if Path(main[2]).resolve() != self._db_path.resolve():
            raise ValueError("Transaction database does not match context backend")

        await self._ensure_agent(db, changes.agent_name)
        for branch_id in changes.delete_branch_ids:
            await db.execute("DELETE FROM branches WHERE branch_id = ?", (branch_id,))
        for session_id in changes.delete_session_ids:
            await db.execute("DELETE FROM nodes WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM branches WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        for session in changes.sessions:
            data = serialize_action_session(session)
            await db.execute(
                """
                INSERT INTO sessions (
                    session_id, agent_name, name, root_node_id, active_branch_id,
                    lifecycle, origin_agent_name, origin_session_id, origin_node_id,
                    system_prompt, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    name=excluded.name,
                    root_node_id=excluded.root_node_id,
                    active_branch_id=excluded.active_branch_id,
                    lifecycle=excluded.lifecycle,
                    origin_agent_name=excluded.origin_agent_name,
                    system_prompt=excluded.system_prompt,
                    metadata=excluded.metadata
                """,
                (
                    data["session_id"],
                    data["agent_name"],
                    data["name"],
                    data["root_node_id"],
                    data["active_branch_id"],
                    data["lifecycle"],
                    data["origin_agent_name"],
                    data["origin_session_id"],
                    data["origin_node_id"],
                    data["system_prompt"],
                    data["created_at"],
                    json.dumps(data["metadata"], ensure_ascii=False),
                ),
            )
        for branch in changes.branches:
            data = serialize_branch(branch)
            await db.execute(
                """
                INSERT INTO branches (
                    branch_id, session_id, name, head_node_id, lifecycle,
                    parent_branch_id, fork_point_node_id, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(branch_id) DO UPDATE SET
                    name=excluded.name,
                    head_node_id=excluded.head_node_id,
                    lifecycle=excluded.lifecycle,
                    metadata=excluded.metadata
                """,
                (
                    data["branch_id"],
                    data["session_id"],
                    data["name"],
                    data["head_node_id"],
                    data["lifecycle"],
                    data["parent_branch_id"],
                    data["fork_point_node_id"],
                    data["created_at"],
                    json.dumps(data["metadata"], ensure_ascii=False),
                ),
            )
        for node in sorted(changes.nodes, key=lambda item: item.iteration):
            await db.execute(
                f"INSERT INTO nodes ({_NODE_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                self._serialize_node_params(serialize_node(node)),
            )
        if changes.active_session_id is not None:
            await db.execute(
                """
                INSERT INTO chain_meta (agent_name, active_session_id) VALUES (?, ?)
                ON CONFLICT(agent_name) DO UPDATE SET
                    active_session_id=excluded.active_session_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (changes.agent_name, changes.active_session_id),
            )

    async def load_checkpoint(self, agent_name: str) -> ContextCheckpoint | None:
        """从 SQLite 加载 Agent 的 v3 checkpoint。"""
        db = await self._ensure_db()
        meta_cursor = await db.execute(
            "SELECT active_session_id FROM chain_meta WHERE agent_name = ?", (agent_name,)
        )
        meta = await meta_cursor.fetchone()
        if meta is None:
            return None
        session_cursor = await db.execute(
            """
            SELECT session_id, agent_name, name, root_node_id, active_branch_id,
                   lifecycle, origin_agent_name, origin_session_id, origin_node_id,
                   system_prompt, created_at, metadata
            FROM sessions WHERE agent_name = ? ORDER BY created_at, session_id
            """,
            (agent_name,),
        )
        sessions = tuple(
            deserialize_action_session(self._session_row_to_dict(row))
            for row in await session_cursor.fetchall()
        )
        session_ids = [session.session_id for session in sessions]
        if session_ids:
            placeholders = ", ".join("?" for _ in session_ids)
            branch_cursor = await db.execute(
                f"""
                SELECT branch_id, session_id, name, head_node_id, lifecycle,
                       parent_branch_id, fork_point_node_id, created_at, metadata
                FROM branches WHERE session_id IN ({placeholders})
                ORDER BY created_at, branch_id
                """,
                tuple(session_ids),
            )
            branches = tuple(
                deserialize_branch(self._branch_row_to_dict(row))
                for row in await branch_cursor.fetchall()
            )
        else:
            branches = ()
        node_cursor = await db.execute(
            f"SELECT {_NODE_COLUMNS} FROM nodes WHERE agent_name = ? "
            "ORDER BY session_id, iteration, timestamp",
            (agent_name,),
        )
        nodes = tuple(
            deserialize_node(self._row_to_dict(row)) for row in await node_cursor.fetchall()
        )
        return ContextCheckpoint(
            agent_name=agent_name,
            active_session_id=meta["active_session_id"],
            sessions=sessions,
            branches=branches,
            nodes=nodes,
        )

    async def delete_checkpoint(self, agent_name: str) -> None:
        """在单个事务中删除指定 Agent 的完整 v2 checkpoint。"""
        async with self._write_lock:
            await self._delete_checkpoint(agent_name)

    async def _delete_checkpoint(self, agent_name: str) -> None:
        """持有后端写锁时删除 checkpoint。"""
        db = await self._ensure_db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute("DELETE FROM chain_meta WHERE agent_name = ?", (agent_name,))
            await db.execute("DELETE FROM nodes WHERE agent_name = ?", (agent_name,))
            await db.execute("DELETE FROM sessions WHERE agent_name = ?", (agent_name,))
            await db.execute("DELETE FROM agents WHERE agent_name = ?", (agent_name,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def list_agents(self) -> list[str]:
        """列出所有有持久化数据的 agent 名称。

        Returns:
            agent 名称列表（按字母排序）
        """
        db = await self._ensure_db()
        cursor = await db.execute("SELECT agent_name FROM agents ORDER BY agent_name")
        rows = await cursor.fetchall()
        return [row["agent_name"] for row in rows]

    # ----------------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------------

    @staticmethod
    def _session_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """将 sessions 行转为 dict，仅在 SQL 参数边界执行一次 JSON 解码。"""
        return {
            "session_id": row["session_id"],
            "agent_name": row["agent_name"],
            "name": row["name"],
            "root_node_id": row["root_node_id"],
            "active_branch_id": row["active_branch_id"],
            "lifecycle": row["lifecycle"],
            "origin_agent_name": row["origin_agent_name"],
            "origin_session_id": row["origin_session_id"],
            "origin_node_id": row["origin_node_id"],
            "system_prompt": row["system_prompt"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    @staticmethod
    def _branch_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """将 branches 行转为 dict，仅在 SQL 参数边界执行一次 JSON 解码。"""
        return {
            "branch_id": row["branch_id"],
            "session_id": row["session_id"],
            "name": row["name"],
            "head_node_id": row["head_node_id"],
            "lifecycle": row["lifecycle"],
            "parent_branch_id": row["parent_branch_id"],
            "fork_point_node_id": row["fork_point_node_id"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        """将数据库行转换为 dict，反序列化 JSON 字段。

        使用列名访问（而非硬编码索引），提高可维护性。

        Args:
            row: aiosqlite 查询结果行（需启用 row_factory）

        Returns:
            反序列化后的 dict，兼容 serialize_node 输出格式
        """
        return {
            "id": row["id"],
            "parent_id": row["parent_id"],
            "agent_name": row["agent_name"],
            "session_id": row["session_id"],
            "created_on_branch_id": row["created_on_branch_id"],
            "timestamp": row["timestamp"],
            "iteration": row["iteration"],
            "is_snapshot": bool(row["is_snapshot"]),
            "ability_names": json.loads(row["ability_names"]),
            "agent_state": json.loads(row["agent_state"]),
            "messages_delta": json.loads(row["messages_delta"]),
            "messages_snapshot": json.loads(row["messages_snapshot"])
            if row["messages_snapshot"] is not None
            else None,
            "action_results": json.loads(row["action_results"]),
            "metadata": json.loads(row["metadata"]),
        }
