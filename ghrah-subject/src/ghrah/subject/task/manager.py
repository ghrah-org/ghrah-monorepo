# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ghrah.protocol.types import (
    TaskAssignPayload,
    TaskBlockPayload,
    TaskCancelPayload,
    TaskCompletePayload,
    TaskCreatePayload,
    TaskDeletePayload,
    TaskFailPayload,
    TaskIdPayload,
    TaskListPayload,
    TaskStatus,
    TaskUpdatePayload,
)
from pydantic import ValidationError

from ghrah.subject.task.graph import TaskGraphView
from ghrah.subject.task.models import (
    TaskRecord,
    can_transition,
    is_terminal,
    make_task_record,
    normalize_status,
)
from ghrah.subject.task.store import ConcurrentModificationError, TaskStore

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]

__all__ = ["TaskManager"]


def _now() -> datetime:
    return datetime.now(UTC)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(msg: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": msg}


class TaskManager:
    """Task 命令编排层：11 命令 + 状态流转 + 拓扑委托 + 事件回调注入。

    - 拓扑校验（环/可达/邻接）全部委托 ``TaskGraphView``，manager 不内联 DFS。
    - 状态校验经 ``can_transition`` 守卫。
    - 事件经 ``on_event`` 回调广播（unit 注入），payload 顶层含 agent_name（hoist）。
    - 不依赖 SubjectContext，纯 store + 回调，便于独立单测。
    """

    def __init__(self, store: TaskStore, *, on_event: OnEvent | None = None) -> None:
        self._store = store
        self._on_event = on_event

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = _HANDLERS.get(command)
        if handler is None:
            return _err(f"unknown command: {command}")
        try:
            return await handler(self, payload)
        except ValidationError as e:
            missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
            if missing:
                return _err(f"{missing[0]} required")
            return _err(f"invalid payload: {e}")

    # ─── helpers ───

    async def _graph(self) -> TaskGraphView:
        return TaskGraphView(await self._store.list_all_active())

    async def _emit(
        self,
        event_type: str,
        record: TaskRecord,
        previous_status: TaskStatus | None,
        reason: str | None,
    ) -> None:
        if self._on_event is None:
            return
        task_wire = record.to_wire()
        payload = {
            "task": task_wire,
            "previous_status": previous_status.value if previous_status else None,
            "reason": reason,
            "agent_id": task_wire.get("agent_id"),
            "agent_name": task_wire.get("agent_name"),
        }
        await self._on_event(event_type, payload)

    async def _require_task(self, task_id: str) -> TaskRecord | dict[str, Any]:
        record = await self._store.get(task_id)
        if record is None:
            return _err(f"task not found: {task_id}")
        return record

    # ─── 命令 handlers ───

    async def _handle_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskCreatePayload.model_validate(payload)
        title = p.title.strip()
        if not title:
            return _err("title required")
        if not p.project_id:
            return _err("project_id required")
        record = make_task_record(
            title=title,
            project_id=p.project_id,
            description=p.description,
            agent_id=p.agent_id,
            agent_name=p.agent_name,
            priority=p.priority,
            parent_id=p.parent_id,
            dependencies=list(p.dependencies),
            metadata=dict(p.metadata),
        )
        # 拓扑校验（新任务尚未入库，图中不含自身，自指由方法内部特判覆盖）
        if record.dependencies or record.parent_id is not None:
            graph = await self._graph()
            if graph.would_create_dependency_cycle(record.task_id, record.dependencies):
                return _err("dependency cycle detected (self-reference or loop)")
            if graph.would_create_parent_cycle(record.task_id, record.parent_id):
                return _err("parent cycle detected (self-reference or loop)")
        await self._store.upsert(record)
        await self._emit("task_created", record, None, None)
        return _ok({"task": record.to_wire()})

    async def _handle_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskUpdatePayload.model_validate(payload)
        existing = await self._store.get(p.task_id)
        if existing is None:
            return _err(f"task not found: {p.task_id}")
        previous_status = existing.status

        # 拓扑校验（用当前全图 + 待写入的假设新值）
        new_deps = p.dependencies if p.dependencies is not None else existing.dependencies
        new_parent = p.parent_id if p.parent_id is not None else existing.parent_id
        if (p.dependencies is not None or p.parent_id is not None) and (
            new_deps or new_parent is not None
        ):
            graph = await self._graph()
            if graph.would_create_dependency_cycle(p.task_id, list(new_deps)):
                return _err("dependency cycle detected (self-reference or loop)")
            if graph.would_create_parent_cycle(p.task_id, new_parent):
                return _err("parent cycle detected (self-reference or loop)")

        # 状态流转校验
        target_status: TaskStatus | None = None
        if p.status is not None:
            target_status = normalize_status(p.status)
            if not can_transition(existing.status, target_status):
                return _err(f"illegal transition: {existing.status.value} -> {target_status.value}")

        def mutator(r: TaskRecord) -> TaskRecord:
            updates: dict[str, Any] = {"updated_at": _now()}
            if p.title is not None:
                updates["title"] = p.title
            if p.description is not None:
                updates["description"] = p.description
            if p.agent_name is not None:
                updates["agent_name"] = p.agent_name
            if p.agent_id is not None:
                updates["agent_id"] = p.agent_id
            if p.priority is not None:
                updates["priority"] = p.priority
            if p.parent_id is not None:
                updates["parent_id"] = new_parent
            if p.dependencies is not None:
                updates["dependencies"] = list(new_deps)
            if p.result is not None:
                updates["result"] = p.result
            if p.error is not None:
                updates["error"] = p.error
            # metadata：整体替换优先于 patch；二者可同时给（先替换再 patch 合并）
            if p.metadata is not None:
                updates["metadata"] = dict(p.metadata)
            if p.metadata_patch is not None:
                base = updates.get("metadata", r.metadata)
                merged = {**base, **p.metadata_patch}
                updates["metadata"] = merged
            # 状态流转 + 时间戳
            if target_status is not None:
                updates["status"] = target_status
                if target_status == TaskStatus.IN_PROGRESS and r.started_at is None:
                    updates["started_at"] = _now()
                if is_terminal(target_status):
                    updates["completed_at"] = _now()
            return r.model_copy(update=updates)

        try:
            updated = await self._store.update(
                p.task_id,
                expected_version=p.expected_version,
                mutator=mutator,
            )
        except ConcurrentModificationError:
            return _err("concurrent modification")
        if updated is None:
            return _err(f"task not found: {p.task_id}")
        await self._emit("task_updated", updated, previous_status, None)
        return _ok({"task": updated.to_wire()})

    async def _handle_assign(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskAssignPayload.model_validate(payload)
        existing = await self._store.get(p.task_id)
        if existing is None:
            return _err(f"task not found: {p.task_id}")
        previous_status = existing.status

        def mutator(r: TaskRecord) -> TaskRecord:
            return r.model_copy(
                update={
                    "agent_id": p.agent_id or None,
                    "agent_name": p.agent_name or None,
                    "updated_at": _now(),
                }
            )

        updated = await self._store.update(p.task_id, expected_version=None, mutator=mutator)
        if updated is None:
            return _err(f"task not found: {p.task_id}")
        await self._emit("task_assigned", updated, previous_status, None)
        return _ok({"task": updated.to_wire()})

    async def _handle_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskIdPayload.model_validate(payload)
        existing = await self._store.get(p.task_id)
        if existing is None:
            return _err(f"task not found: {p.task_id}")
        previous_status = existing.status
        if not can_transition(existing.status, TaskStatus.IN_PROGRESS):
            return _err(f"illegal transition: {existing.status.value} -> in_progress")
        # 前驱状态校验（委托 graph 给前驱集合，manager 查状态）
        graph = await self._graph()
        preds = graph.predecessors(p.task_id)
        incomplete: list[str] = []
        for pred_id in preds:
            pred = await self._store.get(pred_id)
            if pred is None or pred.status != TaskStatus.COMPLETED:
                incomplete.append(pred_id)
        if incomplete:
            return _err(f"dependencies not completed: {incomplete}")

        def mutator(r: TaskRecord) -> TaskRecord:
            updates: dict[str, Any] = {
                "status": TaskStatus.IN_PROGRESS,
                "updated_at": _now(),
            }
            if r.started_at is None:
                updates["started_at"] = _now()
            return r.model_copy(update=updates)

        updated = await self._store.update(p.task_id, expected_version=None, mutator=mutator)
        if updated is None:
            return _err(f"task not found: {p.task_id}")
        await self._emit("task_started", updated, previous_status, None)
        return _ok({"task": updated.to_wire()})

    async def _handle_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskCompletePayload.model_validate(payload)
        return await self._transition_terminal(
            p.task_id,
            TaskStatus.COMPLETED,
            event_type="task_completed",
            extra_updates={"result": p.result},
        )

    async def _handle_fail(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskFailPayload.model_validate(payload)
        return await self._transition_terminal(
            p.task_id,
            TaskStatus.FAILED,
            event_type="task_failed",
            extra_updates={"error": p.error},
        )

    async def _handle_cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskCancelPayload.model_validate(payload)
        reason = p.reason if p.reason is not None else ""
        return await self._transition_terminal(
            p.task_id,
            TaskStatus.CANCELED,
            event_type="task_canceled",
            extra_updates={"error": reason},
            reason=reason or None,
        )

    async def _handle_block(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskBlockPayload.model_validate(payload)
        reason = p.reason if p.reason is not None else ""
        return await self._transition(
            p.task_id,
            TaskStatus.BLOCKED,
            event_type="task_blocked",
            extra_updates={"error": reason} if reason else {},
            reason=reason or None,
        )

    async def _handle_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskListPayload.model_validate(payload)
        # status：list → Python 层过滤；单值 → 传 store；None → 全量
        status_list: set[str] | None = None
        single_status: str | None = None
        if p.status is not None:
            if isinstance(p.status, list):
                status_list = {normalize_status(s).value for s in p.status}
            else:
                single_status = normalize_status(p.status).value

        records = await self._store.list(
            agent_id=p.agent_id,
            agent_name=p.agent_name,
            status=single_status,
            parent_id=p.parent_id,
            project_id=p.project_id,
            include_terminal=p.include_terminal,
            limit=p.limit,
        )
        if status_list is not None:
            records = [r for r in records if r.status.value in status_list]
        tasks = [r.to_wire() for r in records]
        return _ok({"tasks": tasks, "count": len(tasks)})

    async def _handle_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskIdPayload.model_validate(payload)
        record = await self._store.get(p.task_id)
        if record is None:
            return _err(f"task not found: {p.task_id}")
        return _ok({"task": record.to_wire()})

    async def _handle_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = TaskDeletePayload.model_validate(payload)
        existing = await self._store.get(p.task_id)
        if existing is None:
            return _err(f"task not found: {p.task_id}")
        previous_status = existing.status
        if not p.force:
            dependents = await self._store.count_dependents(p.task_id)
            children = await self._store.count_children(p.task_id)
            if dependents > 0 or children > 0:
                return _err(f"task has dependents ({dependents}) or children ({children})")
        await self._store.soft_delete(p.task_id)
        await self._emit("task_deleted", existing, previous_status, None)
        return _ok({"task_id": p.task_id})

    # ─── 通用流转 helper ───

    async def _transition(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        event_type: str,
        extra_updates: dict[str, Any],
        reason: str | None = None,
    ) -> dict[str, Any]:
        existing = await self._store.get(task_id)
        if existing is None:
            return _err(f"task not found: {task_id}")
        previous_status = existing.status
        if not can_transition(existing.status, target):
            return _err(f"illegal transition: {existing.status.value} -> {target.value}")

        def mutator(r: TaskRecord) -> TaskRecord:
            updates: dict[str, Any] = {
                "status": target,
                "updated_at": _now(),
                **extra_updates,
            }
            if is_terminal(target):
                updates["completed_at"] = _now()
            return r.model_copy(update=updates)

        updated = await self._store.update(task_id, expected_version=None, mutator=mutator)
        if updated is None:
            return _err(f"task not found: {task_id}")
        await self._emit(event_type, updated, previous_status, reason)
        return _ok({"task": updated.to_wire()})

    async def _transition_terminal(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        event_type: str,
        extra_updates: dict[str, Any],
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self._transition(
            task_id,
            target,
            event_type=event_type,
            extra_updates=extra_updates,
            reason=reason,
        )


_HANDLERS: dict[str, Callable[[TaskManager, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "task_create": TaskManager._handle_create,
    "task_update": TaskManager._handle_update,
    "task_assign": TaskManager._handle_assign,
    "task_start": TaskManager._handle_start,
    "task_complete": TaskManager._handle_complete,
    "task_fail": TaskManager._handle_fail,
    "task_cancel": TaskManager._handle_cancel,
    "task_block": TaskManager._handle_block,
    "task_list": TaskManager._handle_list,
    "task_get": TaskManager._handle_get,
    "task_delete": TaskManager._handle_delete,
}
