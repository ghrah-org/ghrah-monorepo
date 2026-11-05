# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ghrah.protocol.types import TaskPriority, TaskStatus

from ghrah.subject.task.models import TaskRecord, make_task_record
from ghrah.subject.task.store import ConcurrentModificationError, TaskStore

# ─── fixtures / helpers ───


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TaskStore]:
    s = TaskStore(tmp_path / "tasks.db")
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


def _make(
    *,
    task_id: str = "0" * 32,
    title: str = "T",
    project_id: str = "proj-1",
    status: TaskStatus = TaskStatus.PENDING,
    priority: TaskPriority = TaskPriority.NORMAL,
    agent_name: str | None = None,
    parent_id: str | None = None,
    dependencies: list[str] | None = None,
    metadata: dict[str, object] | None = None,
    result: object = None,
    version: int = 1,
) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        task_id=task_id,
        project_id=project_id,
        title=title,
        status=status,
        priority=priority,
        agent_name=agent_name,
        parent_id=parent_id,
        dependencies=list(dependencies) if dependencies else [],
        metadata=dict(metadata) if metadata else {},
        result=result,
        created_at=now,
        updated_at=now,
        version=version,
    )


# ─── A. 基本 CRUD + JSON 往返 ───


class TestCrudAndRoundtrip:
    async def test_upsert_and_get(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        got = await store.get(record.task_id)
        assert got is not None
        assert got.task_id == record.task_id
        assert got.title == "T"

    async def test_get_missing_returns_none(self, store: TaskStore) -> None:
        assert await store.get("nonexistent") is None

    async def test_exists_true_false(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.exists(record.task_id) is True
        assert await store.exists("nonexistent") is False

    async def test_json_fields_roundtrip(self, store: TaskStore) -> None:
        record = _make(
            dependencies=["x", "y"],
            metadata={"k": "v", "nested": [1, 2, {"a": True}]},
            result={"ok": True, "count": 3},
        )
        await store.upsert(record)
        got = await store.get(record.task_id)
        assert got is not None
        assert got.dependencies == ["x", "y"]
        assert got.metadata == {"k": "v", "nested": [1, 2, {"a": True}]}
        assert got.result == {"ok": True, "count": 3}

    async def test_datetime_roundtrip(self, store: TaskStore) -> None:
        now = datetime(2025, 7, 15, 10, 0, 0, tzinfo=UTC)
        record = _make()
        record.created_at = now
        record.updated_at = now
        await store.upsert(record)
        got = await store.get(record.task_id)
        assert got is not None
        assert isinstance(got.created_at, datetime)
        assert got.created_at == now

    async def test_status_priority_persisted_as_value(self, store: TaskStore) -> None:
        record = _make(status=TaskStatus.IN_PROGRESS, priority=TaskPriority.URGENT)
        await store.upsert(record)
        got = await store.get(record.task_id)
        assert got is not None
        assert got.status == TaskStatus.IN_PROGRESS
        assert got.priority == TaskPriority.URGENT

    async def test_version_persisted(self, store: TaskStore) -> None:
        record = _make(version=1)
        await store.upsert(record)
        got = await store.get(record.task_id)
        assert got is not None
        assert got.version == 1


# ─── B. 乐观锁（验证门）───


class TestOptimisticLock:
    async def test_update_success_bumps_version(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)

        def _bump(r: TaskRecord) -> TaskRecord:
            r.title = "T2"
            return r

        updated = await store.update(record.task_id, expected_version=1, mutator=_bump)
        assert updated is not None
        assert updated.version == 2
        assert updated.title == "T2"
        # 持久化校验
        got = await store.get(record.task_id)
        assert got is not None
        assert got.version == 2

    async def test_update_stale_version_raises(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)

        def _noop(r: TaskRecord) -> TaskRecord:
            return r

        await store.update(record.task_id, expected_version=1, mutator=_noop)
        with pytest.raises(ConcurrentModificationError):
            await store.update(record.task_id, expected_version=1, mutator=_noop)

    async def test_update_expected_version_none_skips_check(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)

        def _bump(r: TaskRecord) -> TaskRecord:
            r.title = "T2"
            return r

        updated = await store.update(record.task_id, expected_version=None, mutator=_bump)
        assert updated is not None
        assert updated.version == 2

    async def test_update_missing_returns_none(self, store: TaskStore) -> None:
        def _noop(r: TaskRecord) -> TaskRecord:
            return r

        assert await store.update("nonexistent", expected_version=None, mutator=_noop) is None

    async def test_update_mutator_applied(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)

        def _change(r: TaskRecord) -> TaskRecord:
            r.title = "Changed"
            r.status = TaskStatus.IN_PROGRESS
            return r

        updated = await store.update(record.task_id, expected_version=1, mutator=_change)
        assert updated is not None
        assert updated.title == "Changed"
        assert updated.status == TaskStatus.IN_PROGRESS

    async def test_update_reads_raw_row_including_deleted(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True

        def _bump(r: TaskRecord) -> TaskRecord:
            r.title = "After delete"
            return r

        # update 读 raw 行，已软删仍可更新
        updated = await store.update(record.task_id, expected_version=None, mutator=_bump)
        assert updated is not None
        assert updated.title == "After delete"
        assert updated.deleted_at is not None


# ─── C. 软删不可见（验证门）───


class TestSoftDelete:
    async def test_soft_delete_returns_true_then_false(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        assert await store.soft_delete(record.task_id) is False

    async def test_get_excludes_deleted_default(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        assert await store.get(record.task_id) is None

    async def test_get_include_deleted_returns_record(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        got = await store.get(record.task_id, include_deleted=True)
        assert got is not None
        assert got.deleted_at is not None

    async def test_list_excludes_deleted_default(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        result = await store.list()
        assert result == []

    async def test_list_include_deleted(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        result = await store.list(include_deleted=True, include_terminal=True)
        assert len(result) == 1
        assert result[0].deleted_at is not None

    async def test_exists_excludes_deleted_default(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        assert await store.exists(record.task_id) is False
        assert await store.exists(record.task_id, include_deleted=True) is True

    async def test_soft_delete_bumps_version(self, store: TaskStore) -> None:
        record = make_task_record(title="T", project_id="proj-1")
        await store.upsert(record)
        assert await store.soft_delete(record.task_id) is True
        got = await store.get(record.task_id, include_deleted=True)
        assert got is not None
        assert got.version == 2


# ─── D. list 过滤组合 ───


class TestListFilters:
    async def _seed_many(self, store: TaskStore) -> dict[str, str]:
        ids: dict[str, str] = {}
        specs = {
            "a": {"agent_name": "agent1", "status": TaskStatus.PENDING, "parent": None},
            "b": {
                "agent_name": "agent1",
                "status": TaskStatus.IN_PROGRESS,
                "parent": "p1",
            },
            "c": {
                "agent_name": "agent2",
                "status": TaskStatus.COMPLETED,
                "parent": "p1",
            },
        }
        for name, spec in specs.items():
            r = _make(
                task_id=name * 32,
                title=name,
                agent_name=spec["agent_name"],
                status=spec["status"],
                parent_id=spec["parent"],
            )
            await store.upsert(r)
            ids[name] = r.task_id
        return ids

    async def test_list_by_agent_name(self, store: TaskStore) -> None:
        await self._seed_many(store)
        result = await store.list(agent_name="agent1")
        assert {r.task_id[0] for r in result} == {"a", "b"}

    async def test_list_by_status(self, store: TaskStore) -> None:
        await self._seed_many(store)
        result = await store.list(status="pending", include_terminal=True)
        assert {r.task_id[0] for r in result} == {"a"}

    async def test_list_by_parent_id(self, store: TaskStore) -> None:
        await self._seed_many(store)
        result = await store.list(parent_id="p1", include_terminal=True)
        assert {r.task_id[0] for r in result} == {"b", "c"}

    async def test_list_exclude_terminal_default(self, store: TaskStore) -> None:
        await self._seed_many(store)
        result = await store.list()
        # 默认排除 completed
        assert {r.task_id[0] for r in result} == {"a", "b"}

    async def test_list_include_terminal(self, store: TaskStore) -> None:
        await self._seed_many(store)
        result = await store.list(include_terminal=True)
        assert {r.task_id[0] for r in result} == {"a", "b", "c"}

    async def test_list_limit(self, store: TaskStore) -> None:
        for i in range(5):
            await store.upsert(_make(task_id=hex(i)[2:] * 32, title=f"T{i}"))
        result = await store.list(limit=3, include_terminal=True)
        assert len(result) == 3

    async def test_list_empty(self, store: TaskStore) -> None:
        assert await store.list() == []

    async def test_list_by_project_id(self, store: TaskStore) -> None:
        await store.upsert(_make(task_id="a" * 32, title="A", project_id="proj-1"))
        await store.upsert(_make(task_id="b" * 32, title="B", project_id="proj-2"))
        await store.upsert(_make(task_id="c" * 32, title="C", project_id="proj-1"))
        result = await store.list(project_id="proj-1", include_terminal=True)
        assert {r.task_id[0] for r in result} == {"a", "c"}
        result2 = await store.list(project_id="proj-2", include_terminal=True)
        assert {r.task_id[0] for r in result2} == {"b"}

    async def test_list_project_id_excludes_sentinel(self, store: TaskStore) -> None:
        # sentinel 行（project_id=''）不应被任何真实 project_id 过滤命中
        await store.upsert(_make(task_id="s" * 32, title="S", project_id=""))
        await store.upsert(_make(task_id="a" * 32, title="A", project_id="proj-1"))
        result = await store.list(project_id="proj-1", include_terminal=True)
        assert {r.task_id[0] for r in result} == {"a"}
        # 无过滤时 sentinel 行可见
        all_result = await store.list(include_terminal=True)
        assert {r.task_id[0] for r in all_result} == {"s", "a"}


# ─── E. count_dependents 子串防护（验证门）───


class TestCountDependents:
    async def test_count_dependents_exact(self, store: TaskStore) -> None:
        # A 依赖 B
        a = _make(task_id="a" * 32, title="A", dependencies=["b" * 32])
        b = _make(task_id="b" * 32, title="B")
        await store.upsert(a)
        await store.upsert(b)
        assert await store.count_dependents("b" * 32) == 1
        assert await store.count_dependents("a" * 32) == 0

    async def test_count_dependents_substring_no_false_positive(self, store: TaskStore) -> None:
        """task_id 互为子串时不得误判（验证门核心）。"""
        short = "abc"
        long_id = "abc123"
        depender = _make(task_id="d" * 32, title="D", dependencies=[long_id])
        await store.upsert(depender)
        assert await store.count_dependents(short) == 0
        assert await store.count_dependents(long_id) == 1

    async def test_count_dependents_multiple(self, store: TaskStore) -> None:
        target = "t" * 32
        for i in range(3):
            await store.upsert(
                _make(
                    task_id=hex(i)[2:] * 32,
                    title=f"T{i}",
                    dependencies=[target],
                )
            )
        assert await store.count_dependents(target) == 3

    async def test_count_dependents_excludes_deleted(self, store: TaskStore) -> None:
        target = "t" * 32
        depender = _make(task_id="d" * 32, title="D", dependencies=[target])
        await store.upsert(depender)
        assert await store.count_dependents(target) == 1
        assert await store.soft_delete(depender.task_id) is True
        assert await store.count_dependents(target) == 0

    async def test_count_dependents_id_as_substring_of_another_dep(self, store: TaskStore) -> None:
        """target 是另一依赖项的子串，但仍不应误判。"""
        target = "ab"
        other = "abxy"
        depender = _make(task_id="d" * 32, title="D", dependencies=[other])
        await store.upsert(depender)
        assert await store.count_dependents(target) == 0
        assert await store.count_dependents(other) == 1


class TestCountChildren:
    async def test_count_children(self, store: TaskStore) -> None:
        for i in range(2):
            await store.upsert(_make(task_id=hex(i)[2:] * 32, title=f"C{i}", parent_id="p1"))
        # 一个不属于 p1
        await store.upsert(_make(task_id="f" * 32, title="F", parent_id="p2"))
        assert await store.count_children("p1") == 2
        assert await store.count_children("p2") == 1
        assert await store.count_children("nope") == 0

    async def test_count_children_excludes_deleted(self, store: TaskStore) -> None:
        child = _make(task_id="c" * 32, title="C", parent_id="p1")
        await store.upsert(child)
        assert await store.count_children("p1") == 1
        assert await store.soft_delete(child.task_id) is True
        assert await store.count_children("p1") == 0


# ─── F. list_all_active（供 graph 构造）───


class TestListAllActive:
    async def test_list_all_active_excludes_deleted(self, store: TaskStore) -> None:
        a = _make(task_id="a" * 32, title="A")
        b = _make(task_id="b" * 32, title="B")
        await store.upsert(a)
        await store.upsert(b)
        assert await store.soft_delete(a.task_id) is True
        result = await store.list_all_active()
        assert {r.task_id[0] for r in result} == {"b"}

    async def test_list_all_active_returns_all_active(self, store: TaskStore) -> None:
        for name in ("a", "b", "c"):
            await store.upsert(
                _make(
                    task_id=name * 32,
                    title=name,
                    status=TaskStatus.COMPLETED,
                )
            )
        result = await store.list_all_active()
        # list_all_active 不过滤终态
        assert {r.task_id[0] for r in result} == {"a", "b", "c"}


class TestReopenPersistence:
    async def test_data_survives_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "tasks.db"
        s = TaskStore(db_path)
        await s.start()
        await s.upsert(_make(task_id="d" * 32, title="T"))
        await s.stop()
        # 同路径新连接：幂等 DDL 不重复建表，数据存活
        s2 = TaskStore(db_path)
        await s2.start()
        try:
            got = await s2.get("d" * 32)
            assert got is not None
            assert got.title == "T"
            assert got.status == TaskStatus.PENDING
        finally:
            await s2.stop()


# ─── G. project_id 列迁移（旧库无该列）───


class TestProjectIdMigration:
    async def test_migrates_old_db_without_project_id_column(self, tmp_path: Path) -> None:
        """旧库（无 project_id 列）经 start() 迁移后可正常读写，旧行落 sentinel。"""
        import aiosqlite

        db_path = tmp_path / "tasks.db"
        # 模拟旧库：手动建无 project_id 列的表并插入一行
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "CREATE TABLE subject_tasks ("
                "task_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                "description TEXT NOT NULL DEFAULT '', agent_name TEXT, "
                "status TEXT NOT NULL, priority TEXT NOT NULL, parent_id TEXT, "
                "dependencies TEXT NOT NULL DEFAULT '[]', result TEXT, error TEXT, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, "
                "completed_at TEXT, metadata TEXT NOT NULL DEFAULT '{}', "
                "version INTEGER NOT NULL DEFAULT 1, deleted_at TEXT)"
            )
            await db.execute(
                "INSERT INTO subject_tasks (task_id, title, status, priority, "
                "dependencies, metadata, created_at, updated_at, version) "
                "VALUES (?, 'Old', 'pending', 'normal', '[]', '{}', "
                "'2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00', 1)",
                ("d" * 32,),
            )
            await db.commit()

        # start() 触发迁移
        s = TaskStore(db_path)
        await s.start()
        try:
            got = await s.get("d" * 32, include_deleted=True)
            assert got is not None
            assert got.title == "Old"
            # 旧行 project_id 落 sentinel ''
            assert got.project_id == ""
            # 新写入正常
            new_rec = _make(task_id="a" * 32, title="New", project_id="proj-1")
            await s.upsert(new_rec)
            got_new = await s.get("a" * 32)
            assert got_new is not None
            assert got_new.project_id == "proj-1"
        finally:
            await s.stop()

    async def test_migration_idempotent(self, tmp_path: Path) -> None:
        """对已含 project_id 列的库再 start 不报错。"""
        db_path = tmp_path / "tasks.db"
        s = TaskStore(db_path)
        await s.start()
        await s.upsert(_make(task_id="a" * 32, title="A", project_id="proj-1"))
        await s.stop()
        # 二次 start：列已存在，ALTER 不重复执行
        s2 = TaskStore(db_path)
        await s2.start()
        try:
            got = await s2.get("a" * 32)
            assert got is not None
            assert got.project_id == "proj-1"
        finally:
            await s2.stop()


# ─── reassign_project_id（reconcile bootstrap 用） ───


class TestReassignProjectId:
    async def test_reassign_migrates_sentinel_tasks(self, store: TaskStore) -> None:
        await store.upsert(_make(task_id="a" * 32, title="A", project_id=""))
        await store.upsert(_make(task_id="b" * 32, title="B", project_id=""))
        await store.upsert(_make(task_id="c" * 32, title="C", project_id="proj-1"))

        migrated = await store.reassign_project_id("", "default")
        assert migrated == 2

        # sentinel 已迁移到 default
        got_a = await store.get("a" * 32)
        got_b = await store.get("b" * 32)
        got_c = await store.get("c" * 32)
        assert got_a is not None and got_a.project_id == "default"
        assert got_b is not None and got_b.project_id == "default"
        assert got_c is not None and got_c.project_id == "proj-1"  # 未迁移
        # version 递增
        assert got_a.version == 2

    async def test_reassign_respects_include_terminal(self, store: TaskStore) -> None:
        await store.upsert(
            _make(task_id="a" * 32, title="A", project_id="", status=TaskStatus.COMPLETED)
        )
        await store.upsert(
            _make(task_id="b" * 32, title="B", project_id="", status=TaskStatus.PENDING)
        )

        migrated = await store.reassign_project_id("", "default", include_terminal=False)
        assert migrated == 1
        got_a = await store.get("a" * 32)
        assert got_a is not None and got_a.project_id == ""  # 终态未迁移

    async def test_reassign_respects_include_deleted(self, store: TaskStore) -> None:
        await store.upsert(_make(task_id="a" * 32, title="A", project_id=""))
        await store.soft_delete("a" * 32)

        migrated = await store.reassign_project_id("", "default", include_deleted=False)
        assert migrated == 0
        got_a = await store.get("a" * 32, include_deleted=True)
        assert got_a is not None and got_a.project_id == ""  # 软删未迁移

    async def test_reassign_no_matches_returns_zero(self, store: TaskStore) -> None:
        await store.upsert(_make(task_id="c" * 32, title="C", project_id="proj-1"))
        migrated = await store.reassign_project_id("", "default")
        assert migrated == 0
