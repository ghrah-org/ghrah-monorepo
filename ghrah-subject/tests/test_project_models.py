# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime

import pytest
from ghrah.protocol.types import ProjectInfoPayload, ProjectStatus, RecoveryAction
from pydantic import ValidationError

from ghrah.subject.project.models import (
    AGENT_TRANSITIONS_NOTE,
    PROJECT_TRANSITIONS,
    AgentSpec,
    IsolationSpec,
    PathGrant,
    ProjectRecord,
    ProjectRuntimeConfig,
    RecoverySpec,
    WorkspaceMount,
    can_transition,
    make_project_record,
    normalize_status,
)

# ─── RecoverySpec ───


class TestRecoverySpec:
    def test_default_resume(self) -> None:
        assert RecoverySpec().on_restart is RecoveryAction.RESUME

    def test_explicit_pause(self) -> None:
        assert RecoverySpec(on_restart=RecoveryAction.PAUSE).on_restart is RecoveryAction.PAUSE

    def test_frozen(self) -> None:
        spec = RecoverySpec()
        with pytest.raises(ValidationError):
            spec.on_restart = RecoveryAction.DROP  # type: ignore[misc]


# ─── WorkspaceMount ───


class TestWorkspaceMount:
    def test_defaults(self) -> None:
        m = WorkspaceMount(workspace_id="ws-1")
        assert m.role is None
        assert m.default_for_agents is False

    def test_validate_single_default_none(self) -> None:
        mounts = [WorkspaceMount(workspace_id="ws-1"), WorkspaceMount(workspace_id="ws-2")]
        assert WorkspaceMount.validate_single_default(mounts) is None

    def test_validate_single_default_one(self) -> None:
        mounts = [
            WorkspaceMount(workspace_id="ws-1", default_for_agents=True),
            WorkspaceMount(workspace_id="ws-2"),
        ]
        assert WorkspaceMount.validate_single_default(mounts) is mounts[0]

    def test_validate_single_default_two_raises(self) -> None:
        mounts = [
            WorkspaceMount(workspace_id="ws-1", default_for_agents=True),
            WorkspaceMount(workspace_id="ws-2", default_for_agents=True),
        ]
        with pytest.raises(ValueError, match="default_for_agents"):
            WorkspaceMount.validate_single_default(mounts)


# ─── AgentSpec ───


class TestAgentSpec:
    def test_no_default_workspace_id(self) -> None:
        # 决策 B：AgentSpec 不含 default_workspace_id 字段
        assert not hasattr(AgentSpec, "default_workspace_id")
        agent = AgentSpec(name="a1", cluster_id="c1")
        dumped = agent.model_dump()
        assert "default_workspace_id" not in dumped

    def test_defaults(self) -> None:
        agent = AgentSpec(name="a1", cluster_id="c1")
        assert agent.manifest_ref == ""
        assert agent.abilities is None
        assert agent.path_grants == []

    def test_path_grants(self) -> None:
        agent = AgentSpec(
            name="a1",
            cluster_id="c1",
            path_grants=[PathGrant(workspace_id="ws-1", subpath="plans/")],
        )
        assert agent.path_grants[0].subpath == "plans/"
        assert agent.path_grants[0].workspace_id == "ws-1"


# ─── IsolationSpec ───


class TestIsolationSpec:
    def test_defaults(self) -> None:
        spec = IsolationSpec()
        assert spec.agent_private_dir is True
        assert spec.effect_allowlist is None
        assert spec.hitl_override is None
        assert spec.task_scope is True
        assert spec.agent_path_grants == {}


# ─── ProjectStatus / transitions ───


class TestTransitions:
    def test_status_values(self) -> None:
        # 复用 protocol 枚举
        assert ProjectStatus.ACTIVE.value == "active"
        assert ProjectStatus.PAUSED.value == "paused"
        assert ProjectStatus.STOPPED.value == "stopped"
        assert ProjectStatus.FAILED.value == "failed"

    def test_legal_transitions(self) -> None:
        assert can_transition(ProjectStatus.ACTIVE, ProjectStatus.PAUSED)
        assert can_transition(ProjectStatus.PAUSED, ProjectStatus.ACTIVE)
        assert can_transition(ProjectStatus.STOPPED, ProjectStatus.ACTIVE)
        assert can_transition(ProjectStatus.FAILED, ProjectStatus.ACTIVE)

    def test_illegal_transitions(self) -> None:
        assert not can_transition(ProjectStatus.ACTIVE, ProjectStatus.ACTIVE)
        assert not can_transition(ProjectStatus.STOPPED, ProjectStatus.PAUSED)
        assert not can_transition(ProjectStatus.PAUSED, ProjectStatus.FAILED)

    def test_normalize_status(self) -> None:
        assert normalize_status("paused") is ProjectStatus.PAUSED
        assert normalize_status(ProjectStatus.ACTIVE) is ProjectStatus.ACTIVE

    def test_transitions_table_completeness(self) -> None:
        # 所有枚举值均在表内
        assert set(PROJECT_TRANSITIONS) == set(ProjectStatus)
        assert isinstance(AGENT_TRANSITIONS_NOTE, str)


# ─── ProjectRecord ───


class TestProjectRecord:
    def test_defaults(self) -> None:
        record = ProjectRecord(project_id="p1", name="P1")
        assert record.status is ProjectStatus.ACTIVE
        assert record.version == 1
        assert record.deleted_at is None
        assert record.cluster_ids == []
        assert record.workspaces == []
        assert record.agents == []
        assert record.task_ids == []
        assert record.recovery.on_restart is RecoveryAction.RESUME
        assert isinstance(record.isolation, IsolationSpec)
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)

    def test_to_wire_shape(self) -> None:
        record = ProjectRecord(
            project_id="p1",
            name="P1",
            cluster_ids=["c1"],
            workspaces=[WorkspaceMount(workspace_id="ws-1", default_for_agents=True)],
            agents=[AgentSpec(name="a1", cluster_id="c1")],
            task_ids=["t1"],
        )
        wire = record.to_wire()
        # ProjectInfoPayload 字段全覆盖
        payload_fields = set(ProjectInfoPayload.model_fields.keys())
        assert payload_fields <= set(wire.keys())
        assert wire["project_id"] == "p1"
        assert wire["cluster_ids"] == ["c1"]
        assert wire["status"] == "active"
        assert wire["recovery"] == "resume"
        assert isinstance(wire["created_at"], str)
        assert isinstance(wire["updated_at"], str)
        assert wire["deleted_at"] is None

    def test_to_wire_recovery_pause(self) -> None:
        record = ProjectRecord(
            project_id="p1",
            name="P1",
            recovery=RecoverySpec(on_restart=RecoveryAction.PAUSE),
        )
        assert record.to_wire()["recovery"] == "pause"

    def test_recovery_coerce_from_str(self) -> None:
        record = ProjectRecord(project_id="p1", name="P1", recovery="drop")
        assert record.recovery.on_restart is RecoveryAction.DROP

    def test_config_defaults_to_none(self) -> None:
        record = ProjectRecord(project_id="p1", name="P1")
        assert record.config is None

    def test_config_model_roundtrip(self) -> None:
        from ghrah.plugin.assembly import PluginAssembly, PluginInstanceConfig

        record = ProjectRecord(
            project_id="p1",
            name="P1",
            config=ProjectRuntimeConfig(
                plugins=PluginAssembly(
                    enabled=["a"],
                    instances={"a": {"i": PluginInstanceConfig(enabled=False)}},
                )
            ),
        )
        restored = ProjectRecord.model_validate(record.model_dump())
        assert restored.config is not None
        assert restored.config.plugins.enabled == ["a"]
        assert restored.config.plugins.instances["a"]["i"].enabled is False
        assert restored == record

    def test_to_wire_excludes_config(self) -> None:
        from ghrah.plugin.assembly import PluginAssembly

        record = ProjectRecord(
            project_id="p1",
            name="P1",
            config=ProjectRuntimeConfig(plugins=PluginAssembly(enabled=["a"])),
        )
        assert "config" not in record.to_wire()

    def test_dt_coerce_from_iso(self) -> None:
        record = ProjectRecord(
            project_id="p1",
            name="P1",
            created_at="2026-07-31T10:00:00+00:00",
            updated_at="2026-07-31T11:00:00+00:00",
            deleted_at="2026-07-31T12:00:00+00:00",
        )
        assert isinstance(record.created_at, datetime)
        assert record.created_at.year == 2026
        assert record.deleted_at is not None
        assert record.deleted_at.hour == 12


# ─── make_project_record ───


class TestMakeProjectRecord:
    def test_generates_uuid_hex(self) -> None:
        record = make_project_record(name="P1")
        assert len(record.project_id) == 32
        assert all(c in "0123456789abcdef" for c in record.project_id)
        assert record.name == "P1"

    def test_defaults(self) -> None:
        record = make_project_record(name="P1")
        assert record.status is ProjectStatus.ACTIVE
        assert record.version == 1
        assert record.cluster_ids == []
        assert record.workspaces == []
        assert record.agents == []
        assert record.task_ids == []
        assert record.created_at == record.updated_at

    def test_custom_args(self) -> None:
        record = make_project_record(
            name="P1",
            manifest_ref="default",
            recovery=RecoverySpec(on_restart=RecoveryAction.DROP),
        )
        assert record.manifest_ref == "default"
        assert record.recovery.on_restart is RecoveryAction.DROP
