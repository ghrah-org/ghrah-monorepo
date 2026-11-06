from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from ghrah.protocol.types import RecoveryAction

from ghrah.subject.project.manager import ProjectManager
from ghrah.subject.project.models import (
    ProjectStatus,
)
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.project.store import ProjectStore
from ghrah.subject.room.models import make_room_record
from ghrah.subject.room.store import RoomStore
from ghrah.subject.workspace.locator import path_to_locator
from ghrah.subject.workspace.models import WorkspaceRecord

# ─── fakes ───


@dataclass
class _FakeAgentWorkspace:
    record: WorkspaceRecord
    path: str = "ws"


class _FakeWorkspaceManager:
    """Fake WorkspaceManager：register/list/get_record，不触文件系统。"""

    def __init__(self) -> None:
        self._records: dict[str, WorkspaceRecord] = {}

    def list_records(self) -> list[WorkspaceRecord]:
        return list(self._records.values())

    def get_record(self, workspace_id: str) -> WorkspaceRecord | None:
        return self._records.get(workspace_id)

    async def register_workspace(
        self, locator: str, *, name: str = "", provider_type: str | None = None
    ) -> _FakeAgentWorkspace:
        record = WorkspaceRecord(
            name=name or "default",
            provider_type=provider_type or "git",
            subject_id="default",
            locator=locator,
        )
        self._records[record.workspace_id] = record
        return _FakeAgentWorkspace(record=record)

    async def unregister_workspace(self, workspace_id: str) -> None:
        self._records.pop(workspace_id, None)


class _FakeTaskMgr:
    def __init__(self, task_exists: bool = True) -> None:
        self._exists = task_exists

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "task_get":
            if self._exists:
                return {
                    "success": True,
                    "data": {
                        "task": {
                            "task_id": payload.get("task_id"),
                            "project_id": payload.get("project_id", ""),
                        }
                    },
                }
            return {"success": False, "data": None, "error": "not found"}
        return {"success": False, "data": None, "error": "unsupported"}


class _FakeClusterTransport:
    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.shutdowns: list[str] = []
        self._handle = _FakeHandle()
        self.fail_ensure = False
        self.fail_shutdown = False

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> _FakeHandle:
        self.ensured.append(cluster_id)
        if self.fail_ensure:
            raise RuntimeError("cluster unavailable")
        return self._handle

    def get_handle(self, cluster_id: str) -> _FakeHandle:
        return self._handle

    def has_cluster(self, cluster_id: str) -> bool:
        return True

    async def shutdown_cluster(self, cluster_id: str) -> None:
        if self.fail_shutdown:
            raise RuntimeError("shutdown unavailable")
        self.shutdowns.append(cluster_id)

    async def stop(self) -> None:
        pass


class _FakeHandle:
    def __init__(self) -> None:
        self.spawned: list[str] = []
        self.terminated: list[str] = []
        self._list: list[dict[str, Any]] = []
        self.fail_spawn = False
        # 非 None 时 terminate_agent 返回该错误（模拟运行侧失败/已不存在）
        self.terminate_error: str | None = None

    @property
    def is_connected(self) -> bool:
        return True

    async def spawn_agent(self, payload: Any) -> dict[str, Any]:
        self.spawned.append(payload.config.name)
        if self.fail_spawn:
            return {"success": False, "data": None, "error": "spawn unavailable"}
        return {"success": True, "data": {"name": payload.config.name}, "error": None}

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return list(self._list)

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        self.terminated.append(agent_name)
        if self.terminate_error is not None:
            return {"success": False, "data": None, "error": self.terminate_error}
        return {"success": True, "data": None, "error": None}

    async def shutdown(self) -> None:
        pass


class _FakeManifestStore:
    pass


class _FakeScopedResource:
    def __init__(self) -> None:
        self.closed: list[str] = []
        self.restored: list[str] = []
        self.evicted: list[str] = []
        self.fail_close = False

    async def close_project(self, project_id: str) -> None:
        if self.fail_close:
            raise RuntimeError("close unavailable")
        self.closed.append(project_id)

    async def restore_project(self, project_id: str) -> None:
        self.restored.append(project_id)

    async def evict_project(self, project_id: str) -> None:
        self.evicted.append(project_id)


class _FakeRoomStore(_FakeScopedResource):
    def __init__(self, room_count: int = 0) -> None:
        super().__init__()
        self.room_count = room_count

    async def count_project_records(self, project_id: str) -> int:
        return self.room_count


def _default_locator(tmp_path: Path) -> str:
    return path_to_locator(str(tmp_path / "default"))


def _workspace_input(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        {
            "locator": _default_locator(tmp_path),
            "name": "default",
            "default_for_agents": True,
        }
    ]


# ─── fixtures ───


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ProjectStore]:
    s = ProjectStore(tmp_path / "projects.db")
    await s.start()
    try:
        yield s
    finally:
        await s.stop()


def _make_manager(
    store: ProjectStore,
    *,
    events: list[tuple[str, dict[str, Any]]] | None = None,
    default_locator: str = "file:///workspaces/default",
    task_mgr: _FakeTaskMgr | None = None,
    cluster_transport: _FakeClusterTransport | None = None,
    scoped_resource: _FakeScopedResource | None = None,
    room_store: _FakeRoomStore | None = None,
) -> ProjectManager:
    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, payload))

    manager = ProjectManager(
        store,
        _FakeWorkspaceManager(),
        task_mgr or _FakeTaskMgr(),
        cluster_transport or _FakeClusterTransport(),
        _FakeManifestStore(),
        on_event=on_event if events is not None else None,
        bootstrap_workspace_locator=default_locator,
        default_root_locator_template=str(store._db_path.parent / "roots/{project_id}"),
    )
    if scoped_resource is not None:
        manager.register_scoped_resource(scoped_resource)
    if room_store is not None:
        manager.register_scoped_resource(room_store, room_store=True)
    return manager


class TestProjectCreate:
    async def test_create_success(self, store: ProjectStore, tmp_path: Path) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        mgr = _make_manager(store, events=events, default_locator=_default_locator(tmp_path))
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "description": "Example project",
                "writable_workspaces": _workspace_input(tmp_path),
            },
        )
        assert result["success"], result.get("error")
        project = result["data"]["project"]
        assert project["name"] == "P1"
        assert project["description"] == "Example project"
        assert len(project["cluster_ids"]) == 1
        assert len(project["workspaces"]) == 1
        assert project["workspaces"][0]["default_for_agents"] is True
        assert project["status"] == ProjectStatus.ACTIVE.value
        # 事件
        assert events[0][0] == "project_created"

    async def test_create_requires_name(self, store: ProjectStore) -> None:
        mgr = _make_manager(store)
        result = await mgr.handle_command("project_create", {})
        assert not result["success"]
        assert "name" in (result["error"] or "")

    async def test_create_allows_no_writable_workspaces(self, store: ProjectStore) -> None:
        mgr = _make_manager(store, default_locator="file:///bootstrap/default")
        result = await mgr.handle_command(
            "project_create", {"name": "P1", "writable_workspaces": []}
        )
        assert result["success"], result.get("error")
        assert result["data"]["project"]["workspaces"] == []

    async def test_create_rejects_removed_workspace_fields(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store)
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "default_workspace_locator": _default_locator(tmp_path),
            },
        )
        assert not result["success"]
        assert "default_workspace_locator" in (result["error"] or "")
        assert await store.list() == []

    async def test_create_multiple_writable_workspaces(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator="")
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "writable_workspaces": [
                    {"locator": str(tmp_path / "source"), "name": "source"},
                    {
                        "locator": str(tmp_path / "output"),
                        "name": "output",
                        "role": "artifacts",
                        "default_for_agents": True,
                    },
                ],
            },
        )
        assert result["success"], result.get("error")
        mounts = result["data"]["project"]["workspaces"]
        assert len(mounts) == 2
        assert [m["default_for_agents"] for m in mounts] == [False, True]

    async def test_create_multiple_workspaces_requires_default(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator="")
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "writable_workspaces": [
                    {"locator": str(tmp_path / "source")},
                    {"locator": str(tmp_path / "output")},
                ],
            },
        )
        assert not result["success"]
        assert "exactly one" in (result["error"] or "")

    async def test_create_rejects_empty_workspace_locator(self, store: ProjectStore) -> None:
        mgr = _make_manager(store, default_locator="")
        result = await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": [{"locator": "  "}]},
        )
        assert not result["success"]
        assert "locator required" in (result["error"] or "")

    async def test_create_rejects_root_workspace_overlap(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        root = tmp_path / "private"
        mgr = _make_manager(store, default_locator="")
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "project_root_locator": str(root),
                "writable_workspaces": [{"locator": str(root / "source")}],
            },
        )
        assert not result["success"]
        assert "overlaps a writable Workspace" in (result["error"] or "")
        assert not root.exists()

    async def test_create_rejects_nested_project_roots(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator="")
        first_root = tmp_path / "projects" / "one"
        first = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "project_root_locator": str(first_root),
                "writable_workspaces": [],
            },
        )
        assert first["success"], first.get("error")
        second = await mgr.handle_command(
            "project_create",
            {
                "name": "P2",
                "project_root_locator": str(first_root / "nested"),
                "writable_workspaces": [],
            },
        )
        assert not second["success"]
        assert "Project Roots overlap" in (second["error"] or "")

    async def test_concurrent_create_serializes_root_claim(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator="")
        root = str(tmp_path / "exclusive")
        first, second = await asyncio.gather(
            mgr.handle_command(
                "project_create",
                {"name": "P1", "project_root_locator": root, "writable_workspaces": []},
            ),
            mgr.handle_command(
                "project_create",
                {"name": "P2", "project_root_locator": root, "writable_workspaces": []},
            ),
        )
        assert sum(1 for result in (first, second) if result["success"]) == 1
        assert len(await store.list()) == 1

    async def test_create_rolls_back_root_workspace_and_record(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        root = tmp_path / "private"
        workspace = tmp_path / "source"
        mgr = _make_manager(store, default_locator="")
        mgr._cluster_transport.fail_ensure = True  # type: ignore[attr-defined]
        result = await mgr.handle_command(
            "project_create",
            {
                "name": "P1",
                "project_root_locator": str(root),
                "writable_workspaces": [{"locator": str(workspace)}],
            },
        )
        assert not result["success"]
        assert await store.list() == []
        assert mgr._workspace_mgr.list_records() == []  # type: ignore[attr-defined]
        assert not root.exists()


class TestProjectGetList:
    async def test_get_and_list(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": _workspace_input(tmp_path)},
        )
        project_id = created["data"]["project"]["project_id"]

        got = await mgr.handle_command("project_get", {"project_id": project_id})
        assert got["success"]
        assert got["data"]["project"]["project_id"] == project_id

        listed = await mgr.handle_command("project_list", {})
        assert listed["success"]
        assert listed["data"]["count"] == 1

    async def test_get_missing(self, store: ProjectStore) -> None:
        mgr = _make_manager(store)
        result = await mgr.handle_command("project_get", {"project_id": "nope"})
        assert not result["success"]


class TestProjectAddRemoveAgent:
    async def _create(self, mgr: ProjectManager, tmp_path: Path) -> dict[str, Any]:
        return await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": _workspace_input(tmp_path)},
        )

    async def test_add_agent(self, store: ProjectStore, tmp_path: Path) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        mgr = _make_manager(store, events=events, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        cluster_id = project["cluster_ids"][0]
        workspace_id = project["workspaces"][0]["workspace_id"]

        result = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {
                    "name": "a1",
                    "cluster_id": cluster_id,
                    "path_grants": [{"workspace_id": workspace_id, "subpath": "."}],
                },
            },
        )
        assert result["success"], result.get("error")
        assert len(result["data"]["project"]["agents"]) == 1
        agent_id = result["data"]["agent_id"]
        assert len(agent_id) == 32
        assert result["data"]["project"]["agents"][0]["agent_id"] == agent_id
        assert events[-1][1]["agent_id"] == agent_id
        assert events[-1][0] == "project_agent_added"
        # spawn 调用
        assert mgr._cluster_transport._handle.spawned == ["a1"]  # type: ignore[attr-defined]

    async def test_add_agent_rejects_unknown_cluster(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        result = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": created["data"]["project"]["project_id"],
                "agent": {"name": "a1", "cluster_id": "unknown-cluster"},
            },
        )
        assert not result["success"]
        assert "cluster_id" in (result["error"] or "")

    async def test_add_agent_spawn_failure_keeps_diagnostic_desired_state(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        cluster = _FakeClusterTransport()
        cluster._handle.fail_spawn = True
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            cluster_transport=cluster,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        result = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {
                    "name": "pending-agent",
                    "cluster_id": project["cluster_ids"][0],
                },
            },
        )
        assert result["success"] is True
        assert result["data"]["runtime_pending"] is True
        assert result["data"]["runtime_error"] == "spawn unavailable"
        stored = await store.get(project["project_id"])
        assert stored is not None
        assert [agent.name for agent in stored.agents] == ["pending-agent"]

    async def test_duplicate_name_is_rejected_within_project(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        cluster_id = project["cluster_ids"][0]
        first = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "same-name", "cluster_id": cluster_id},
            },
        )
        assert first["success"]
        duplicate = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "same-name", "cluster_id": cluster_id},
            },
        )
        assert duplicate["success"] is False
        assert duplicate["error"] == "agent_name_exists"
        assert "already exists in project" in duplicate["error_detail"]

    async def test_remove_agent(self, store: ProjectStore, tmp_path: Path) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        mgr = _make_manager(store, events=events, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        cluster_id = project["cluster_ids"][0]
        added = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "a1", "cluster_id": cluster_id},
            },
        )
        result = await mgr.handle_command(
            "project_remove_agent",
            {
                "project_id": project["project_id"],
                "agent_id": added["data"]["agent_id"],
                "agent_name": "a1",
            },
        )
        assert result["success"], result.get("error")
        assert result["data"]["project"]["agents"] == []
        assert events[-1][0] == "project_agent_removed"

    async def test_add_agent_persists_runtime_diagnostics(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        """运行诊断随定义持久化（error/running），重启后仍可读。"""
        events: list[tuple[str, dict[str, Any]]] = []
        transport = _FakeClusterTransport()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=transport,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        pid = project["project_id"]
        cluster_id = project["cluster_ids"][0]

        # 失败路径：spawn 失败 → runtime_status=error + 诊断原因
        transport._handle.fail_spawn = True
        failed = await mgr.handle_command(
            "project_add_agent",
            {"project_id": pid, "agent": {"name": "broken", "cluster_id": cluster_id}},
        )
        assert failed["success"]
        assert failed["data"]["runtime_pending"] is True
        stored = await store.get(pid)
        assert stored is not None
        broken = next(a for a in stored.agents if a.name == "broken")
        assert broken.runtime_status == "error"
        assert broken.runtime_error == "spawn unavailable"

        # 成功路径：runtime_status=running、error 清空
        transport._handle.fail_spawn = False
        ok_add = await mgr.handle_command(
            "project_add_agent",
            {"project_id": pid, "agent": {"name": "healthy", "cluster_id": cluster_id}},
        )
        assert ok_add["success"]
        stored = await store.get(pid)
        assert stored is not None
        healthy = next(a for a in stored.agents if a.name == "healthy")
        assert healthy.runtime_status == "running"
        assert healthy.runtime_error == ""

        # 重启（同 db 新 store）后诊断仍可读
        reopened = ProjectStore(store._db_path)
        await reopened.start()
        try:
            reread = await reopened.get(pid)
        finally:
            await reopened.stop()
        assert reread is not None
        statuses = {a.name: a.runtime_status for a in reread.agents}
        assert statuses == {"broken": "error", "healthy": "running"}

    async def test_remove_agent_stale_version_does_not_terminate(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        """过期删除请求必须先拒绝，不能先停止实例再返回版本冲突。"""
        events: list[tuple[str, dict[str, Any]]] = []
        transport = _FakeClusterTransport()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=transport,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        added = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {
                    "name": "a1",
                    "cluster_id": project["cluster_ids"][0],
                },
            },
        )
        event_count = len(events)
        result = await mgr.handle_command(
            "project_remove_agent",
            {
                "project_id": project["project_id"],
                "agent_id": added["data"]["agent_id"],
                "agent_name": "a1",
                "expected_version": project["version"],
            },
        )
        assert result["success"] is False
        assert "expected version" in result["error"]
        assert transport._handle.terminated == []
        record = await store.get(project["project_id"])
        assert record is not None
        assert [a.agent_id for a in record.agents] == [added["data"]["agent_id"]]
        assert len(events) == event_count

    async def test_remove_agent_terminate_failure_keeps_definition(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        """回归：terminate 失败 → 定义保留、无 removed 事件、稳定错误码。"""
        events: list[tuple[str, dict[str, Any]]] = []
        transport = _FakeClusterTransport()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=transport,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        added = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "a1", "cluster_id": project["cluster_ids"][0]},
            },
        )
        transport._handle.terminate_error = "core transport down"
        event_count = len(events)

        result = await mgr.handle_command(
            "project_remove_agent",
            {
                "project_id": project["project_id"],
                "agent_id": added["data"]["agent_id"],
                "agent_name": "a1",
            },
        )
        assert result["success"] is False
        assert result["error"] == "agent_terminate_failed"
        assert result["error_detail"] == "core transport down"
        # 定义仍在、无 removed 事件（宁可不删，不留游离 runtime）
        stored = await store.get(project["project_id"])
        assert stored is not None
        assert [a.name for a in stored.agents] == ["a1"]
        assert len(events) == event_count

    async def test_remove_agent_terminate_absent_proceeds(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        """运行侧已无该 agent（identity mismatch）→ 视为已终止，继续删定义。"""
        events: list[tuple[str, dict[str, Any]]] = []
        transport = _FakeClusterTransport()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=transport,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        added = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "a1", "cluster_id": project["cluster_ids"][0]},
            },
        )
        transport._handle.terminate_error = "agent_identity_mismatch"

        result = await mgr.handle_command(
            "project_remove_agent",
            {
                "project_id": project["project_id"],
                "agent_id": added["data"]["agent_id"],
                "agent_name": "a1",
            },
        )
        assert result["success"], result.get("error")
        assert result["data"]["project"]["agents"] == []
        assert events[-1][0] == "project_agent_removed"

    async def test_remove_agent_unmounted_cluster_skips_terminate(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        """cluster 未挂载（stopped 后）→ 无 runtime 可终止，直接删定义。"""

        class _EmptyTransport(_FakeClusterTransport):
            def has_cluster(self, cluster_id: str) -> bool:
                return False

        events: list[tuple[str, dict[str, Any]]] = []
        transport = _EmptyTransport()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=transport,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        added = await mgr.handle_command(
            "project_add_agent",
            {
                "project_id": project["project_id"],
                "agent": {"name": "a1", "cluster_id": project["cluster_ids"][0]},
            },
        )
        result = await mgr.handle_command(
            "project_remove_agent",
            {
                "project_id": project["project_id"],
                "agent_id": added["data"]["agent_id"],
                "agent_name": "a1",
            },
        )
        assert result["success"], result.get("error")
        assert result["data"]["project"]["agents"] == []
        assert transport._handle.terminated == []


class TestProjectStatusTransitions:
    async def _create(self, mgr: ProjectManager, tmp_path: Path) -> dict[str, Any]:
        return await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": _workspace_input(tmp_path)},
        )

    async def test_pause_resume_stop(self, store: ProjectStore, tmp_path: Path) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        mgr = _make_manager(store, events=events, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        pid = created["data"]["project"]["project_id"]
        ct = mgr._cluster_transport  # type: ignore[attr-defined]

        paused = await mgr.handle_command("project_pause", {"project_id": pid})
        assert paused["success"]
        assert paused["data"]["project"]["status"] == ProjectStatus.PAUSED.value
        assert ct.shutdowns  # pause shutdown cluster

        resumed = await mgr.handle_command("project_resume", {"project_id": pid})
        assert resumed["success"]
        assert resumed["data"]["project"]["status"] == ProjectStatus.ACTIVE.value

        stopped = await mgr.handle_command("project_stop", {"project_id": pid})
        assert stopped["success"]
        assert stopped["data"]["project"]["status"] == ProjectStatus.STOPPED.value

    async def test_illegal_transition_rejected(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        pid = created["data"]["project"]["project_id"]
        # ACTIVE -> STOPPED 直接（合法），但 PAUSED -> ACTIVE 后再 PAUSED...
        stopped = await mgr.handle_command("project_stop", {"project_id": pid})
        assert stopped["success"]
        # STOPPED -> PAUSED 非法
        bad = await mgr.handle_command("project_pause", {"project_id": pid})
        assert not bad["success"]
        assert "transition" in (bad["error"] or "")


class TestProjectLifecycleAndDelete:
    async def _create(self, mgr: ProjectManager, tmp_path: Path) -> dict[str, Any]:
        return await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": _workspace_input(tmp_path)},
        )

    async def test_set_recovery(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        pid = created["data"]["project"]["project_id"]
        result = await mgr.handle_command(
            "project_set_recovery",
            {"project_id": pid, "recovery": RecoveryAction.PAUSE},
        )
        assert result["success"]
        assert result["data"]["project"]["recovery"] == RecoveryAction.PAUSE.value

    async def test_archive_restore_freezes_root_and_keeps_workspace(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        cluster = _FakeClusterTransport()
        resource = _FakeScopedResource()
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            cluster_transport=cluster,
            scoped_resource=resource,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        pid = project["project_id"]
        paths = ProjectPaths.from_locator(project["project_root_locator"])
        workspace_file = tmp_path / "default" / "external.txt"
        workspace_file.parent.mkdir(parents=True, exist_ok=True)
        workspace_file.write_text("external", encoding="utf-8")

        archived_result = await mgr.handle_command(
            "project_archive",
            {"project_id": pid, "expected_version": project["version"]},
        )
        assert archived_result["success"], archived_result.get("error")
        archived = archived_result["data"]["project"]
        assert archived["archived_at"] is not None
        assert archived["status"] == ProjectStatus.STOPPED.value
        assert paths.root.exists()
        assert workspace_file.read_text(encoding="utf-8") == "external"
        assert cluster.shutdowns == project["cluster_ids"]
        assert resource.closed == [pid]
        assert events[-1] == ("project_archived", {"project": archived})
        assert await store.get(pid) is None
        assert (await store.get(pid, include_archived=True)) is not None

        event_count = len(events)
        repeated = await mgr.handle_command(
            "project_archive",
            {"project_id": pid, "expected_version": project["version"]},
        )
        assert repeated["success"]
        assert len(events) == event_count
        assert len(cluster.shutdowns) == 1

        for command, payload in (
            (
                "project_update",
                {"project_id": pid, "name": "blocked", "expected_version": archived["version"]},
            ),
            (
                "project_add_agent",
                {
                    "project_id": pid,
                    "agent": {
                        "agent_id": "a" * 32,
                        "name": "blocked",
                        "cluster_id": project["cluster_ids"][0],
                    },
                },
            ),
            (
                "project_remove_agent",
                {"project_id": pid, "agent_id": "a" * 32, "agent_name": "blocked"},
            ),
            ("project_link_task", {"project_id": pid, "task_id": "blocked"}),
            ("project_unlink_task", {"project_id": pid, "task_id": "blocked"}),
            ("project_pause", {"project_id": pid}),
            ("project_resume", {"project_id": pid}),
            ("project_stop", {"project_id": pid}),
            (
                "project_set_recovery",
                {"project_id": pid, "recovery": RecoveryAction.PAUSE},
            ),
        ):
            result = await mgr.handle_command(command, payload)
            assert result["error"] == "resource_archived", command

        stale = await mgr.handle_command(
            "project_restore",
            {"project_id": pid, "expected_version": project["version"]},
        )
        assert "expected version" in stale["error"]
        restored_result = await mgr.handle_command(
            "project_restore",
            {"project_id": pid, "expected_version": archived["version"]},
        )
        assert restored_result["success"], restored_result.get("error")
        restored = restored_result["data"]["project"]
        assert restored["archived_at"] is None
        assert restored["status"] == ProjectStatus.STOPPED.value
        assert resource.restored == [pid]
        assert events[-1] == ("project_restored", {"project": restored})

    async def test_delete_hard_purges_root_but_not_workspace(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        resource = _FakeScopedResource()
        room_store = _FakeRoomStore(room_count=2)
        mgr = _make_manager(
            store,
            events=events,
            default_locator=_default_locator(tmp_path),
            scoped_resource=resource,
            room_store=room_store,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        root = ProjectPaths.from_locator(project["project_root_locator"]).root
        workspace_file = tmp_path / "default" / "keep.txt"
        workspace_file.parent.mkdir(parents=True, exist_ok=True)
        workspace_file.write_text("keep", encoding="utf-8")

        result = await mgr.handle_command(
            "project_delete",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
                "cascade_rooms": True,
            },
        )

        assert result["success"], result.get("error")
        assert result["data"]["storage_purged"] is True
        assert result["data"]["rooms_deleted"] == 2
        assert not root.exists()
        assert workspace_file.read_text(encoding="utf-8") == "keep"
        assert await store.get(project["project_id"], include_archived=True) is None
        assert resource.closed == [project["project_id"]]
        assert resource.evicted == [project["project_id"]]
        assert events[-1][0] == "project_deleted"

    async def test_archive_shutdown_failure_does_not_freeze_or_mark_catalog(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        cluster = _FakeClusterTransport()
        cluster.fail_shutdown = True
        resource = _FakeScopedResource()
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            cluster_transport=cluster,
            scoped_resource=resource,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]

        result = await mgr.handle_command(
            "project_archive",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
            },
        )

        assert "project_shutdown_failed" in result["error"]
        current = await store.get(project["project_id"])
        assert current is not None
        assert current.archived_at is None
        assert resource.closed == []

    async def test_delete_requires_explicit_room_cascade_with_zero_side_effects(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        cluster = _FakeClusterTransport()
        room_store = _FakeRoomStore(room_count=2)
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            cluster_transport=cluster,
            room_store=room_store,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        root = ProjectPaths.from_locator(project["project_root_locator"]).root

        result = await mgr.handle_command(
            "project_delete",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
                "cascade_rooms": False,
            },
        )

        assert result["error"] == "project_has_rooms"
        assert root.exists()
        assert await store.get(project["project_id"]) is not None
        assert cluster.shutdowns == []
        assert room_store.closed == []

    async def test_delete_cascade_guard_reads_root_without_room_unit(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        cluster = _FakeClusterTransport()
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            cluster_transport=cluster,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        paths = ProjectPaths.from_locator(project["project_root_locator"])
        rooms = RoomStore(paths.room_db_path)
        await rooms.start()
        await rooms.upsert(make_room_record(project_id=project["project_id"], name="room"))
        await rooms.stop()

        result = await mgr.handle_command(
            "project_delete",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
                "cascade_rooms": False,
            },
        )

        assert result["error"] == "project_has_rooms"
        assert paths.root.exists()
        assert await store.get(project["project_id"]) is not None
        assert cluster.shutdowns == []

    async def test_purge_rejects_marker_owner_mismatch(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]
        paths = ProjectPaths.from_locator(project["project_root_locator"])
        paths.marker_path.write_text('{"project_id":"someone-else"}\n')

        result = await mgr.handle_command(
            "project_delete",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
                "cascade_rooms": True,
            },
        )

        assert not result["success"]
        assert paths.root.exists()
        assert await store.get(project["project_id"]) is not None

    async def test_root_purge_failure_keeps_catalog_and_unfreezes_resources(
        self,
        store: ProjectStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resource = _FakeScopedResource()
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            scoped_resource=resource,
        )
        created = await self._create(mgr, tmp_path)
        project = created["data"]["project"]

        def fail_purge(self: ProjectPaths, project_id: str) -> None:
            raise OSError("disk refused purge")

        monkeypatch.setattr(ProjectPaths, "purge", fail_purge)
        result = await mgr.handle_command(
            "project_delete",
            {
                "project_id": project["project_id"],
                "expected_version": project["version"],
                "cascade_rooms": True,
            },
        )

        assert "project_root_purge_failed" in result["error"]
        assert await store.get(project["project_id"]) is not None
        assert resource.closed == [project["project_id"]]
        assert resource.restored == [project["project_id"]]

    async def test_delete_missing(self, store: ProjectStore) -> None:
        mgr = _make_manager(store)
        result = await mgr.handle_command(
            "project_delete", {"project_id": "nope", "expected_version": 1}
        )
        assert not result["success"]


class TestProjectLinkTask:
    async def _create(self, mgr: ProjectManager, tmp_path: Path) -> dict[str, Any]:
        return await mgr.handle_command(
            "project_create",
            {"name": "P1", "writable_workspaces": _workspace_input(tmp_path)},
        )

    async def test_link_unlink_task(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        created = await self._create(mgr, tmp_path)
        pid = created["data"]["project"]["project_id"]
        linked = await mgr.handle_command("project_link_task", {"project_id": pid, "task_id": "t1"})
        assert linked["success"]
        assert "t1" in linked["data"]["project"]["task_ids"]

        unlinked = await mgr.handle_command(
            "project_unlink_task", {"project_id": pid, "task_id": "t1"}
        )
        assert unlinked["success"]
        assert "t1" not in unlinked["data"]["project"]["task_ids"]

    async def test_link_task_missing_task_rejected(
        self, store: ProjectStore, tmp_path: Path
    ) -> None:
        mgr = _make_manager(
            store,
            default_locator=_default_locator(tmp_path),
            task_mgr=_FakeTaskMgr(task_exists=False),
        )
        created = await self._create(mgr, tmp_path)
        pid = created["data"]["project"]["project_id"]
        result = await mgr.handle_command("project_link_task", {"project_id": pid, "task_id": "t1"})
        assert not result["success"]
        assert "task not found" in (result["error"] or "")


class TestBootstrapMethods:
    async def test_bootstrap_default_project(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        project = await mgr.bootstrap_default_project()
        assert project.name == "default"
        assert project.cluster_ids == ["default"]
        assert project.project_root_locator.startswith("file://")
        assert ProjectPaths.from_locator(project.project_root_locator).marker_path.is_file()
        assert len(project.workspaces) == 1
        assert project.workspaces[0].default_for_agents is True
        # 持久化
        loaded = await store.get(project.project_id)
        assert loaded is not None
        assert loaded.name == "default"

    async def test_adopt_existing_agents(self, store: ProjectStore, tmp_path: Path) -> None:
        mgr = _make_manager(store, default_locator=_default_locator(tmp_path))
        project = await mgr.bootstrap_default_project()
        # 让 fake handle 返回现有 agent
        mgr._cluster_transport._handle._list = [  # type: ignore[attr-defined]
            {"name": "existing-agent"}
        ]
        specs = await mgr.adopt_existing_agents(project.project_id, "default")
        assert len(specs) == 1
        assert specs[0].name == "existing-agent"
        loaded = await store.get(project.project_id)
        assert loaded is not None
        assert any(a.name == "existing-agent" for a in loaded.agents)

    async def test_bootstrap_requires_locator(self, store: ProjectStore) -> None:
        mgr = _make_manager(store, default_locator="")
        with pytest.raises(ValueError, match="locator"):
            await mgr.bootstrap_default_project()

    async def test_handle_unknown_command(self, store: ProjectStore) -> None:
        mgr = _make_manager(store)
        result = await mgr.handle_command("project_mystery", {})
        assert not result["success"]


async def test_builtin_restore_rejects_legacy_spec_without_stable_id(store):
    from ghrah.subject.project.models import AgentSpec

    manager = _make_manager(store)
    manager._ability_source = "builtin"
    handle = _FakeHandle()
    mode, error = await manager._spawn_agent_spec(
        handle, AgentSpec(name="old-name", cluster_id="cluster", agent_id=""), "project"
    )
    assert mode is None and "stable_agent_id_required" in error
    assert not handle.spawned
