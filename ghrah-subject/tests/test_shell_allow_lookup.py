# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Subject 项目记忆经生产工厂传入 Core，查询始终读项目权威。"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.models import ShellAllowEntry, make_project_record
from ghrah.subject.project.store import ProjectStore
from ghrah.subject.runtime.service_keys import MANIFEST_STORE, PROJECT_STORE
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit


@pytest.fixture
async def wired(tmp_path):
    store = ProjectStore(tmp_path / "subject.db")
    await store.start()
    services = {PROJECT_STORE.name: store, MANIFEST_STORE.name: object()}
    ctx = SimpleNamespace(get=services.__getitem__, provide=services.__setitem__)
    unit = CoreClusterRegistryUnit(SubjectConfig(observer_http_api_enabled=False))
    await unit.init(ctx)
    try:
        yield store, unit._unit_factory.build_config
    finally:
        await unit.stop()
        await store.stop()


async def test_project_bound_lookup_reads_changes_without_restart(wired, tmp_path):
    store, factory = wired
    first = make_project_record(name="A")
    second = make_project_record(name="B")
    await store.upsert(first)
    await store.upsert(second)
    a = factory(first.project_id, (tmp_path / "a").as_uri())
    b = factory(second.project_id, (tmp_path / "b").as_uri())
    lookup_a = a.shell_allow_lookup
    lookup_b = b.shell_allow_lookup
    assert await lookup_a() == []
    entry = ShellAllowEntry(kind="class", key="pnpm:script:lint", label="lint")
    updated = await store.update(
        first.project_id, first.version, lambda r: r.model_copy(update={"shell_allow": [entry]})
    )
    assert await lookup_a() == [entry.model_dump(mode="json")]
    assert await lookup_b() == []
    await store.update(
        first.project_id, updated.version, lambda r: r.model_copy(update={"shell_allow": []})
    )
    assert await lookup_a() == []


async def test_database_failure_is_logged_as_empty_memory(wired, tmp_path, monkeypatch, caplog):
    store, factory = wired
    core = factory("missing", (tmp_path / "a").as_uri())
    assert await core.shell_allow_lookup() == []

    async def failed_get(project_id):
        raise sqlite3.OperationalError("test read failure")

    monkeypatch.setattr(store, "get", failed_get)
    assert await core.shell_allow_lookup() == []
    assert "shell allow lookup failed" in caplog.text
