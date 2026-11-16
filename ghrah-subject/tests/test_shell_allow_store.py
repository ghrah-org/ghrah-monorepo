# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from pathlib import Path

from ghrah.subject.project.models import ShellAllowEntry, make_project_record
from ghrah.subject.project.store import ProjectStore


async def test_shell_allow_survives_restart_and_keeps_project_boundary(tmp_path: Path) -> None:
    db_path = tmp_path / "projects.db"
    store = ProjectStore(db_path)
    await store.start()
    first = make_project_record(name="A")
    second = make_project_record(name="B")
    entry = ShellAllowEntry(kind="class", key="pnpm:script:lint", label="pnpm lint")
    first.shell_allow = [entry]
    try:
        await store.upsert(first)
        await store.upsert(second)
    finally:
        await store.stop()

    reopened = ProjectStore(db_path)
    await reopened.start()
    try:
        saved = await reopened.get(first.project_id)
        other = await reopened.get(second.project_id)
        assert saved is not None and other is not None
        assert saved.shell_allow == [entry]
        assert other.shell_allow == []
        assert "shell_allow" not in saved.to_wire()
        await reopened.update(
            first.project_id, saved.version, lambda r: r.model_copy(update={"shell_allow": []})
        )
        saved = await reopened.get(first.project_id)
        assert saved is not None
        assert saved.shell_allow == []
    finally:
        await reopened.stop()


async def test_shell_allow_migrates_v5_without_losing_project_data(tmp_path: Path) -> None:
    db_path = tmp_path / "v5.db"
    store = ProjectStore(db_path)
    await store.start()
    record = make_project_record(name="Existing", description="keep me")
    try:
        await store.upsert(record)
    finally:
        await store.stop()
    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE subject_projects DROP COLUMN shell_allow")
        db.execute("UPDATE subject_schema_versions SET version=5 WHERE component='project_store'")

    for _ in range(2):
        migrated = ProjectStore(db_path)
        await migrated.start()
        try:
            saved = await migrated.get(record.project_id)
            assert saved is not None
            assert saved.name == "Existing"
            assert saved.description == "keep me"
            assert saved.shell_allow == []
        finally:
            await migrated.stop()
