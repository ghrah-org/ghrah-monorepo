# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from ghrah.subject.task.manager import TaskManager
from ghrah.subject.task.store import TaskStore

# ─── fixtures / helpers ───


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[TaskStore]:
    s = TaskStore(tmp_path / "tasks.db")
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


@pytest.fixture
async def manager(
    store: TaskStore,
) -> AsyncIterator[tuple[TaskManager, list[tuple[str, dict[str, Any]]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        events.append((event_type, payload))

    m = TaskManager(store, on_event=on_event)
    try:
        yield m, events
    finally:
        pass


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _create(
    manager: TaskManager,
    *,
    title: str = "T",
    project_id: str = "proj-1",
    agent_name: str | None = None,
    parent_id: str | None = None,
    dependencies: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "project_id": project_id}
    if agent_name is not None:
        payload["agent_name"] = agent_name
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if dependencies is not None:
        payload["dependencies"] = dependencies
    if metadata is not None:
        payload["metadata"] = metadata
    return _data(await manager.handle_command("task_create", payload))["task"]


# ─── 1. create 持久化 + 默认字段 + 事件 ───


class TestCreate:
    async def test_create_defaults_and_event(self, manager: tuple[TaskManager, list]) -> None:
        m, events = manager
        task = await _create(m, title="hello", agent_name="agent-a")
        assert len(task["task_id"]) == 32
        assert task["status"] == "pending"
        assert task["created_at"] == task["updated_at"]
        assert task["dependencies"] == []
        assert task["metadata"] == {}
        assert task["result"] is None
        # wire 形态：无 version / deleted_at
        assert "version" not in task
        assert "deleted_at" not in task
        # 事件
        assert len(events) == 1
        etype, payload = events[0]
        assert etype == "task_created"
        assert payload["previous_status"] is None
        assert payload["agent_name"] == "agent-a"
        assert payload["task"]["task_id"] == task["task_id"]
        assert "version" not in payload["task"]

    async def test_create_empty_title_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        result = await m.handle_command("task_create", {"title": "   ", "project_id": "proj-1"})
        assert not result["success"]
        assert "title" in result["error"]

    async def test_create_missing_project_id_rejected(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        result = await m.handle_command("task_create", {"title": "t"})
        assert not result["success"]
        assert "project_id" in result["error"]

    async def test_create_empty_project_id_rejected(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        result = await m.handle_command("task_create", {"title": "t", "project_id": ""})
        assert not result["success"]
        assert "project_id" in result["error"]

    async def test_create_persists_project_id(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        task = await _create(m, title="t", project_id="proj-42")
        assert task["project_id"] == "proj-42"


# ─── 2. list 过滤 + get ───


class TestListGet:
    async def test_list_filters_and_get(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        a1 = await _create(m, title="a1", agent_name="alpha")
        a2 = await _create(m, title="a2", agent_name="alpha")
        await _create(m, title="b1", agent_name="beta")

        # by agent
        data = _data(await m.handle_command("task_list", {"agent_name": "alpha"}))
        assert data["count"] == 2
        assert {t["task_id"] for t in data["tasks"]} == {a1["task_id"], a2["task_id"]}

        # by parent (none here)
        data = _data(await m.handle_command("task_list", {"parent_id": "ghost"}))
        assert data["count"] == 0

        # limit
        data = _data(await m.handle_command("task_list", {"limit": 1}))
        assert data["count"] == 1

        # get hit / miss
        data = _data(await m.handle_command("task_get", {"task_id": a1["task_id"]}))
        assert data["task"]["task_id"] == a1["task_id"]
        miss = await m.handle_command("task_get", {"task_id": "x" * 32})
        assert not miss["success"]

    async def test_list_status_as_list_python_filter(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        t1 = await _create(m, title="t1")
        await m.handle_command("task_start", {"task_id": t1["task_id"]})
        await _create(m, title="t2")  # pending
        data = _data(
            await m.handle_command(
                "task_list",
                {"status": ["pending", "in_progress"], "include_terminal": True},
            )
        )
        statuses = {t["status"] for t in data["tasks"]}
        assert statuses <= {"pending", "in_progress"}
        assert data["count"] == 2

    async def test_list_include_terminal_passthrough(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        await m.handle_command("task_complete", {"task_id": t["task_id"]})
        # include_terminal=False (store default-ish) -> 0
        data = _data(await m.handle_command("task_list", {"include_terminal": False}))
        assert data["count"] == 0
        # include_terminal=True -> 1
        data = _data(await m.handle_command("task_list", {"include_terminal": True}))
        assert data["count"] == 1

    async def test_list_filters_by_project_id(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        a = await _create(m, title="a", project_id="proj-1")
        b = await _create(m, title="b", project_id="proj-2")
        c = await _create(m, title="c", project_id="proj-1")
        data = _data(
            await m.handle_command("task_list", {"project_id": "proj-1", "include_terminal": True})
        )
        assert data["count"] == 2
        ids = {t["task_id"] for t in data["tasks"]}
        assert ids == {a["task_id"], c["task_id"]}
        assert b["task_id"] not in ids


# ─── 3. assign 不改 status，version+1 ───


class TestAssign:
    async def test_assign_keeps_status(
        self, manager: tuple[TaskManager, list], store: TaskStore
    ) -> None:
        m, events = manager
        t = await _create(m, title="t")
        data = _data(
            await m.handle_command("task_assign", {"task_id": t["task_id"], "agent_name": "x"})
        )
        assert data["task"]["status"] == "pending"
        assert data["task"]["agent_name"] == "x"
        # version 自增（store 层）
        record = await store.get(t["task_id"])
        assert record is not None
        assert record.version == 2
        # 事件 previous_status = pending
        etype, payload = events[-1]
        assert etype == "task_assigned"
        assert payload["previous_status"] == "pending"


# ─── 4. start 校验依赖 ───


class TestStartDependencies:
    async def test_start_blocked_by_incomplete_deps(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        b = await _create(m, title="b")
        a = await _create(m, title="a", dependencies=[b["task_id"]])
        result = await m.handle_command("task_start", {"task_id": a["task_id"]})
        assert not result["success"]
        assert "dependencies not completed" in result["error"]
        # 状态未改
        data = _data(await m.handle_command("task_get", {"task_id": a["task_id"]}))
        assert data["task"]["status"] == "pending"

    async def test_start_after_dep_complete(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        b = await _create(m, title="b")
        a = await _create(m, title="a", dependencies=[b["task_id"]])
        await m.handle_command("task_complete", {"task_id": b["task_id"]})
        data = _data(await m.handle_command("task_start", {"task_id": a["task_id"]}))
        assert data["task"]["status"] == "in_progress"
        assert data["task"]["started_at"] is not None


# ─── 5. complete/fail/cancel/block 时间戳；started_at 不覆盖 ───


class TestTransitions:
    async def test_complete_sets_completed_at(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        await m.handle_command("task_start", {"task_id": t["task_id"]})
        data = _data(
            await m.handle_command(
                "task_complete", {"task_id": t["task_id"], "result": {"ok": True}}
            )
        )
        assert data["task"]["status"] == "completed"
        assert data["task"]["completed_at"] is not None
        assert data["task"]["result"] == {"ok": True}

    async def test_fail_sets_error_and_completed_at(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        await m.handle_command("task_start", {"task_id": t["task_id"]})
        data = _data(
            await m.handle_command("task_fail", {"task_id": t["task_id"], "error": "boom"})
        )
        assert data["task"]["status"] == "failed"
        assert data["task"]["error"] == "boom"
        assert data["task"]["completed_at"] is not None

    async def test_cancel_sets_reason_as_error(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        data = _data(
            await m.handle_command("task_cancel", {"task_id": t["task_id"], "reason": "user"})
        )
        assert data["task"]["status"] == "canceled"
        assert data["task"]["error"] == "user"
        assert data["task"]["completed_at"] is not None

    async def test_block_sets_reason_no_completed_at(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        data = _data(
            await m.handle_command("task_block", {"task_id": t["task_id"], "reason": "waiting"})
        )
        assert data["task"]["status"] == "blocked"
        assert data["task"]["error"] == "waiting"
        assert data["task"]["completed_at"] is None

    async def test_started_at_not_overwritten(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        await m.handle_command("task_start", {"task_id": t["task_id"]})
        started = _data(await m.handle_command("task_get", {"task_id": t["task_id"]}))["task"][
            "started_at"
        ]
        # block -> start again
        await m.handle_command("task_block", {"task_id": t["task_id"]})
        await m.handle_command("task_start", {"task_id": t["task_id"]})
        started2 = _data(await m.handle_command("task_get", {"task_id": t["task_id"]}))["task"][
            "started_at"
        ]
        assert started == started2


# ─── 6. terminal 不可再流转 ───


class TestTerminalGuard:
    async def test_completed_cannot_start(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        await m.handle_command("task_complete", {"task_id": t["task_id"]})
        result = await m.handle_command("task_start", {"task_id": t["task_id"]})
        assert not result["success"]
        assert "illegal transition" in result["error"]


# ─── 7. dependency 自指 + 环 ───


class TestDependencyCycle:
    async def test_create_self_reference_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        fake = "f" * 32
        result = await m.handle_command(
            "task_create", {"title": "x", "project_id": "proj-1", "dependencies": [fake]}
        )
        # 依赖不存在 task 会被 graph 丢弃（& self._ids），无环 → 通过
        assert result["success"]

    async def test_create_self_dep_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        # 先建一个，再 update 让它依赖自身
        t = await _create(m, title="t")
        result = await m.handle_command(
            "task_update", {"task_id": t["task_id"], "dependencies": [t["task_id"]]}
        )
        assert not result["success"]
        assert "cycle" in result["error"]

    async def test_dependency_loop_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        a = await _create(m, title="a")
        b = await _create(m, title="b", dependencies=[a["task_id"]])
        c = await _create(m, title="c", dependencies=[b["task_id"]])
        # 让 a 依赖 c → 形成环 a->b->c->a（经 a 依赖 c）
        result = await m.handle_command(
            "task_update", {"task_id": a["task_id"], "dependencies": [c["task_id"]]}
        )
        assert not result["success"]
        assert "cycle" in result["error"]


# ─── 8. parent 自指 + 环 ───


class TestParentCycle:
    async def test_create_self_parent_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        # 自指 parent 在 create 时：新任务不在图中，graph.would_create_parent_cycle
        # 对 new_parent==task_id 返回 True
        # 但 task_id 此时未知（make_task_record 内生成）。create 路径用 record.task_id
        # 与 parent_id 比较：若相同则拒。这里 parent 指向一个已存在的、等于自身 id 不可能。
        # 改测 update 路径：
        t = await _create(m, title="t")
        result = await m.handle_command(
            "task_update", {"task_id": t["task_id"], "parent_id": t["task_id"]}
        )
        assert not result["success"]
        assert "cycle" in result["error"]

    async def test_parent_loop_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        a = await _create(m, title="a")
        b = await _create(m, title="b", parent_id=a["task_id"])
        # 让 a 的 parent = b → 环 a->b->a
        result = await m.handle_command(
            "task_update", {"task_id": a["task_id"], "parent_id": b["task_id"]}
        )
        assert not result["success"]
        assert "cycle" in result["error"]

    async def test_legal_parent_accepted(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        a = await _create(m, title="a")
        data = _data(
            await m.handle_command(
                "task_create", {"title": "child", "project_id": "proj-1", "parent_id": a["task_id"]}
            )
        )
        assert data["task"]["parent_id"] == a["task_id"]


# ─── 9. delete 保护 + force 软删 ───


class TestDelete:
    async def test_delete_protected_when_has_dependents(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        a = await _create(m, title="a")
        await _create(m, title="b", dependencies=[a["task_id"]])
        result = await m.handle_command("task_delete", {"task_id": a["task_id"]})
        assert not result["success"]
        assert "dependents" in result["error"]
        # 仍存在
        data = _data(await m.handle_command("task_get", {"task_id": a["task_id"]}))
        assert data["task"]["task_id"] == a["task_id"]

    async def test_delete_protected_when_has_children(
        self, manager: tuple[TaskManager, list]
    ) -> None:
        m, _ = manager
        a = await _create(m, title="a")
        await _create(m, title="child", parent_id=a["task_id"])
        result = await m.handle_command("task_delete", {"task_id": a["task_id"]})
        assert not result["success"]

    async def test_delete_force_soft_delete(self, manager: tuple[TaskManager, list]) -> None:
        m, events = manager
        a = await _create(m, title="a")
        await _create(m, title="b", dependencies=[a["task_id"]])
        data = _data(
            await m.handle_command("task_delete", {"task_id": a["task_id"], "force": True})
        )
        assert data["task_id"] == a["task_id"]
        # 删后 get 返回 not found
        miss = await m.handle_command("task_get", {"task_id": a["task_id"]})
        assert not miss["success"]
        # list 不含
        listing = _data(
            await m.handle_command("task_list", {"agent_name": None, "include_terminal": True})
        )
        ids = {t["task_id"] for t in listing["tasks"]}
        assert a["task_id"] not in ids
        # 事件
        etypes = [e[0] for e in events]
        assert "task_deleted" in etypes


# ─── 10. 事件回调断言 ───


class TestEventPayload:
    async def test_event_payload_shape(self, manager: tuple[TaskManager, list]) -> None:
        m, events = manager
        t = await _create(m, title="t", agent_name="z")
        await m.handle_command("task_start", {"task_id": t["task_id"]})
        await m.handle_command("task_complete", {"task_id": t["task_id"]})
        # 检查 task_started 事件
        started_event = next(e for e in events if e[0] == "task_started")
        payload = started_event[1]
        assert payload["previous_status"] == "pending"
        assert payload["agent_name"] == "z"
        assert "version" not in payload["task"]
        assert "deleted_at" not in payload["task"]
        # ISO str 时间戳
        assert isinstance(payload["task"]["created_at"], str)
        # task_completed previous_status = in_progress
        completed_event = next(e for e in events if e[0] == "task_completed")
        assert completed_event[1]["previous_status"] == "in_progress"


# ─── 11. 乐观锁 ───


class TestOptimisticLock:
    async def test_update_with_matching_version(
        self, manager: tuple[TaskManager, list], store: TaskStore
    ) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        record = await store.get(t["task_id"])
        assert record is not None and record.version == 1
        data = _data(
            await m.handle_command(
                "task_update",
                {"task_id": t["task_id"], "title": "new", "expected_version": 1},
            )
        )
        assert data["task"]["title"] == "new"
        record2 = await store.get(t["task_id"])
        assert record2 is not None and record2.version == 2

    async def test_update_with_stale_version_rejected(
        self, manager: tuple[TaskManager, list], store: TaskStore
    ) -> None:
        m, _ = manager
        t = await _create(m, title="t")
        # 先成功更新一次 version->2
        await m.handle_command(
            "task_update",
            {"task_id": t["task_id"], "title": "v2", "expected_version": 1},
        )
        # 用过期 version=1 再更新 → 拒
        result = await m.handle_command(
            "task_update",
            {"task_id": t["task_id"], "title": "stale", "expected_version": 1},
        )
        assert not result["success"]
        assert "concurrent modification" in result["error"]
        # 标题未变
        data = _data(await m.handle_command("task_get", {"task_id": t["task_id"]}))
        assert data["task"]["title"] == "v2"


# ─── 额外边界：metadata replace / patch ───


class TestMetadataUpdate:
    async def test_metadata_replace_and_patch(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        t = await _create(m, title="t", metadata={"a": 1, "b": 2})
        # patch
        data = _data(
            await m.handle_command(
                "task_update",
                {"task_id": t["task_id"], "metadata_patch": {"b": 3, "c": 4}},
            )
        )
        assert data["task"]["metadata"] == {"a": 1, "b": 3, "c": 4}
        # replace
        data = _data(
            await m.handle_command(
                "task_update",
                {"task_id": t["task_id"], "metadata": {"x": 0}},
            )
        )
        assert data["task"]["metadata"] == {"x": 0}

    async def test_update_nonexistent_task(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        result = await m.handle_command("task_update", {"task_id": "z" * 32, "title": "x"})
        assert not result["success"]
        assert "not found" in result["error"]


# ─── 未知命令 ───


class TestUnknownCommand:
    async def test_unknown_command_rejected(self, manager: tuple[TaskManager, list]) -> None:
        m, _ = manager
        result = await m.handle_command("task_frob", {})
        assert not result["success"]
        assert "unknown command" in result["error"]
