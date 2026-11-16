# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""ProjectStore 的 agent → project 归属反查（M1 事件归属的唯一读路径）。"""

from __future__ import annotations

from pathlib import Path

from ghrah.subject.project.models import AgentSpec, make_project_record
from ghrah.subject.project.store import ProjectStore


def _with_agents(name: str, agents: list[AgentSpec]):
    record = make_project_record(name=name)
    return record.model_copy(update={"agents": agents})


async def test_project_id_for_agent_resolves_owner(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "subject.db")
    await store.start()
    try:
        p1 = _with_agents("p1", [AgentSpec(name="a1", agent_id="a" * 32)])
        p2 = _with_agents("p2", [AgentSpec(name="b1", agent_id="b" * 32)])
        await store.upsert(p1)
        await store.upsert(p2)

        assert await store.project_id_for_agent("a" * 32) == p1.project_id
        assert await store.project_id_for_agent("b" * 32) == p2.project_id
        assert await store.project_id_for_agent("c" * 32) is None
        assert await store.project_id_for_agent("") is None
    finally:
        await store.stop()


async def test_project_id_for_agent_ignores_soft_deleted_project(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "subject.db")
    await store.start()
    try:
        record = _with_agents("p1", [AgentSpec(name="a1", agent_id="a" * 32)])
        await store.upsert(record)
        assert await store.project_id_for_agent("a" * 32) == record.project_id

        await store.soft_delete(record.project_id, expected_version=1)
        assert await store.project_id_for_agent("a" * 32) is None
    finally:
        await store.stop()
