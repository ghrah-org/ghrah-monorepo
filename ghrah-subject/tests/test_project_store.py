# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ghrah.plugin.assembly import PluginAssembly, PluginInstanceConfig
from ghrah.protocol.types import ProjectStatus, RecoveryAction

from ghrah.subject.project.models import (
    AgentSpec,
    ProjectRecord,
    ProjectRuntimeConfig,
    RecoverySpec,
    WorkspaceMount,
    make_project_record,
)
from ghrah.subject.project.store import (
    ConcurrentModificationError,
    ProjectNotFoundError,
    ProjectStore,
)

# ─── fixtures / helpers ───


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ProjectStore]:
    s = ProjectStore(tmp_path / "projects.db")
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


def _make(
    *,
    project_id: str = "a" * 32,
    name: str = "P1",
    status: ProjectStatus = ProjectStatus.ACTIVE,
    workspaces: list[WorkspaceMount] | None = None,
    agents: list[AgentSpec] | None = None,
    version: int = 1,
    archived: bool = False,
    deleted: bool = False,
) -> ProjectRecord:
    now = datetime.now(UTC)
    return ProjectRecord(
        project_id=project_id,
        name=name,
        status=status,
        workspaces=list(workspaces) if workspaces else [],
        agents=list(agents) if agents else [],
        created_at=now,
        updated_at=now,
        version=version,
        archived_at=now if archived else None,
        deleted_at=now if deleted else None,
    )


# ─── A. 基本 CRUD + JSON 往返 ───


class TestCrudAndRoundtrip:
    async def test_start_idempotent(self, tmp_path: Path) -> None:
        s = ProjectStore(tmp_path / "p.db")
        await s.start()
        await s.start()  # 不报错
        await s.stop()

    async def test_upsert_and_get(self, store: ProjectStore) -> None:
        record = make_project_record(
            name="P1",
            description="Description",
            project_root_locator="file:///private/p1",
        )
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.name == "P1"
        assert got.description == "Description"
        assert got.project_root_locator == "file:///private/p1"
        assert got.project_id == record.project_id

    async def test_migrates_legacy_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as db:
            db.execute(
                "CREATE TABLE subject_projects ("
                "project_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "manifest_ref TEXT NOT NULL DEFAULT '', "
                "cluster_ids TEXT NOT NULL DEFAULT '[]', "
                "workspaces TEXT NOT NULL DEFAULT '[]', "
                "agents TEXT NOT NULL DEFAULT '[]', task_ids TEXT NOT NULL DEFAULT '[]', "
                "isolation TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, "
                "recovery TEXT NOT NULL DEFAULT 'resume', created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, deleted_at TEXT)"
            )
            db.execute(
                "INSERT INTO subject_projects ("
                "project_id, name, status, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy",
                    "Legacy",
                    "active",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        migrated = ProjectStore(db_path)
        await migrated.start()
        try:
            with sqlite3.connect(db_path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(subject_projects)")}
                version = db.execute(
                    "SELECT version FROM subject_schema_versions WHERE component = 'project_store'"
                ).fetchone()
            assert {
                "description",
                "project_root_locator",
                "archived_at",
                "config",
                "shell_cwd",
            } <= columns
            assert version == (5,)
            legacy = await migrated.get("legacy")
            assert legacy is not None
            assert legacy.shell_cwd == ""
        finally:
            await migrated.stop()

    async def test_get_missing_returns_none(self, store: ProjectStore) -> None:
        assert await store.get("nonexistent") is None

    async def test_config_plugins_roundtrip(self, store: ProjectStore) -> None:
        """``ProjectRecord.config.plugins`` 随 JSON 列持久化往返。"""
        record = make_project_record(name="P1")
        record.config = ProjectRuntimeConfig(
            plugins=PluginAssembly(
                enabled=["plugin-a"],
                instances={"plugin-a": {"i1": PluginInstanceConfig(enabled=False)}},
            )
        )
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.config is not None
        assert got.config.plugins.enabled == ["plugin-a"]
        assert got.config.plugins.instances["plugin-a"]["i1"].enabled is False
        # 内部装配语义不进 wire
        assert "config" not in got.to_wire()

    async def test_config_absent_reads_as_none(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.config is None

    async def test_json_columns_roundtrip(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        record.cluster_ids = ["c1", "c2"]
        record.workspaces = [
            WorkspaceMount(workspace_id="ws-1", default_for_agents=True),
            WorkspaceMount(workspace_id="ws-2", role="scratch"),
        ]
        record.agents = [AgentSpec(name="a1", cluster_id="c1", path_grants=[])]
        record.task_ids = ["t1", "t2"]
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.cluster_ids == ["c1", "c2"]
        assert len(got.workspaces) == 2
        assert got.workspaces[0].default_for_agents is True
        assert got.workspaces[1].role == "scratch"
        assert got.agents[0].name == "a1"
        assert got.task_ids == ["t1", "t2"]

    async def test_recovery_roundtrip(self, store: ProjectStore) -> None:
        record = make_project_record(
            name="P1", recovery=RecoverySpec(on_restart=RecoveryAction.PAUSE)
        )
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.recovery.on_restart is RecoveryAction.PAUSE

    async def test_status_roundtrip(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        record.status = ProjectStatus.PAUSED
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.status is ProjectStatus.PAUSED

    async def test_dt_roundtrip(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        ts = datetime(2026, 7, 31, 10, 0, 0, tzinfo=UTC)
        record.created_at = ts
        record.updated_at = ts
        await store.upsert(record)
        got = await store.get(record.project_id)
        assert got is not None
        assert got.created_at == ts


# ─── B. 乐观锁 update ───


class TestOptimisticLock:
    async def test_update_success(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        updated = await store.update(
            record.project_id, 1, lambda r: r.model_copy(update={"name": "P2"})
        )
        assert updated.name == "P2"
        assert updated.version == 2
        assert updated.updated_at >= record.updated_at

    async def test_update_version_mismatch(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        with pytest.raises(ConcurrentModificationError):
            await store.update(record.project_id, 999, lambda r: r)

    async def test_update_missing_raises_not_found(self, store: ProjectStore) -> None:
        with pytest.raises(ProjectNotFoundError):
            await store.update("nonexistent", 1, lambda r: r)

    async def test_update_chained_versions(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        await store.update(record.project_id, 1, lambda r: r.model_copy(update={"name": "P2"}))
        v3 = await store.update(record.project_id, 2, lambda r: r.model_copy(update={"name": "P3"}))
        assert v3.version == 3
        got = await store.get(record.project_id)
        assert got is not None
        assert got.version == 3
        assert got.name == "P3"


# ─── C. 软删 ───


class TestSoftDelete:
    async def test_soft_delete(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        await store.soft_delete(record.project_id, 1)
        assert await store.get(record.project_id) is None
        got = await store.get(record.project_id, include_deleted=True)
        assert got is not None
        assert got.deleted_at is not None
        assert got.version == 2

    async def test_soft_delete_version_mismatch(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        with pytest.raises(ConcurrentModificationError):
            await store.soft_delete(record.project_id, 999)


class TestArchiveRestoreHardDelete:
    async def test_archive_restore_and_filters(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)

        archived = await store.archive(record.project_id, record.version)
        assert archived.archived_at is not None
        assert archived.status is ProjectStatus.STOPPED
        assert await store.get(record.project_id) is None
        assert await store.get(record.project_id, include_archived=True) == archived
        assert await store.list() == []
        assert await store.list(archived=True) == [archived]
        assert await store.list(archived=None) == [archived]

        restored = await store.restore(record.project_id, archived.version)
        assert restored.archived_at is None
        assert restored.status is ProjectStatus.STOPPED
        assert await store.list() == [restored]

    async def test_hard_delete_uses_version_lock(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        with pytest.raises(ConcurrentModificationError):
            await store.hard_delete(record.project_id, 99)
        assert await store.get(record.project_id) is not None
        assert await store.hard_delete(record.project_id, record.version) is True
        assert await store.get(record.project_id, include_archived=True) is None

    async def test_start_migrates_legacy_deleted_row_to_archived(self, tmp_path: Path) -> None:
        db_path = tmp_path / "legacy-deleted.db"
        record = _make(deleted=True)
        legacy = ProjectStore(db_path)
        await legacy.start()
        await legacy.upsert(record)
        await legacy.stop()

        # Simulate an older schema version so startup executes the data migration.
        with sqlite3.connect(db_path) as db:
            db.execute(
                "UPDATE subject_schema_versions SET version = 2 WHERE component = 'project_store'"
            )

        migrated = ProjectStore(db_path)
        await migrated.start()
        try:
            archived = await migrated.get(record.project_id, include_archived=True)
            assert archived is not None
            assert archived.archived_at == record.deleted_at
            assert archived.deleted_at is None
            assert archived.status is ProjectStatus.STOPPED
            assert await migrated.list(archived=True) == [archived]
        finally:
            await migrated.stop()


# ─── D. list / exists ───


class TestListExists:
    async def test_list_empty(self, store: ProjectStore) -> None:
        assert await store.list() == []

    async def test_list_status_filter(self, store: ProjectStore) -> None:
        r1 = make_project_record(name="P1")
        r2 = make_project_record(name="P2")
        r2.status = ProjectStatus.PAUSED
        await store.upsert(r1)
        await store.upsert(r2)
        active = await store.list(status=ProjectStatus.ACTIVE)
        assert [p.name for p in active] == ["P1"]
        paused = await store.list(status=ProjectStatus.PAUSED)
        assert [p.name for p in paused] == ["P2"]

    async def test_list_excludes_deleted(self, store: ProjectStore) -> None:
        r1 = make_project_record(name="P1")
        r2 = make_project_record(name="P2")
        await store.upsert(r1)
        await store.upsert(r2)
        await store.soft_delete(r1.project_id, 1)
        result = await store.list()
        assert [p.name for p in result] == ["P2"]
        result_all = await store.list(include_deleted=True)
        assert {p.name for p in result_all} == {"P1", "P2"}

    async def test_exists(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        assert await store.exists(record.project_id) is True
        assert await store.exists("nonexistent") is False

    async def test_exists_excludes_deleted(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        await store.upsert(record)
        await store.soft_delete(record.project_id, 1)
        assert await store.exists(record.project_id) is False


# ─── E. exists_workspace_mounted ───


class TestExistsWorkspaceMounted:
    async def test_mounted(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        record.workspaces = [WorkspaceMount(workspace_id="ws-1", default_for_agents=True)]
        await store.upsert(record)
        assert await store.exists_workspace_mounted("ws-1") is True

    async def test_not_mounted(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        record.workspaces = [WorkspaceMount(workspace_id="ws-1")]
        await store.upsert(record)
        assert await store.exists_workspace_mounted("ws-99") is False

    async def test_excludes_deleted_project(self, store: ProjectStore) -> None:
        record = make_project_record(name="P1")
        record.workspaces = [WorkspaceMount(workspace_id="ws-1")]
        await store.upsert(record)
        await store.soft_delete(record.project_id, 1)
        assert await store.exists_workspace_mounted("ws-1") is False

    async def test_multiple_projects(self, store: ProjectStore) -> None:
        r1 = make_project_record(name="P1")
        r1.workspaces = [WorkspaceMount(workspace_id="ws-1")]
        r2 = make_project_record(name="P2")
        r2.workspaces = [WorkspaceMount(workspace_id="ws-2")]
        await store.upsert(r1)
        await store.upsert(r2)
        assert await store.exists_workspace_mounted("ws-1") is True
        assert await store.exists_workspace_mounted("ws-2") is True
