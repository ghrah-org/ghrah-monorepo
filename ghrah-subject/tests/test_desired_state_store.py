# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ghrah.subject.project.models import (
    AgentSpec,
    ProjectRecord,
    WorkspaceMount,
    make_project_record,
)
from ghrah.subject.recovery.desired_state import (
    DesiredStateRecord,
    DesiredStateStore,
    UnitSpec,
)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[DesiredStateStore]:
    s = DesiredStateStore(tmp_path / "desired.db")
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


def _project(name: str = "P1") -> ProjectRecord:
    record = make_project_record(name=name)
    return record.model_copy(
        update={
            "cluster_ids": ["c1"],
            "workspaces": [WorkspaceMount(workspace_id="ws1", default_for_agents=True)],
            "agents": [AgentSpec(name="a1", cluster_id="c1")],
        }
    )


class TestDesiredStateStore:
    async def test_start_idempotent(self, tmp_path: Path) -> None:
        s = DesiredStateStore(tmp_path / "d.db")
        await s.start()
        await s.start()
        await s.stop()

    async def test_load_empty_returns_none(self, store: DesiredStateStore) -> None:
        assert await store.load("default") is None

    async def test_save_and_load_roundtrip(self, store: DesiredStateStore) -> None:
        record = DesiredStateRecord(
            subject_id="default",
            projects=[_project()],
            units=[UnitSpec(name="third_party", enabled=True)],
        )
        await store.save(record)
        loaded = await store.load("default")
        assert loaded is not None
        assert loaded.subject_id == "default"
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "P1"
        assert len(loaded.units) == 1
        assert loaded.units[0].name == "third_party"

    async def test_save_derives_agents_from_projects(self, store: DesiredStateStore) -> None:
        p1 = _project("P1")
        p2 = make_project_record(name="P2")
        p2 = p2.model_copy(
            update={
                "cluster_ids": ["c2"],
                "agents": [
                    AgentSpec(name="a1", cluster_id="c2"),  # 跨 project 同名不是同一 Agent
                    AgentSpec(name="a2", cluster_id="c2"),
                ],
            }
        )
        record = DesiredStateRecord(subject_id="default", projects=[p1, p2])
        await store.save(record)
        loaded = await store.load("default")
        assert loaded is not None
        names = [a.name for a in loaded.agents]
        # 无 UUID 的旧记录也按 project/cluster/name 隔离，不能跨 project 合并。
        assert names == ["a1", "a1", "a2"]

    async def test_derive_agents_deduplicates_same_uuid_only(
        self, store: DesiredStateStore
    ) -> None:
        shared_id = "a" * 32
        p1 = _project("P1")
        p1 = p1.model_copy(
            update={"agents": [AgentSpec(name="old-name", cluster_id="c1", agent_id=shared_id)]}
        )
        p2 = make_project_record(name="P2").model_copy(
            update={
                "cluster_ids": ["c2"],
                "agents": [AgentSpec(name="new-name", cluster_id="c2", agent_id=shared_id)],
            }
        )
        await store.save(DesiredStateRecord(subject_id="default", projects=[p1, p2]))

        loaded = await store.load("default")
        assert loaded is not None
        assert [(a.agent_id, a.name) for a in loaded.agents] == [(shared_id, "old-name")]

    async def test_save_overwrites_single_row(self, store: DesiredStateStore) -> None:
        r1 = DesiredStateRecord(subject_id="default", projects=[_project("A")])
        r2 = DesiredStateRecord(subject_id="default", projects=[_project("B")])
        await store.save(r1)
        await store.save(r2)
        loaded = await store.load("default")
        assert loaded is not None
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "B"

    async def test_clear(self, store: DesiredStateStore) -> None:
        await store.save(DesiredStateRecord(subject_id="default", projects=[_project()]))
        assert await store.load("default") is not None
        await store.clear("default")
        assert await store.load("default") is None

    async def test_load_nonexistent_subject_returns_none(self, store: DesiredStateStore) -> None:
        await store.save(DesiredStateRecord(subject_id="default", projects=[]))
        assert await store.load("other") is None

    async def test_require_db_before_start(self, tmp_path: Path) -> None:
        s = DesiredStateStore(tmp_path / "x.db")
        with pytest.raises(RuntimeError, match="not started"):
            await s.load("default")
