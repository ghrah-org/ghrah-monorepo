from __future__ import annotations

from typing import Any

from ghrah.protocol.types import ProjectStatus, RecoveryAction

from ghrah.subject.config import RecoveryConfig
from ghrah.subject.project.models import (
    AgentSpec,
    ProjectRecord,
    RecoverySpec,
    WorkspaceMount,
    make_project_record,
)
from ghrah.subject.recovery.desired_state import DesiredStateRecord, DesiredStateStore
from ghrah.subject.recovery.reconciler import ReconcileReport, ReconciliationService
from ghrah.subject.workspace.models import WorkspaceRecord

# ─── fakes ───


class FakeProjectMgr:
    def __init__(
        self,
        projects: list[ProjectRecord] | None = None,
        bootstrap_project: ProjectRecord | None = None,
        agents: list[AgentSpec] | None = None,
    ) -> None:
        self._projects = projects or []
        self._bootstrap_project = bootstrap_project
        self._agents = agents or []
        self.bootstrap_called = False
        self.adopt_called = False
        self.runtime_marks: list[tuple[str, str, str | None]] = []

    async def restore_agent_if_desired(self, project_id: str, agent_id: str, agent_name: str):
        project = next(p for p in self._projects if p.project_id == project_id)
        agent = next(a for a in project.agents if a.name == agent_name)
        if agent.desired_state == "stopped":
            return "skipped", None
        from ghrah.protocol.types import AgentConfigPayload, SpawnAgentPayload

        result = await self.transport._handle.spawn_agent(
            SpawnAgentPayload(
                project_id=project_id,
                cluster_id=agent.cluster_id,
                config=AgentConfigPayload(name=agent.name, agent_id=agent.agent_id),
            )
        )
        return ("spawned", None) if result["success"] else (None, result["error"])

    async def mark_agent_runtime(
        self, project_id: str, agent_id: str, runtime_error: str | None
    ) -> None:
        self.runtime_marks.append((project_id, agent_id, runtime_error))

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "project_list":
            return {
                "success": True,
                "data": {
                    "projects": [p.to_wire() for p in self._projects],
                    "count": len(self._projects),
                },
            }
        return {"success": False, "data": None, "error": "unsupported"}

    async def bootstrap_default_project(self) -> ProjectRecord:
        self.bootstrap_called = True
        if self._bootstrap_project is None:
            raise RuntimeError("no bootstrap project configured")
        self._projects = [self._bootstrap_project]
        return self._bootstrap_project

    async def adopt_existing_agents(self, project_id: str, cluster_id: str) -> list[AgentSpec]:
        self.adopt_called = True
        return self._agents


class FakeClusterHandle:
    def __init__(self, existing_agents: list[str] | None = None) -> None:
        self._existing = existing_agents or []
        self.spawned: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def spawn_agent(self, payload: Any) -> dict[str, Any]:
        self.spawned.append(payload.config.name)
        return {"success": True, "data": {"name": payload.config.name}, "error": None}

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [{"name": n} for n in self._existing]

    async def terminate_agent(self, agent_id: str, agent_name: str) -> dict[str, Any]:
        return {"success": True, "data": None, "error": None}

    async def shutdown(self) -> None:
        pass


class FakeClusterTransport:
    def __init__(self, handle: FakeClusterHandle | None = None) -> None:
        self._handle = handle or FakeClusterHandle()
        self.ensure_calls: list[str] = []

    async def ensure_cluster(
        self,
        cluster_id: str,
        *,
        project_id: str,
        project_root_locator: str,
    ) -> FakeClusterHandle:
        self.ensure_calls.append(cluster_id)
        return self._handle

    def get_handle(self, cluster_id: str) -> FakeClusterHandle:
        return self._handle

    def has_cluster(self, cluster_id: str) -> bool:
        return True

    async def shutdown_cluster(self, cluster_id: str) -> None:
        pass

    async def stop(self) -> None:
        pass


class FakeWorkspaceMgr:
    def __init__(self, records: list[WorkspaceRecord] | None = None) -> None:
        self._records = records or []

    def list_records(self) -> list[WorkspaceRecord]:
        return list(self._records)

    def get_record(self, workspace_id: str) -> WorkspaceRecord | None:
        for r in self._records:
            if r.workspace_id == workspace_id:
                return r
        return None

    async def register_workspace(
        self, locator: str, *, name: str = "", provider_type: str | None = None
    ) -> Any:
        raise NotImplementedError


class FakeTaskStore:
    def __init__(self) -> None:
        self.reassign_calls: list[tuple[str, str]] = []

    async def reassign_project_id(self, old_id: str, new_id: str, **kwargs: Any) -> int:
        self.reassign_calls.append((old_id, new_id))
        return 3


class FakeManifestStore:
    pass


def _make_service(
    *,
    desired_record: DesiredStateRecord | None = None,
    projects: list[ProjectRecord] | None = None,
    bootstrap_project: ProjectRecord | None = None,
    agents: list[AgentSpec] | None = None,
    existing_agents: list[str] | None = None,
    workspace_records: list[WorkspaceRecord] | None = None,
    config: RecoveryConfig | None = None,
) -> tuple[
    ReconciliationService,
    tuple[DesiredStateStore, FakeProjectMgr, FakeClusterTransport, FakeTaskStore],
]:
    desired_store = _FakeDesiredStore(desired_record)
    authoritative_projects = projects
    if authoritative_projects is None and desired_record is not None:
        authoritative_projects = desired_record.projects
    project_mgr = FakeProjectMgr(
        projects=authoritative_projects, bootstrap_project=bootstrap_project, agents=agents
    )
    cluster_transport = FakeClusterTransport(FakeClusterHandle(existing_agents))
    project_mgr.transport = cluster_transport
    workspace_mgr = FakeWorkspaceMgr(workspace_records)
    task_store = FakeTaskStore()
    svc = ReconciliationService(
        desired_store,
        project_mgr,
        workspace_mgr,
        task_store,
        cluster_transport,
        FakeManifestStore(),
        subject_id="default",
        on_event=None,
        config=config or RecoveryConfig(),
    )
    return svc, (desired_store, project_mgr, cluster_transport, task_store)


_WS1 = WorkspaceRecord(
    workspace_id="ws1",
    name="n",
    provider_type="git",
    subject_id="default",
    locator="file:///workspaces/ws1",
)


class _FakeDesiredStore:
    def __init__(self, record: DesiredStateRecord | None) -> None:
        self._record = record

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def load(self, subject_id: str) -> DesiredStateRecord | None:
        return self._record

    async def save(self, record: DesiredStateRecord) -> None:
        self._record = record

    async def clear(self, subject_id: str) -> None:
        self._record = None


def _desired_project(
    *,
    cluster_id: str = "c1",
    workspace_id: str = "ws1",
    agents: list[AgentSpec] | None = None,
    recovery: RecoveryAction = RecoveryAction.RESUME,
) -> ProjectRecord:
    record = make_project_record(name="P1")
    return record.model_copy(
        update={
            "cluster_ids": [cluster_id],
            "workspaces": [WorkspaceMount(workspace_id=workspace_id, default_for_agents=True)],
            "agents": agents or [],
            "recovery": RecoverySpec(on_restart=recovery),
        }
    )


class TestReconcile:
    async def test_bootstrap_first_start(self) -> None:
        # desired 空 + 无已登记 workspace → bootstrap
        svc, (desired_store, project_mgr, cluster_transport, task_store) = _make_service(
            desired_record=None,
            bootstrap_project=_desired_project(),
            agents=[AgentSpec(name="a1", cluster_id="default")],
        )
        report = await svc.reconcile()
        assert report.bootstrap is True
        assert report.tasks_migrated == 3
        assert project_mgr.bootstrap_called and project_mgr.adopt_called
        assert task_store.reassign_calls  # 迁移了 sentinel task
        assert report.success is True
        # 保存了 desired-state
        assert await desired_store.load("default") is not None

    async def test_bootstrap_skipped_when_workspaces_exist(self) -> None:
        ws = WorkspaceRecord(
            workspace_id="ws1",
            name="n",
            provider_type="git",
            subject_id="default",
            locator="file:///workspaces/ws1",
        )
        svc, (_, project_mgr, _, _) = _make_service(
            desired_record=None,
            bootstrap_project=_desired_project(),
            workspace_records=[ws],
        )
        report = await svc.reconcile()
        assert report.bootstrap is False
        assert not project_mgr.bootstrap_called

    async def test_reconcile_existing_project_spawns_missing_agent(self) -> None:
        project = _desired_project(
            agents=[AgentSpec(name="a1", cluster_id="c1")],
        )
        record = DesiredStateRecord(subject_id="default", projects=[project])
        svc, (_, _, cluster_transport, _) = _make_service(
            desired_record=record,
            existing_agents=[],  # Core 无 a1 → spawn
            workspace_records=[_WS1],
        )
        report = await svc.reconcile()
        assert report.agents_spawned == 1
        assert cluster_transport._handle.spawned == ["a1"]
        assert report.bootstrap is False

    async def test_project_store_overrides_stale_desired_cache(self) -> None:
        authoritative = _desired_project(
            agents=[AgentSpec(name="fresh", cluster_id="c1", agent_id="f" * 32)]
        )
        stale = _desired_project(
            agents=[AgentSpec(name="stale", cluster_id="c1", agent_id="s" * 32)]
        )
        svc, (desired_store, _, cluster_transport, _) = _make_service(
            desired_record=DesiredStateRecord(subject_id="default", projects=[stale]),
            projects=[authoritative],
            existing_agents=[],
            workspace_records=[_WS1],
        )

        report = await svc.reconcile()

        assert report.success is True
        assert cluster_transport._handle.spawned == ["fresh"]
        rebuilt = await desired_store.load("default")
        assert rebuilt is not None
        assert [a.name for a in rebuilt.projects[0].agents] == ["fresh"]

    async def test_paused_project_does_not_mount_cluster(self) -> None:
        project = _desired_project(agents=[AgentSpec(name="paused", cluster_id="c1")]).model_copy(
            update={"status": ProjectStatus.PAUSED}
        )
        svc, (_, _, cluster_transport, _) = _make_service(
            projects=[project],
            workspace_records=[_WS1],
        )

        report = await svc.reconcile()

        assert report.paused == 1
        assert cluster_transport.ensure_calls == []
        assert cluster_transport._handle.spawned == []

    async def test_reconcile_existing_agent_not_spawned(self) -> None:
        project = _desired_project(
            agents=[AgentSpec(name="a1", cluster_id="c1")],
        )
        record = DesiredStateRecord(subject_id="default", projects=[project])
        svc, (_, _, cluster_transport, _) = _make_service(
            desired_record=record,
            existing_agents=["a1"],  # Core 已有 a1 → 跳过
            workspace_records=[_WS1],
        )
        report = await svc.reconcile()
        assert report.agents_spawned == 0
        assert cluster_transport._handle.spawned == []

    async def test_reconcile_idempotent_repeated(self) -> None:
        project = _desired_project(agents=[AgentSpec(name="a1", cluster_id="c1")])
        record = DesiredStateRecord(subject_id="default", projects=[project])
        svc, (_, _, _, _) = _make_service(
            desired_record=record, existing_agents=["a1"], workspace_records=[_WS1]
        )
        r1 = await svc.reconcile()
        r2 = await svc.reconcile()
        assert r1.agents_spawned == 0 and r2.agents_spawned == 0
        assert r1.success and r2.success

    async def test_reconcile_pause_counts_paused(self) -> None:
        project = _desired_project(
            agents=[AgentSpec(name="a1", cluster_id="c1"), AgentSpec(name="a2", cluster_id="c1")],
            recovery=RecoveryAction.PAUSE,
        )
        record = DesiredStateRecord(subject_id="default", projects=[project])
        ws = WorkspaceRecord(
            workspace_id="ws1",
            name="n",
            provider_type="git",
            subject_id="default",
            locator="file:///workspaces/ws1",
        )
        svc, (_, _, cluster_transport, _) = _make_service(
            desired_record=record, workspace_records=[ws]
        )
        report = await svc.reconcile()
        assert report.paused == 2
        assert report.agents_spawned == 0
        assert cluster_transport._handle.spawned == []

    async def test_reconcile_drop_counts_dropped(self) -> None:
        project = _desired_project(
            agents=[AgentSpec(name="a1", cluster_id="c1")],
            recovery=RecoveryAction.DROP,
        )
        record = DesiredStateRecord(subject_id="default", projects=[project])
        svc, (_, _, _, _) = _make_service(desired_record=record, workspace_records=[_WS1])
        report = await svc.reconcile()
        assert report.dropped == 1

    async def test_reconcile_emits_reconciled_event(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        async def on_event(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, payload))

        project = _desired_project()
        record = DesiredStateRecord(subject_id="default", projects=[project])
        desired_store = _FakeDesiredStore(record)
        svc = ReconciliationService(
            desired_store,
            FakeProjectMgr(projects=[project]),
            FakeWorkspaceMgr(),
            FakeTaskStore(),
            FakeClusterTransport(),
            FakeManifestStore(),
            subject_id="default",
            on_event=on_event,
            config=RecoveryConfig(),
        )
        await svc.reconcile()
        assert events
        assert events[0][0] == "subject_reconciled"
        assert events[0][1]["success"] is True

    async def test_reconcile_failure_emits_reconcile_failed(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        async def on_event(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, payload))

        # 让 desired_store.start 抛错
        class _BoomStore(_FakeDesiredStore):
            async def start(self) -> None:
                raise RuntimeError("boom")

        svc = ReconciliationService(
            _BoomStore(None),
            FakeProjectMgr(),
            FakeWorkspaceMgr(),
            FakeTaskStore(),
            FakeClusterTransport(),
            FakeManifestStore(),
            subject_id="default",
            on_event=on_event,
            config=RecoveryConfig(),
        )
        report = await svc.reconcile()
        assert report.success is False
        assert events
        assert events[0][0] == "reconcile_failed"

    async def test_reconcile_status_returns_last_report(self) -> None:
        svc, _ = _make_service(desired_record=None, bootstrap_project=_desired_project())
        await svc.reconcile()
        status = await svc.reconcile_status()
        assert status is not None
        assert status.bootstrap is True

    async def test_reconcile_status_none_before_reconcile(self) -> None:
        svc, _ = _make_service(desired_record=None)
        status = await svc.reconcile_status()
        assert status is None

    async def test_reconcile_report_to_payload(self) -> None:
        report = ReconcileReport(
            subject_id="s",
            success=True,
            bootstrap=True,
            projects_reconciled=2,
            agents_spawned=3,
            workspaces_adopted=1,
            tasks_migrated=5,
            paused=1,
            dropped=2,
            errors=[],
        )
        payload = report.to_payload()
        assert payload["bootstrap"] is True
        assert payload["tasks_migrated"] == 5
        assert payload["paused"] == 1
        assert payload["dropped"] == 2
