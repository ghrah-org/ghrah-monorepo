# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SQLite v3 多 Session checkpoint 与增量写入测试。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ghrah.chat.factory import ChatMessageFactory
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend


@pytest.mark.asyncio
async def test_sqlite_round_trip_multiple_sessions_and_branches(tmp_path: Path) -> None:
    """SQLite 往返保留多 Root、Branch Head 和激活指针。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        message_factory=ChatMessageFactory(),
    )
    first_session_id = cm.active_session_id
    cm.begin_iteration()
    cm.apply_state_changes({"path": "first"})
    cm.commit_iteration(ability_names=["first"])
    second = cm.create_session(initial_state={"path": "second"})
    cm.activate_session(second.session_id)
    retry = cm.create_branch(session_id=second.session_id, name="retry-1")
    cm.activate_branch(second.session_id, retry.branch_id)
    await cm.persist()
    await backend.close()

    restored_backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-b")
    await restored_backend.connect()
    restored = ContextManager(
        agent_name="agent",
        persistence=restored_backend,
        message_factory=ChatMessageFactory(),
    )
    await restored.restore("agent")

    assert {session.session_id for session in restored.list_sessions()} == {
        first_session_id,
        second.session_id,
    }
    assert restored.active_session_id == second.session_id
    assert restored.get_active_session().active_branch_id == retry.branch_id
    assert restored.get_current_state() == {"path": "second"}
    await restored_backend.close()


@pytest.mark.asyncio
async def test_sqlite_commits_do_not_delete_existing_nodes(tmp_path: Path) -> None:
    """连续 commit 只增量 INSERT，不得 DELETE 已有节点。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        auto_persist=True,
        message_factory=ChatMessageFactory(),
    )
    await cm.wait_for_persist()
    db = await backend._ensure_db()
    await db.executescript(
        """
        CREATE TABLE node_delete_audit (node_id TEXT NOT NULL);
        CREATE TRIGGER audit_node_delete AFTER DELETE ON nodes
        BEGIN
            INSERT INTO node_delete_audit (node_id) VALUES (OLD.id);
        END;
        """
    )

    for index in range(50):
        cm.begin_iteration()
        cm.commit_iteration(ability_names=[f"step-{index}"])
    await cm.wait_for_persist()

    count_cursor = await db.execute("SELECT COUNT(*) AS count FROM nodes")
    delete_cursor = await db.execute("SELECT COUNT(*) AS count FROM node_delete_audit")
    assert (await count_cursor.fetchone())["count"] == 51
    assert (await delete_cursor.fetchone())["count"] == 0
    await backend.close()


@pytest.mark.asyncio
async def test_legacy_sqlite_schema_fails_fast(tmp_path: Path) -> None:
    """无 schema marker 的旧库必须显式重建。"""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    backend = SqliteBackend(db_path=path, run_id="run-a")
    await backend.connect()
    with pytest.raises(RuntimeError, match="Legacy context database schema"):
        await backend.load_checkpoint("agent")
    await backend.close()


@pytest.mark.asyncio
async def test_v2_sqlite_schema_fails_fast(tmp_path: Path) -> None:
    """v2 marker 库（版本 < 3）快速失败，禁止迁移/静默兼容。"""
    path = tmp_path / "v2.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE context_schema (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO context_schema (singleton, version) VALUES (1, 2)")
    connection.commit()
    connection.close()

    backend = SqliteBackend(db_path=path, run_id="run-a")
    await backend.connect()
    with pytest.raises(RuntimeError, match="Unsupported context database schema"):
        await backend.load_checkpoint("agent")
    await backend.close()


@pytest.mark.asyncio
async def test_sqlite_v3_columns_round_trip(tmp_path: Path) -> None:
    """v3 新列（session name/lifecycle/origin_*、branch lifecycle）往返保真。"""
    backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-a")
    await backend.connect()
    cm = ContextManager(
        agent_name="agent",
        persistence=backend,
        message_factory=ChatMessageFactory(),
    )
    first = cm.create_session(name="显式名-一")
    second = cm.create_session(
        origin_session_id=first.session_id,
        origin_node_id=first.root_node_id,
    )
    cm.begin_iteration()
    cm.commit_iteration(ability_names=["step"])
    cm.archive_session(first.session_id)
    retry = cm.create_branch(session_id=second.session_id, name="retry")
    cm.archive_branch(second.session_id, retry.branch_id)
    await cm.persist()
    await backend.close()

    restored_backend = SqliteBackend(db_path=tmp_path / "context.db", run_id="run-b")
    await restored_backend.connect()
    restored = ContextManager(
        agent_name="agent",
        persistence=restored_backend,
        message_factory=ChatMessageFactory(),
    )
    await restored.restore("agent")

    sessions = {
        session.session_id: session for session in restored.list_sessions(include_deleted=True)
    }
    assert sessions[first.session_id].name == "显式名-一"
    assert sessions[first.session_id].lifecycle == "archived"
    assert sessions[second.session_id].origin_agent_name == "agent"
    assert sessions[second.session_id].origin_session_id == first.session_id
    assert sessions[second.session_id].origin_node_id == first.root_node_id
    branches = restored.list_branches(second.session_id)
    by_name = {branch.name: branch for branch in branches}
    assert by_name["retry"].lifecycle == "archived"
    assert by_name["main"].lifecycle == "open"
    await restored_backend.close()


async def test_v3_sqlite_migrates_and_keeps_legacy_nodes(tmp_path: Path) -> None:
    """旧节点在只读打开及就地升级后都保留，轮次归属为空。"""
    path = tmp_path / "context.db"
    async with SqliteBackend(path) as backend:
        cm = ContextManager(
            agent_name="agent", persistence=backend, message_factory=ChatMessageFactory()
        )
        await cm.persist()
        head_id = cm.active_head.id

    with sqlite3.connect(path) as db:
        db.executescript(
            "DROP TABLE IF EXISTS hitl_requests; DROP TABLE IF EXISTS turns; "
            "DROP INDEX IF EXISTS idx_nodes_turn; "
        )
        if "turn_id" in {row[1] for row in db.execute("PRAGMA table_info(nodes)")}:
            db.execute("ALTER TABLE nodes DROP COLUMN turn_id")
        db.execute("UPDATE context_schema SET version=3")

    async with SqliteBackend(path, read_only=True) as reader:
        assert await reader.load_node("agent", head_id) is not None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM context_schema").fetchone() == (3,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='turns'").fetchone() is None

    async with SqliteBackend(path) as backend:
        await backend.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM context_schema").fetchone() == (4,)
        assert db.execute("SELECT turn_id FROM nodes WHERE id=?", (head_id,)).fetchone() == (None,)
        assert db.execute("SELECT count(*) FROM turns").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM hitl_requests").fetchone() == (0,)


async def test_v4_sqlite_rebuilds_turn_and_hitl_indexes_on_writable_reopen(tmp_path: Path) -> None:
    """只读打开存量 v4 库不补建索引，写入侧初始化补建且保留轮次和审批。"""
    path = tmp_path / "context.db"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        session_id = cm.active_session_id
        turn_id = await cm.open_turn()
        pending = await cm.open_hitl_request(
            promise_id="approval",
            ability_name="tool",
            tool_call_id="call",
            tool_call={},
            reason="needs approval",
            options={},
            deadline_at=None,
        )
        assert pending.persisted

    indexes = {"idx_turns_session_opened", "idx_hitl_waiting_only"}
    with closing(sqlite3.connect(path)) as db:
        for index in sorted(indexes):
            db.execute(f"DROP INDEX IF EXISTS {index}")

    for read_only in (True, False, False):
        async with SqliteBackend(path, read_only=read_only) as backend:
            if not read_only:
                await backend.initialize()
            (saved_turn,) = await backend.list_turns("agent", session_id, None, 10)
            (saved_request,) = await backend.list_pending_hitl("agent", session_id)
            assert (saved_turn["turn_id"], saved_turn["status"]) == (turn_id, "waiting_hitl")
            assert (saved_request["turn_id"], saved_request["promise_id"]) == (turn_id, "approval")
        with closing(sqlite3.connect(path)) as db:
            schema_indexes = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            assert schema_indexes & indexes == (set() if read_only else indexes)
            assert db.execute("SELECT version FROM context_schema").fetchone() == (4,)


async def test_v3_schema_migration_rolls_back_ddl_on_error(tmp_path: Path) -> None:
    """索引创建受阻时节点列、轮次表和版本号都回到迁移前。"""
    path = tmp_path / "v3.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE context_schema (singleton INTEGER PRIMARY KEY, version INTEGER); "
            "INSERT INTO context_schema VALUES (1, 3); "
            "CREATE TABLE nodes (id TEXT PRIMARY KEY); "
            "CREATE TABLE idx_turns_opened (id TEXT PRIMARY KEY);"
        )
    async with SqliteBackend(path) as backend:
        with pytest.raises(sqlite3.OperationalError, match="idx_turns_opened"):
            await backend.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM context_schema").fetchone() == (3,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='turns'").fetchone() is None
        assert {column[1] for column in db.execute("PRAGMA table_info(nodes)")} == {"id"}
