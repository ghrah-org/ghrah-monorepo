# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ProjectManager：Project 命令编排、生命周期锁与 Root 所有权边界。

- 透传 15 个 ``project_*`` 命令（乐观锁 + 状态流转 + workspace 挂载禁嵌套 +
  path_grants 不重叠 + 实例 manifest 协调）。
- ``bootstrap_default_project`` / ``adopt_existing_agents``：reconcile
  首启 bootstrap 专用，经 CoreClusterRegistry + WorkspaceManager
  建 default cluster + default workspace + 从 Core list_agents 归入现有 agent。

依赖经构造注入：``cluster_registry`` 为 ``CoreClusterRegistryService``（Protocol），
``workspace_mgr`` 为具体 ``WorkspaceManager``（位于 ``ghrah.subject.sandbox.workspace``，
非 ``ghrah.subject.workspace.manager``，v1 误标）。不依赖 SubjectContext，便于独立单测。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ghrah.protocol.payloads.entity import (
    AgentEntityCreatePayload,
    AgentEntityListPayload,
    AgentEntityPayload,
)
from ghrah.protocol.types import (
    AbilityDefinitionPayload,
    AgentConfigPayload,
    ProjectAddAgentPayload,
    ProjectCreatePayload,
    ProjectDeletePayload,
    ProjectIdPayload,
    ProjectLifecyclePayload,
    ProjectLinkTaskPayload,
    ProjectListPayload,
    ProjectRemoveAgentPayload,
    ProjectSetRecoveryPayload,
    ProjectUnlinkTaskPayload,
    ProjectUpdatePayload,
    RecoveryAction,
    SpawnAgentPayload,
)
from pydantic import ValidationError

from ghrah.subject.errors import StableError
from ghrah.subject.project.entities import AgentEntityManager
from ghrah.subject.project.isolation import (
    validate_path_grants_non_overlapping,
    validate_workspace_locators_non_nested,
)
from ghrah.subject.project.models import (
    AgentSpec,
    PathGrant,
    ProjectRecord,
    ProjectStatus,
    RecoverySpec,
    WorkspaceMount,
    can_transition,
    make_project_record,
)
from ghrah.subject.project.paths import (
    ProjectPaths,
    canonical_file_locator,
    validate_project_path_boundaries,
)
from ghrah.subject.project.store import (
    ConcurrentModificationError,
    ProjectNotFoundError,
    ProjectStore,
)
from ghrah.subject.workspace.locator import locator_to_path, path_to_locator

if TYPE_CHECKING:
    from ghrah.subject.manifest_store.store import ManifestStore
    from ghrah.subject.runtime.service_keys import (
        CoreClusterRegistryService as ClusterRegistryProtocol,
    )
    from ghrah.subject.runtime.service_keys import (
        TaskManagerService,
    )
    from ghrah.subject.sandbox.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

# server 装配层（units/websocket_observer_endpoint.py 的 _EngineDispatchAdapter）
# 会把 request_id/session_id 信封字段注入 payload 副本；ProjectCreatePayload /
# ProjectUpdatePayload 为 extra="forbid" strict 模型，验证前需剥离。
_ENVELOPE_KEYS = frozenset({"request_id", "session_id"})
_LOCKED_PROJECT_MUTATIONS = frozenset(
    {
        "project_update",
        "project_add_agent",
        "project_remove_agent",
        "project_link_task",
        "project_unlink_task",
        "project_set_recovery",
        "project_pause",
        "project_resume",
        "project_stop",
    }
)

# terminate 回执中的"运行侧已无该 agent"错误码（remove 语义 = 已终止，继续删定义）。
_TERMINATE_ABSENT_ERRORS = frozenset({"agent_identity_mismatch", "agent_not_found"})


def _strip_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    if _ENVELOPE_KEYS.isdisjoint(payload):
        return payload
    return {k: v for k, v in payload.items() if k not in _ENVELOPE_KEYS}


__all__ = ["ProjectManager"]

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


def _err_code(code: str, detail: str = "") -> dict[str, Any]:
    """稳定码 + 细节的双字段失败回执。"""
    return {
        "success": False,
        "data": None,
        "error": code,
        "error_detail": detail,
    }


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(msg: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": msg}


def _normalize_locator(locator: str) -> str:
    """归一化 locator：无 scheme（裸路径）则转为 file:// URI。"""
    if "://" in locator:
        return locator
    return path_to_locator(locator)


def _locator_dirname(locator: str) -> str:
    """bootstrap workspace locator → 本地目录路径（makedirs 用）。"""
    return locator_to_path(locator)


class ProjectManager:
    """Project 命令编排层 + per-project lifecycle lock + bootstrap 方法。

    Args:
        store: ProjectStore（持久化 + 乐观锁）。
        workspace_mgr: WorkspaceManager（register/list_records/get_record）。
        task_mgr: TaskManagerService（经 task_get 命令校验 task 属本 project）。
        cluster_registry: CoreClusterRegistryService（ensure_cluster + get_handle）。
        manifest_store: ManifestStore（实例 manifest 渲染协调，MVP 跳过）。
        on_event: 事件回调（unit 注入）。
        bootstrap_workspace_locator: 首启 bootstrap 用的 workspace locator
            （派生自 config.project.bootstrap_workspace_locator）。
        default_root_locator_template: 未显式传 Root 时使用的模板，支持
            ``{project_id}`` 占位。
    """

    def __init__(
        self,
        store: ProjectStore,
        workspace_mgr: WorkspaceManager,
        task_mgr: TaskManagerService,
        cluster_registry: ClusterRegistryProtocol,
        manifest_store: ManifestStore,
        *,
        on_event: OnEvent | None = None,
        bootstrap_workspace_locator: str = "",
        default_root_locator_template: str = "~/.ghrah/projects/{project_id}",
    ) -> None:
        self._store = store
        self._workspace_mgr = workspace_mgr
        self._task_mgr = task_mgr
        self._cluster_transport = cluster_registry
        self._manifest_store = manifest_store
        self._on_event = on_event
        self._bootstrap_workspace_locator = bootstrap_workspace_locator
        self._default_root_locator_template = default_root_locator_template
        self._create_lock = asyncio.Lock()
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._scoped_resources: list[Any] = []
        self._room_store: Any | None = None
        self.agents = AgentEntityManager(self)

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = _HANDLERS.get(command)
        if handler is None:
            return _err(f"unknown command: {command}")
        try:
            project_id = str(payload.get("project_id") or "")
            if command in _LOCKED_PROJECT_MUTATIONS and project_id:
                async with self._lifecycle_lock(project_id):
                    return await handler(self, payload)
            return await handler(self, payload)
        except ValidationError as e:
            missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
            if missing:
                return _err(f"{missing[0]} required")
            return _err(f"invalid payload: {e}")
        except (ProjectNotFoundError, ConcurrentModificationError) as e:
            return _err(str(e))
        except StableError as e:
            return _err_code(e.code, e.detail)
        except ValueError as e:
            return _err(str(e))

    async def _handle_entity(self, payload: dict[str, Any]) -> dict[str, Any]:
        return _ok(
            await self.agents.execute(AgentEntityPayload.model_validate(_strip_envelope(payload)))
        )

    async def _handle_entity_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        entity = await self.agents.create(
            AgentEntityCreatePayload.model_validate(_strip_envelope(payload))
        )
        return _ok((await entity.snapshot()).model_dump(mode="json"))

    async def _handle_entity_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = AgentEntityListPayload.model_validate(_strip_envelope(payload))
        return _ok(
            {
                "items": await self.agents.list(request.project_id),
                "available_ability_types": self.agents.available_ability_types(),
                **self.agents.model_catalog(),
            }
        )

    # ─── helpers ───

    async def _emit(self, event_type: str, record: ProjectRecord) -> None:
        if self._on_event is None:
            return
        await self._on_event(event_type, {"project": record.to_wire()})

    async def _emit_agent(self, event_type: str, record: ProjectRecord, agent: AgentSpec) -> None:
        if self._on_event is None:
            return
        await self._on_event(
            event_type,
            {
                "project": record.to_wire(),
                "project_id": record.project_id,
                "agent_name": agent.name,
                "agent_id": agent.agent_id,
                "cluster_id": agent.cluster_id,
            },
        )

    def register_scoped_resource(self, resource: Any, *, room_store: bool = False) -> None:
        """Register a lazily mounted per-Project store/cache lifecycle participant."""

        if resource not in self._scoped_resources:
            self._scoped_resources.append(resource)
        if room_store:
            self._room_store = resource

    def _lifecycle_lock(self, project_id: str) -> asyncio.Lock:
        lock = self._lifecycle_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_locks[project_id] = lock
        return lock

    async def _get_active_project(
        self, project_id: str
    ) -> tuple[ProjectRecord | None, dict[str, Any] | None]:
        record = await self._store.get(project_id, include_archived=True)
        if record is None:
            return None, _err(f"project not found: {project_id}")
        if record.archived_at is not None:
            return record, _err("resource_archived")
        return record, None

    @staticmethod
    def _check_expected_version(
        record: ProjectRecord, expected_version: int
    ) -> dict[str, Any] | None:
        if record.version == expected_version:
            return None
        return _err(
            f"Project {record.project_id} modified: expected version "
            f"{expected_version}, got {record.version}"
        )

    async def _shutdown_project_clusters(self, record: ProjectRecord) -> str | None:
        failures: list[str] = []
        for cluster_id in record.cluster_ids:
            try:
                if self._cluster_transport.has_cluster(cluster_id):
                    await self._cluster_transport.shutdown_cluster(cluster_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Project lifecycle: shutdown failed project=%s cluster=%s",
                    record.project_id,
                    cluster_id,
                )
                failures.append(f"{cluster_id}: {exc}")
        return "; ".join(failures) or None

    async def _close_project_resources(self, project_id: str) -> str | None:
        failures: list[str] = []
        for resource in self._scoped_resources:
            close = getattr(resource, "close_project", None)
            if close is None:
                continue
            try:
                await close(project_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Project lifecycle: resource close failed project=%s resource=%r",
                    project_id,
                    resource,
                )
                failures.append(f"{type(resource).__name__}: {exc}")
        return "; ".join(failures) or None

    async def _restore_project_resources(self, project_id: str) -> None:
        for resource in self._scoped_resources:
            restore = getattr(resource, "restore_project", None)
            if restore is not None:
                await restore(project_id)

    async def _evict_project_resources(self, project_id: str) -> None:
        for resource in self._scoped_resources:
            evict = getattr(resource, "evict_project", None)
            if evict is not None:
                await evict(project_id)

    async def _count_project_rooms(self, record: ProjectRecord) -> int:
        if self._room_store is None:
            # ProjectUnit can run without RoomUnit in reduced profiles. The
            # cascade guard must still inspect the authoritative Root DB.
            from ghrah.subject.room.store import RoomStore

            store = RoomStore(ProjectPaths.from_locator(record.project_root_locator).room_db_path)
            await store.start()
            try:
                return len(await store.list(project_id=record.project_id))
            finally:
                await store.stop()
        count = getattr(self._room_store, "count_project_records", None)
        if count is None:
            records = await self._room_store.list(project_id=record.project_id)
            return len(records)
        return int(await count(record.project_id))

    def _root_locator_for(self, project_id: str, requested: str = "") -> str:
        value = requested or self._default_root_locator_template.format(project_id=project_id)
        return canonical_file_locator(value)

    def _validate_agent(self, agent: AgentSpec, project: ProjectRecord) -> None:
        """agent_id/name 在 Project 内唯一 + cluster 归属/授权校验。"""
        if agent.cluster_id not in project.cluster_ids:
            raise ValueError(f"agent {agent.name!r} cluster_id {agent.cluster_id!r} not in project")
        for existing in project.agents:
            if agent.agent_id and existing.agent_id == agent.agent_id:
                raise StableError(
                    "agent_name_exists",
                    f"agent_id {agent.agent_id!r} already exists in project",
                )
            if existing.name == agent.name:
                raise StableError(
                    "agent_name_exists",
                    f"agent {agent.name!r} already exists in project",
                )
        validate_path_grants_non_overlapping(agent, project.workspaces)

    # ─── 命令 handlers ───

    async def _handle_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._create_lock:
            return await self._handle_create_locked(payload)

    async def _handle_create_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectCreatePayload.model_validate(_strip_envelope(payload))
        if not p.name.strip():
            return _err("name required")
        workspace_inputs = list(p.writable_workspaces)

        normalized_workspaces: list[dict[str, Any]] = []
        for item in workspace_inputs:
            raw = item if isinstance(item, dict) else item.model_dump()
            raw_locator = str(raw["locator"]).strip()
            if not raw_locator:
                return _err("writable_workspaces locator required")
            locator = _normalize_locator(raw_locator)
            normalized_workspaces.append({**raw, "locator": locator})
        defaults = [w for w in normalized_workspaces if w["default_for_agents"]]
        if len(normalized_workspaces) == 1 and not defaults:
            normalized_workspaces[0]["default_for_agents"] = True
        elif len(defaults) > 1 or (len(normalized_workspaces) > 1 and not defaults):
            return _err("writable_workspaces must have exactly one default_for_agents workspace")

        record = make_project_record(
            name=p.name.strip(),
            description=p.description.strip(),
            manifest_ref=p.manifest_ref,
        )
        root_locator = self._root_locator_for(record.project_id, p.project_root_locator)
        project_paths = ProjectPaths.from_locator(root_locator)
        record = record.model_copy(update={"project_root_locator": root_locator})
        cluster_id = uuid4().hex
        await self._check_create_paths(
            root_locator, [str(w["locator"]) for w in normalized_workspaces]
        )

        receipt = None
        registered_ids: list[str] = []
        stored = False
        cluster_attempted = False
        try:
            receipt = project_paths.initialize(record.project_id)
            mounts: list[WorkspaceMount] = []
            known_ids = {r.workspace_id for r in self._workspace_mgr.list_records()}
            for normalized_item in normalized_workspaces:
                ws = await self._workspace_mgr.register_workspace(
                    str(normalized_item["locator"]),
                    name=str(normalized_item.get("name") or "default"),
                )
                if ws.record.workspace_id not in known_ids:
                    registered_ids.append(ws.record.workspace_id)
                    known_ids.add(ws.record.workspace_id)
                mounts.append(
                    WorkspaceMount(
                        workspace_id=ws.record.workspace_id,
                        role=normalized_item.get("role"),
                        default_for_agents=bool(normalized_item["default_for_agents"]),
                    )
                )
            WorkspaceMount.validate_single_default(mounts)
            record = record.model_copy(
                update={
                    "cluster_ids": [cluster_id],
                    "workspaces": mounts,
                    "recovery": RecoverySpec(on_restart=RecoveryAction(p.recovery)),
                }
            )
            await self._store.upsert(record)
            stored = True
            cluster_attempted = True
            await self._cluster_transport.ensure_cluster(
                cluster_id,
                project_id=record.project_id,
                project_root_locator=record.project_root_locator,
            )
        except Exception as exc:  # noqa: BLE001 — 创建事务在此统一补偿
            if cluster_attempted:
                try:
                    if self._cluster_transport.has_cluster(cluster_id):
                        await self._cluster_transport.shutdown_cluster(cluster_id)
                except Exception:  # noqa: BLE001
                    logger.warning("create rollback: cluster shutdown failed", exc_info=True)
            if stored:
                try:
                    await self._store.delete_uncommitted(record.project_id)
                except Exception:  # noqa: BLE001
                    logger.warning("create rollback: project delete failed", exc_info=True)
            for workspace_id in reversed(registered_ids):
                try:
                    await self._workspace_mgr.unregister_workspace(workspace_id)
                except Exception:  # noqa: BLE001
                    logger.warning("create rollback: workspace unregister failed", exc_info=True)
            if receipt is not None:
                project_paths.rollback_initialize(receipt)
            return _err(f"project creation failed: {exc}")

        try:
            await self._emit("project_created", record)
        except Exception:  # noqa: BLE001 — 资源已提交，事件失败不反向销毁
            logger.exception("project_created event delivery failed for %s", record.project_id)
        return _ok({"project": record.to_wire()})

    async def _check_create_paths(self, root_locator: str, workspace_locators: list[str]) -> None:
        records = await self._store.list(archived=None)
        existing_roots = [r.project_root_locator for r in records if r.project_root_locator]
        existing_workspaces: list[str] = []
        for project in records:
            for mount in project.workspaces:
                rec = self._workspace_mgr.get_record(mount.workspace_id)
                if rec is not None:
                    existing_workspaces.append(rec.locator)
        validate_workspace_locators_non_nested(existing_workspaces + workspace_locators)
        validate_project_path_boundaries(
            root_locator=root_locator,
            existing_root_locators=existing_roots,
            workspace_locators=existing_workspaces + workspace_locators,
        )

    async def migrate_legacy_project_roots(self) -> int:
        """为尚无 Root 的旧 Project 幂等建立内部目录并更新元数据。"""
        async with self._create_lock:
            records = await self._store.list(archived=None, include_deleted=True)
            migrated = 0
            existing_roots = [r.project_root_locator for r in records if r.project_root_locator]
            workspace_locators: list[str] = []
            for project in records:
                for mount in project.workspaces:
                    workspace = self._workspace_mgr.get_record(mount.workspace_id)
                    if workspace is not None:
                        workspace_locators.append(workspace.locator)
            for record in records:
                if record.project_root_locator:
                    continue
                root_locator = self._root_locator_for(record.project_id)
                validate_project_path_boundaries(
                    root_locator=root_locator,
                    existing_root_locators=existing_roots,
                    workspace_locators=workspace_locators,
                )
                paths = ProjectPaths.from_locator(root_locator)
                receipt = paths.initialize(record.project_id)
                try:
                    await self._store.update(
                        record.project_id,
                        record.version,
                        lambda current: current.model_copy(
                            update={"project_root_locator": root_locator}
                        ),
                    )
                except Exception:
                    paths.rollback_initialize(receipt)
                    raise
                existing_roots.append(root_locator)
                migrated += 1
            return migrated

    async def _handle_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectUpdatePayload.model_validate(_strip_envelope(payload))
        existing, error = await self._get_active_project(p.project_id)
        if error is not None:
            return error
        assert existing is not None
        expected = p.expected_version if p.expected_version is not None else existing.version

        def mutator(r: ProjectRecord) -> ProjectRecord:
            updates: dict[str, Any] = {}
            if p.name is not None:
                updates["name"] = p.name
            if p.description is not None:
                updates["description"] = p.description
            if p.manifest_ref is not None:
                updates["manifest_ref"] = p.manifest_ref
            return r.model_copy(update=updates)

        updated = await self._store.update(p.project_id, expected, mutator)
        await self._emit("project_updated", updated)
        return _ok({"project": updated.to_wire()})

    async def _handle_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectIdPayload.model_validate(payload)
        # Explicit Project get is the system/settings discovery path and must
        # resolve archived records; business commands use _get_active_project.
        record = await self._store.get(p.project_id, include_archived=True)
        if record is None:
            return _err(f"project not found: {p.project_id}")
        return _ok({"project": record.to_wire()})

    async def _handle_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectListPayload.model_validate(payload)
        records = await self._store.list(
            status=p.status,
            archived=p.archived,
            include_deleted=p.include_deleted,
        )
        return _ok(
            {
                "projects": [r.to_wire() for r in records],
                "count": len(records),
            }
        )

    async def _handle_archive(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectLifecyclePayload.model_validate(payload)
        async with self._lifecycle_lock(p.project_id):
            existing = await self._store.get(p.project_id, include_archived=True)
            if existing is None:
                return _err(f"project not found: {p.project_id}")
            if existing.archived_at is not None:
                return _ok({"project": existing.to_wire()})
            conflict = self._check_expected_version(existing, p.expected_version)
            if conflict is not None:
                return conflict
            shutdown_error = await self._shutdown_project_clusters(existing)
            if shutdown_error is not None:
                return _err_code("project_shutdown_failed", str(shutdown_error))
            close_error = await self._close_project_resources(existing.project_id)
            if close_error is not None:
                await self._restore_project_resources(existing.project_id)
                return _err_code("project_resource_close_failed", str(close_error))
            try:
                updated = await self._store.archive(existing.project_id, p.expected_version)
            except Exception:
                await self._restore_project_resources(existing.project_id)
                raise
            try:
                await self._emit("project_archived", updated)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "project_archived event delivery failed for %s", updated.project_id
                )
            return _ok({"project": updated.to_wire()})

    async def _handle_restore(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectLifecyclePayload.model_validate(payload)
        async with self._lifecycle_lock(p.project_id):
            existing = await self._store.get(p.project_id, include_archived=True)
            if existing is None:
                return _err(f"project not found: {p.project_id}")
            if existing.archived_at is None:
                return _ok({"project": existing.to_wire()})
            conflict = self._check_expected_version(existing, p.expected_version)
            if conflict is not None:
                return conflict
            try:
                ProjectPaths.from_locator(existing.project_root_locator).validate_owner(
                    existing.project_id
                )
            except (OSError, ValueError) as exc:
                return _err(str(exc))
            updated = await self._store.restore(existing.project_id, p.expected_version)
            try:
                await self._restore_project_resources(existing.project_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "restore: scoped resource unfreeze failed project=%s",
                    existing.project_id,
                )
                return _err(f"project_resource_restore_failed: {exc}")
            try:
                await self._emit("project_restored", updated)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "project_restored event delivery failed for %s", updated.project_id
                )
            return _ok({"project": updated.to_wire()})

    async def _handle_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectDeletePayload.model_validate(payload)
        async with self._lifecycle_lock(p.project_id):
            existing = await self._store.get(
                p.project_id, include_archived=True, include_deleted=True
            )
            if existing is None:
                return _err(f"project not found: {p.project_id}")
            conflict = self._check_expected_version(existing, p.expected_version)
            if conflict is not None:
                return conflict
            try:
                paths = ProjectPaths.from_locator(existing.project_root_locator)
                paths.validate_owner(existing.project_id)
            except (OSError, ValueError) as exc:
                return _err(str(exc))
            try:
                room_count = await self._count_project_rooms(existing)
            except Exception as exc:  # noqa: BLE001
                return _err(f"project_room_count_failed: {exc}")
            if room_count and not p.cascade_rooms:
                return _err("project_has_rooms")
            shutdown_error = await self._shutdown_project_clusters(existing)
            if shutdown_error is not None:
                return _err_code("project_shutdown_failed", str(shutdown_error))
            close_error = await self._close_project_resources(existing.project_id)
            if close_error is not None:
                if existing.archived_at is None:
                    await self._restore_project_resources(existing.project_id)
                return _err_code("project_resource_close_failed", str(close_error))
            try:
                paths.purge(existing.project_id)
            except Exception as exc:  # noqa: BLE001
                if existing.archived_at is None:
                    await self._restore_project_resources(existing.project_id)
                return _err_code("project_root_purge_failed", str(exc))
            deleted = await self._store.hard_delete(existing.project_id, p.expected_version)
            if not deleted:
                return _err(f"project not found: {existing.project_id}")
            await self._evict_project_resources(existing.project_id)
            try:
                await self._emit("project_deleted", existing)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "project_deleted event delivery failed for %s", existing.project_id
                )
            return _ok(
                {
                    "project_id": existing.project_id,
                    "deleted": True,
                    "storage_purged": True,
                    "rooms_deleted": room_count,
                }
            )

    async def _handle_add_agent(
        self, payload: dict[str, Any], *, validate_model: bool = False
    ) -> dict[str, Any]:
        p = ProjectAddAgentPayload.model_validate(payload)
        existing, error = await self._get_active_project(p.project_id)
        if error is not None:
            return error
        assert existing is not None
        expected = p.expected_version if p.expected_version is not None else existing.version
        agent = AgentSpec(
            name=p.agent.name,
            cluster_id=p.agent.cluster_id,
            agent_id=p.agent.agent_id or uuid4().hex,
            manifest_ref=p.agent.manifest_ref,
            instance_manifest_path=(
                p.agent.instance_manifest_path
                or str(
                    ProjectPaths.from_locator(existing.project_root_locator).agent_manifest_dir
                    / f"{p.agent.name}.yaml"
                )
            ),
            agent_config_name=p.agent.agent_config_name,
            system_prompt=p.agent.system_prompt,
            abilities=p.agent.abilities,
            desired_state=p.agent.desired_state,
            runtime_status="stopped" if p.agent.desired_state == "stopped" else "pending",
            path_grants=[
                PathGrant(workspace_id=g.workspace_id, subpath=g.subpath)
                for g in p.agent.path_grants
            ],
        )
        self._validate_agent(agent, existing)

        def mutator(r: ProjectRecord) -> ProjectRecord:
            return r.model_copy(update={"agents": [*r.agents, agent]})

        updated = await self._store.update(p.project_id, expected, mutator)
        # active 且 cluster 缺该 agent 则 spawn
        runtime_error: str | None = None
        if updated.status == ProjectStatus.ACTIVE and agent.desired_state == "running":
            runtime_error = await self._ensure_agent_spawned(
                agent,
                updated.project_id,
                updated.project_root_locator,
                validate_model=validate_model,
            )
        # 运行诊断持久化（pending→running/error）：失败不改变命令结果，
        # 只影响重启后的可诊断性，best-effort。
        if updated.status == ProjectStatus.ACTIVE and agent.desired_state == "running":
            await self.mark_agent_runtime(updated.project_id, agent.agent_id, runtime_error)
        refreshed = await self._store.get(updated.project_id) or updated
        await self._emit_agent("project_agent_added", refreshed, agent)
        return _ok(
            {
                "project": refreshed.to_wire(),
                "agent_name": agent.name,
                "agent_id": agent.agent_id,
                "runtime_pending": runtime_error is not None,
                "runtime_error": runtime_error,
            }
        )

    async def mark_agent_runtime(
        self, project_id: str, agent_id: str, runtime_error: str | None
    ) -> None:
        """持久化 agent 运行诊断（running / error）。

        命令路径（lifecycle lock 内）与 reconcile 后台路径共用；不持锁，
        以最新 version 写入，冲突时重试一次，仍失败仅告警（诊断字段允许
        陈旧，不作为一致性边界）。
        """
        if not agent_id:
            return
        status = "running" if runtime_error is None else "error"

        def mutator(r: ProjectRecord) -> ProjectRecord:
            agents = [
                (
                    a.model_copy(
                        update={
                            "runtime_status": status,
                            "runtime_error": runtime_error or "",
                        }
                    )
                    if a.agent_id == agent_id
                    else a
                )
                for a in r.agents
            ]
            return r.model_copy(update={"agents": agents})

        for attempt in range(2):
            record = await self._store.get(project_id)
            if record is None:
                return
            try:
                await self._store.update(project_id, record.version, mutator)
                return
            except ConcurrentModificationError:
                if attempt:
                    logger.warning(
                        "mark_agent_runtime: version conflict persisted "
                        "project=%s agent=%s status=%s",
                        project_id,
                        agent_id,
                        status,
                    )
        return

    async def restore_agent_if_desired(
        self, project_id: str, agent_id: str, agent_name: str
    ) -> tuple[str | None, str | None]:
        """在生命周期锁内重读权威；已停止或移除的实体不会被旧快照拉起。"""
        async with self._lifecycle_lock(project_id):
            project, error = await self._get_active_project(project_id)
            if error or project is None or project.status != ProjectStatus.ACTIVE:
                return "skipped", None
            agent = next(
                (
                    a
                    for a in project.agents
                    if (agent_id and a.agent_id == agent_id)
                    or (not agent_id and not a.agent_id and a.name == agent_name)
                ),
                None,
            )
            if agent is None or agent.desired_state != "running":
                return "skipped", None
            handle = await self._cluster_transport.ensure_cluster(
                agent.cluster_id,
                project_id=project_id,
                project_root_locator=project.project_root_locator,
            )
            existing = await handle.list_agents(project_id)
            if any(
                (agent.agent_id and a.get("agent_id") == agent.agent_id)
                or (not agent.agent_id and a.get("name") == agent.name)
                for a in existing
            ):
                return "skipped", None
            mode, spawn_error = await self._spawn_agent_spec(handle, agent, project_id)
            await self.mark_agent_runtime(project_id, agent.agent_id, spawn_error)
            return mode, spawn_error

    async def _ensure_agent_spawned(
        self,
        agent: AgentSpec,
        project_id: str,
        project_root_locator: str,
        *,
        validate_model: bool = False,
    ) -> str | None:
        """启动 desired Agent；失败保留定义并返回可诊断 pending 原因。"""
        try:
            handle = await self._cluster_transport.ensure_cluster(
                agent.cluster_id,
                project_id=project_id,
                project_root_locator=project_root_locator,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("add_agent: ensure_cluster %s failed: %s", agent.cluster_id, exc)
            return str(exc)
        _, error = await self._spawn_agent_spec(
            handle, agent, project_id, validate_model=validate_model
        )
        return error

    async def _spawn_agent_spec(
        self, handle: Any, agent: AgentSpec, project_id: str, *, validate_model: bool = False
    ) -> tuple[str | None, str | None]:
        spawn_payload = SpawnAgentPayload(
            validate_model=validate_model or bool(agent.agent_config_name),
            project_id=project_id,
            cluster_id=agent.cluster_id,
            config=AgentConfigPayload(
                name=agent.name,
                agent_id=agent.agent_id,
                agent_config_name=agent.agent_config_name,
                system_prompt=agent.system_prompt or "",
            ),
            abilities=(
                [AbilityDefinitionPayload(ability_type=name, params={}) for name in agent.abilities]
                if agent.abilities
                else None
            ),
            manifest_ref=agent.manifest_ref or None,
        )
        try:
            result = await handle.spawn_agent(spawn_payload)
            if not result.get("success"):
                logger.warning("add_agent: spawn %s rejected: %s", agent.name, result.get("error"))
                return None, str(result.get("error") or "spawn rejected")
        except Exception as exc:  # noqa: BLE001
            logger.warning("add_agent: spawn %s failed: %s", agent.name, exc)
            return None, str(exc)
        return str((result.get("data") or {}).get("recovery_mode") or "spawned"), None

    async def _handle_remove_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectRemoveAgentPayload.model_validate(payload)
        existing, error = await self._get_active_project(p.project_id)
        if error is not None:
            return error
        assert existing is not None
        expected = p.expected_version if p.expected_version is not None else existing.version
        target = next(
            (
                a
                for a in existing.agents
                if (p.agent_id and a.agent_id == p.agent_id)
                or (not p.agent_id and a.name == p.agent_name)
            ),
            None,
        )
        if target is None:
            identity = p.agent_id or p.agent_name
            return _err_code("agent_not_found", str(identity))

        def mutator(r: ProjectRecord) -> ProjectRecord:
            def is_target(candidate: AgentSpec) -> bool:
                if target.agent_id:
                    return candidate.agent_id == target.agent_id
                return candidate.name == target.name and candidate.cluster_id == target.cluster_id

            return r.model_copy(update={"agents": [a for a in r.agents if not is_target(a)]})

        # 先 terminate runtime、成功后再删定义：terminate 失败保留定义并返回
        # 稳定错误码——宁可不删，不留"定义已删、runtime 仍在运行"的游离
        # Agent。cluster 未挂载（stopped/archived 后）视为无 runtime。
        # 运行侧已无该 agent（shutdown 竞态/已被终止）视为已终止，继续删除。
        handle = None
        if self._cluster_transport.has_cluster(target.cluster_id):
            try:
                candidate_handle = self._cluster_transport.get_handle(target.cluster_id)
                if candidate_handle.is_connected:
                    handle = candidate_handle
            except KeyError:
                handle = None
        if handle is not None:
            terminate_error: str | None = None
            try:
                result = await handle.terminate_agent(target.agent_id, target.name)
                if not result.get("success"):
                    error_text = str(result.get("error") or "")
                    if error_text not in _TERMINATE_ABSENT_ERRORS:
                        terminate_error = error_text or "terminate rejected"
            except Exception as exc:  # noqa: BLE001
                terminate_error = str(exc)
            if terminate_error is not None:
                logger.warning(
                    "remove_agent: terminate %s failed: %s (definition kept)",
                    p.agent_name,
                    terminate_error,
                )
                return _err_code("agent_terminate_failed", terminate_error)

        updated = await self._store.update(p.project_id, expected, mutator)
        await self._emit_agent("project_agent_removed", updated, target)
        return _ok(
            {
                "project": updated.to_wire(),
                "agent_name": target.name,
                "agent_id": target.agent_id,
            }
        )

    async def _handle_link_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._toggle_task_link(payload, link=True)

    async def _handle_unlink_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._toggle_task_link(payload, link=False)

    async def _toggle_task_link(self, payload: dict[str, Any], *, link: bool) -> dict[str, Any]:
        # 两种 payload 结构相同（project_id/task_id/expected_version）；各自 validate
        # 以校验入参，随后统一读 dict 字段避免 union 赋值类型冲突。
        if link:
            ProjectLinkTaskPayload.model_validate(payload)
        else:
            ProjectUnlinkTaskPayload.model_validate(payload)
        project_id = payload["project_id"]
        task_id = payload["task_id"]
        expected_version = payload.get("expected_version")
        existing, error = await self._get_active_project(project_id)
        if error is not None:
            return error
        assert existing is not None
        exp = expected_version if expected_version is not None else existing.version
        # 校验 task 存在（经 task_get 命令）
        task_result = await self._task_mgr.handle_command(
            "task_get", {"task_id": task_id, "project_id": project_id}
        )
        if not task_result.get("success"):
            return _err(f"task not found: {task_id}")
        task = (task_result.get("data") or {}).get("task") or {}
        if task.get("project_id") != project_id:
            return _err("task_project_mismatch")

        def mutator(r: ProjectRecord) -> ProjectRecord:
            task_ids = list(r.task_ids)
            if link:
                if task_id not in task_ids:
                    task_ids.append(task_id)
            else:
                task_ids = [t for t in task_ids if t != task_id]
            return r.model_copy(update={"task_ids": task_ids})

        updated = await self._store.update(project_id, exp, mutator)
        return _ok({"project": updated.to_wire(), "task_id": task_id})

    async def _handle_set_recovery(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = ProjectSetRecoveryPayload.model_validate(payload)
        existing, error = await self._get_active_project(p.project_id)
        if error is not None:
            return error
        assert existing is not None
        expected = p.expected_version if p.expected_version is not None else existing.version

        def mutator(r: ProjectRecord) -> ProjectRecord:
            return r.model_copy(update={"recovery": RecoverySpec(on_restart=p.recovery)})

        updated = await self._store.update(p.project_id, expected, mutator)
        await self._emit("project_recovery_set", updated)
        return _ok({"project": updated.to_wire()})

    async def _handle_pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._transition_status(
            payload,
            ProjectStatus.PAUSED,
            "project_paused",
            shutdown_clusters=True,
        )

    async def _handle_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._transition_status(
            payload, ProjectStatus.ACTIVE, "project_resumed", reinit_clusters=True
        )

    async def _handle_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._transition_status(
            payload, ProjectStatus.STOPPED, "project_stopped", shutdown_clusters=True
        )

    async def _transition_status(
        self,
        payload: dict[str, Any],
        target: ProjectStatus,
        event_type: str,
        *,
        shutdown_clusters: bool = False,
        reinit_clusters: bool = False,
    ) -> dict[str, Any]:
        p = ProjectIdPayload.model_validate(payload)
        existing, error = await self._get_active_project(p.project_id)
        if error is not None:
            return error
        assert existing is not None
        if not can_transition(existing.status, target):
            return _err(f"illegal transition: {existing.status.value} -> {target.value}")
        updated = await self._store.update(
            p.project_id, existing.version, lambda r: r.model_copy(update={"status": target})
        )
        if shutdown_clusters:
            for cluster_id in existing.cluster_ids:
                try:
                    if self._cluster_transport.has_cluster(cluster_id):
                        await self._cluster_transport.shutdown_cluster(cluster_id)
                except Exception:  # noqa: BLE001
                    logger.warning("status: shutdown cluster %s failed", cluster_id)
        if reinit_clusters:
            for cluster_id in existing.cluster_ids:
                try:
                    await self._cluster_transport.ensure_cluster(
                        cluster_id,
                        project_id=existing.project_id,
                        project_root_locator=existing.project_root_locator,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("status: reinit cluster %s failed: %s", cluster_id, exc)
        await self._emit(event_type, updated)
        return _ok({"project": updated.to_wire()})

    # ─── bootstrap 高层方法（供 reconcile 首启） ───

    async def bootstrap_default_project(self) -> ProjectRecord:
        async with self._create_lock:
            return await self._bootstrap_default_project_locked()

    async def _bootstrap_default_project_locked(self) -> ProjectRecord:
        """建 default project（1 default cluster + 1 default workspace + init_cluster）。"""
        locator = self._bootstrap_workspace_locator
        if not locator:
            raise ValueError("bootstrap_workspace_locator not configured")
        locator = _normalize_locator(locator)
        record = make_project_record(name="default")
        root_locator = self._root_locator_for(record.project_id)
        project_paths = ProjectPaths.from_locator(root_locator)
        record = record.model_copy(update={"project_root_locator": root_locator})
        cluster_id = "default"
        await self._check_create_paths(root_locator, [locator])
        receipt = None
        registered_id: str | None = None
        stored = False
        cluster_attempted = False
        try:
            receipt = project_paths.initialize(record.project_id)
            # bootstrap workspace 指向 ghrah 自有的 workspace_root/projects/default，
            # 显式创建合法（register_workspace 挂载语义仅登记已有目录，不 mkdir）。
            os.makedirs(_locator_dirname(locator), exist_ok=True)
            known_ids = {r.workspace_id for r in self._workspace_mgr.list_records()}
            ws = await self._workspace_mgr.register_workspace(locator, name="default")
            if ws.record.workspace_id not in known_ids:
                registered_id = ws.record.workspace_id
            mount = WorkspaceMount(
                workspace_id=ws.record.workspace_id,
                role="default",
                default_for_agents=True,
            )
            record = record.model_copy(update={"cluster_ids": [cluster_id], "workspaces": [mount]})
            await self._store.upsert(record)
            stored = True
            cluster_attempted = True
            await self._cluster_transport.ensure_cluster(
                cluster_id,
                project_id=record.project_id,
                project_root_locator=record.project_root_locator,
            )
        except Exception:
            if cluster_attempted:
                try:
                    if self._cluster_transport.has_cluster(cluster_id):
                        await self._cluster_transport.shutdown_cluster(cluster_id)
                except Exception:  # noqa: BLE001
                    logger.warning("bootstrap rollback: cluster shutdown failed", exc_info=True)
            if stored:
                try:
                    await self._store.delete_uncommitted(record.project_id)
                except Exception:  # noqa: BLE001
                    logger.warning("bootstrap rollback: project delete failed", exc_info=True)
            if registered_id is not None:
                try:
                    await self._workspace_mgr.unregister_workspace(registered_id)
                except Exception:  # noqa: BLE001
                    logger.warning("bootstrap rollback: workspace unregister failed", exc_info=True)
            if receipt is not None:
                project_paths.rollback_initialize(receipt)
            raise
        try:
            await self._emit("project_created", record)
        except Exception:  # noqa: BLE001
            logger.exception("bootstrap project_created event delivery failed")
        return record

    async def adopt_existing_agents(self, project_id: str, cluster_id: str) -> list[AgentSpec]:
        """经 ClusterHandle.list_agents 从 Core 取现有 agent，构造 AgentSpec 并加入 project。"""
        existing, error = await self._get_active_project(project_id)
        if error is not None:
            raise ValueError(str(error["error"]))
        assert existing is not None
        handle = await self._cluster_transport.ensure_cluster(
            cluster_id,
            project_id=existing.project_id,
            project_root_locator=existing.project_root_locator,
        )
        agents_info: list[dict[str, Any]] = []
        try:
            agents_info = await handle.list_agents(existing.project_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("adopt_existing_agents: list_agents failed: %s", exc)
            return []
        specs: list[AgentSpec] = []
        for info in agents_info:
            name = info.get("name")
            if not isinstance(name, str) or not name:
                continue
            spec = AgentSpec(
                name=name,
                cluster_id=cluster_id,
                agent_id=(str(info.get("agent_id")) if info.get("agent_id") else uuid4().hex),
                manifest_ref="",
                instance_manifest_path="",
            )
            specs.append(spec)

        def mutator(r: ProjectRecord) -> ProjectRecord:
            # Project 内 name 唯一；跨 Project 可复用同名。
            existing_names = {a.name for a in r.agents}
            new_agents = [s for s in specs if s.name not in existing_names]
            return r.model_copy(update={"agents": [*r.agents, *new_agents]})

        await self._store.update(project_id, existing.version, mutator)
        if self._on_event is not None and specs:
            updated = await self._store.get(project_id)
            if updated is not None:
                await self._emit("project_updated", updated)
        return specs


_HANDLERS: dict[str, Callable[[ProjectManager, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "project_create": ProjectManager._handle_create,
    "project_update": ProjectManager._handle_update,
    "project_get": ProjectManager._handle_get,
    "project_list": ProjectManager._handle_list,
    "project_archive": ProjectManager._handle_archive,
    "project_restore": ProjectManager._handle_restore,
    "project_delete": ProjectManager._handle_delete,
    "project_add_agent": ProjectManager._handle_add_agent,
    "agent_entity": ProjectManager._handle_entity,
    "agent_entity_create": ProjectManager._handle_entity_create,
    "agent_entity_list": ProjectManager._handle_entity_list,
    "project_remove_agent": ProjectManager._handle_remove_agent,
    "project_link_task": ProjectManager._handle_link_task,
    "project_unlink_task": ProjectManager._handle_unlink_task,
    "project_set_recovery": ProjectManager._handle_set_recovery,
    "project_pause": ProjectManager._handle_pause,
    "project_resume": ProjectManager._handle_resume,
    "project_stop": ProjectManager._handle_stop,
}
