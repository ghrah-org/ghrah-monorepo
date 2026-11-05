# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ghrah.protocol.types import TaskInfoPayload, TaskPriority, TaskStatus
from pydantic import ConfigDict, Field, field_serializer, field_validator

__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "TaskRecord",
    "can_transition",
    "is_terminal",
    "make_task_record",
    "normalize_status",
]

TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.PENDING,
            TaskStatus.IN_PROGRESS,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
}

TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
)


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES


def normalize_status(value: str | TaskStatus) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    return TaskStatus(value)


class TaskRecord(TaskInfoPayload):
    """Subject 内部任务记录。

    继承 protocol TaskInfoPayload，保证 wire 一一对应：
    - 时间戳内部用 datetime(UTC)，序列化为 ISO str 以匹配父类 str 字段。
    - 扩展 version（乐观锁）与 deleted_at（软删）；to_wire() 输出纯
      TaskInfoPayload 形态（排除 version/deleted_at）。
    """

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # type: ignore[assignment]
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # type: ignore[assignment]
    started_at: datetime | None = None  # type: ignore[assignment]
    completed_at: datetime | None = None  # type: ignore[assignment]

    version: int = 1
    deleted_at: datetime | None = None

    @field_serializer("created_at", "updated_at", "started_at", "completed_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @field_validator("created_at", "updated_at", "started_at", "completed_at", mode="before")
    @classmethod
    def _coerce_dt(cls, value: Any) -> Any:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    def to_wire(self) -> dict[str, Any]:
        """输出 TaskInfoPayload 形态 dict（排除 version/deleted_at，时间戳为 ISO str）。"""
        return self.model_dump(
            mode="json",
            exclude={"version", "deleted_at"},
        )


def make_task_record(
    *,
    title: str,
    project_id: str,
    description: str = "",
    agent_id: str | None = None,
    agent_name: str | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
    parent_id: str | None = None,
    dependencies: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TaskRecord:
    """创建新任务：生成 task_id、设置 created_at/updated_at=now、status=PENDING。"""
    now = datetime.now(UTC)
    return TaskRecord(
        task_id=uuid4().hex,
        project_id=project_id,
        title=title,
        description=description,
        agent_id=agent_id,
        agent_name=agent_name,
        status=TaskStatus.PENDING,
        priority=priority,
        parent_id=parent_id,
        dependencies=list(dependencies) if dependencies else [],
        created_at=now,
        updated_at=now,
        metadata=dict(metadata) if metadata else {},
    )
