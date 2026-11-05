# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ghrah.protocol.types import TaskInfoPayload, TaskPriority, TaskStatus
from pydantic import ValidationError

from ghrah.subject.task.models import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    TaskRecord,
    can_transition,
    is_terminal,
    make_task_record,
    normalize_status,
)

# ─── 辅助 ───

INFO_FIELDS = {
    "task_id",
    "project_id",
    "title",
    "description",
    "agent_name",
    "status",
    "priority",
    "parent_id",
    "dependencies",
    "result",
    "error",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "metadata",
}

PROJ = "proj-1"


def _iso(now: datetime) -> str:
    return now.isoformat()


# ─── TaskRecord ───


class TestTaskRecord:
    def test_inherits_all_parent_fields(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        dumped = record.model_dump()
        assert INFO_FIELDS <= set(dumped.keys())

    def test_defaults(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        assert record.status == TaskStatus.PENDING
        assert record.priority == TaskPriority.NORMAL
        assert record.agent_name is None
        assert record.parent_id is None
        assert record.dependencies == []
        assert record.metadata == {}
        assert record.result is None
        assert record.error is None
        assert record.description == ""

    def test_internal_extension_fields(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        assert record.version == 1
        assert record.deleted_at is None

    def test_timestamps_are_datetime_internally(self) -> None:
        now = datetime.now(UTC)
        record = TaskRecord(
            task_id="t1",
            project_id=PROJ,
            title="T",
            created_at=now,
            updated_at=now,
        )
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)
        assert record.started_at is None
        assert record.completed_at is None


class TestCoerceDt:
    def test_iso_str_input_coerced_to_datetime(self) -> None:
        record = TaskRecord(
            task_id="t1",
            project_id=PROJ,
            title="T",
            created_at="2025-07-15T10:00:00+00:00",
            updated_at="2025-07-15T10:00:00+00:00",
        )
        assert isinstance(record.created_at, datetime)
        assert record.created_at == datetime.fromisoformat("2025-07-15T10:00:00+00:00")

    def test_datetime_input_preserved(self) -> None:
        now = datetime.now(UTC)
        record = TaskRecord(
            task_id="t1", project_id=PROJ, title="T", created_at=now, updated_at=now
        )
        assert record.created_at is now

    def test_none_for_nullable_timestamps(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        assert record.started_at is None
        assert record.completed_at is None


class TestFieldSerializer:
    def test_datetime_serialized_to_iso_str(self) -> None:
        now = datetime.now(UTC)
        record = TaskRecord(
            task_id="t1", project_id=PROJ, title="T", created_at=now, updated_at=now
        )
        dumped = record.model_dump(mode="json")
        assert dumped["created_at"] == now.isoformat()
        assert isinstance(dumped["created_at"], str)

    def test_none_serialized_to_none(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        dumped = record.model_dump(mode="json")
        assert dumped["started_at"] is None
        assert dumped["completed_at"] is None

    def test_roundtrip_str_to_datetime_to_str(self) -> None:
        now = datetime.now(UTC)
        record = TaskRecord(
            task_id="t1", project_id=PROJ, title="T", created_at=now, updated_at=now
        )
        dumped = record.model_dump(mode="json")
        restored = datetime.fromisoformat(dumped["created_at"])
        assert restored == now


class TestToWire:
    def test_excludes_internal_fields(self) -> None:
        record = TaskRecord(task_id="t1", project_id=PROJ, title="T")
        wire = record.to_wire()
        assert "version" not in wire
        assert "deleted_at" not in wire

    def test_timestamps_are_iso_str(self) -> None:
        now = datetime.now(UTC)
        record = TaskRecord(
            task_id="t1", project_id=PROJ, title="T", created_at=now, updated_at=now
        )
        wire = record.to_wire()
        assert isinstance(wire["created_at"], str)
        assert isinstance(wire["updated_at"], str)

    def test_wire_compatible_with_task_info_payload(self) -> None:
        record = TaskRecord(
            task_id="t1",
            project_id=PROJ,
            title="T",
            description="d",
            agent_name="agent-1",
            dependencies=["dep"],
        )
        wire = record.to_wire()
        # 不抛错即兼容
        restored = TaskInfoPayload.model_validate(wire)
        assert restored.task_id == "t1"
        assert restored.project_id == PROJ
        assert restored.title == "T"
        assert restored.agent_name == "agent-1"
        assert restored.dependencies == ["dep"]


class TestMakeTaskRecord:
    def test_task_id_is_32_char_hex(self) -> None:
        record = make_task_record(title="T", project_id=PROJ)
        assert len(record.task_id) == 32
        # 不抛错即全 hex
        int(record.task_id, 16)

    def test_initial_state(self) -> None:
        record = make_task_record(title="T", project_id=PROJ)
        assert record.status == TaskStatus.PENDING
        assert record.version == 1
        assert record.deleted_at is None
        assert record.dependencies == []
        assert record.metadata == {}
        assert record.project_id == PROJ

    def test_created_and_updated_approx_equal(self) -> None:
        record = make_task_record(title="T", project_id=PROJ)
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)
        assert abs((record.updated_at - record.created_at).total_seconds()) < 1

    def test_dependencies_and_metadata_are_copies(self) -> None:
        deps = ["a", "b"]
        meta = {"k": "v"}
        record = make_task_record(title="T", project_id=PROJ, dependencies=deps, metadata=meta)
        deps.append("c")
        meta["k"] = "changed"
        assert record.dependencies == ["a", "b"]
        assert record.metadata == {"k": "v"}

    def test_explicit_fields_preserved(self) -> None:
        record = make_task_record(
            title="T",
            project_id=PROJ,
            description="desc",
            agent_name="agent-1",
            priority=TaskPriority.URGENT,
            parent_id="p1",
        )
        assert record.title == "T"
        assert record.description == "desc"
        assert record.agent_name == "agent-1"
        assert record.priority == TaskPriority.URGENT
        assert record.parent_id == "p1"

    def test_project_id_required(self) -> None:
        with pytest.raises(TypeError):
            make_task_record(title="T")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            TaskRecord(task_id="t1", title="T")  # type: ignore[call-arg]


# ─── 状态辅助 ───


class TestCanTransition:
    def test_pending_transitions(self) -> None:
        allowed = TRANSITIONS[TaskStatus.PENDING]
        assert allowed == frozenset(
            {
                TaskStatus.IN_PROGRESS,
                TaskStatus.BLOCKED,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELED,
            }
        )
        for target in allowed:
            assert can_transition(TaskStatus.PENDING, target) is True
        # pending -> pending 不允许
        assert can_transition(TaskStatus.PENDING, TaskStatus.PENDING) is False

    def test_in_progress_transitions(self) -> None:
        assert can_transition(TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED) is True
        assert can_transition(TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED) is True
        assert can_transition(TaskStatus.IN_PROGRESS, TaskStatus.FAILED) is True
        assert can_transition(TaskStatus.IN_PROGRESS, TaskStatus.CANCELED) is True
        # in_progress -> pending 不允许（不可回退）
        assert can_transition(TaskStatus.IN_PROGRESS, TaskStatus.PENDING) is False

    def test_blocked_transitions(self) -> None:
        assert can_transition(TaskStatus.BLOCKED, TaskStatus.PENDING) is True
        assert can_transition(TaskStatus.BLOCKED, TaskStatus.IN_PROGRESS) is True
        assert can_transition(TaskStatus.BLOCKED, TaskStatus.FAILED) is True
        assert can_transition(TaskStatus.BLOCKED, TaskStatus.CANCELED) is True

    @pytest.mark.parametrize(
        "terminal",
        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED],
    )
    def test_terminal_no_transitions(self, terminal: TaskStatus) -> None:
        assert TRANSITIONS[terminal] == frozenset()
        for target in TaskStatus:
            assert can_transition(terminal, target) is False


class TestIsTerminal:
    @pytest.mark.parametrize(
        "status",
        [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED],
    )
    def test_terminal_true(self, status: TaskStatus) -> None:
        assert is_terminal(status) is True

    @pytest.mark.parametrize(
        "status",
        [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED],
    )
    def test_nonterminal_false(self, status: TaskStatus) -> None:
        assert is_terminal(status) is False

    def test_terminal_statuses_set(self) -> None:
        assert TERMINAL_STATUSES == frozenset(
            {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
        )


class TestNormalizeStatus:
    def test_str_input(self) -> None:
        assert normalize_status("pending") == TaskStatus.PENDING
        assert normalize_status("in_progress") == TaskStatus.IN_PROGRESS

    def test_enum_input(self) -> None:
        assert normalize_status(TaskStatus.COMPLETED) == TaskStatus.COMPLETED

    def test_invalid_str_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            normalize_status("bogus")
