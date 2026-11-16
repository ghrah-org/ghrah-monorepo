# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project built-in Subject unit.

thin wrapper around :class:`ProjectManager`：注入依赖（ProjectStore /
WorkspaceManager / TaskManagerService / CoreClusterRegistry / ManifestStore /
DesiredStateStore），把 manager 的 ``on_event`` 回调桥到 internal event_bus，
并在每次成功变更命令后把 ProjectStore 全量投影写入 DesiredStateStore（派生缓存）。
"""

from __future__ import annotations

import logging
import shlex
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from ghrah.ability.permissions.bash_classify_frontend import classify_command_expression
from ghrah.ability.permissions.shell_allow import (
    extract_shell_class,
    irreversible_reason,
    is_builtin_shell_expression,
)
from pydantic import ValidationError

from ghrah.subject.config import SubjectConfig
from ghrah.subject.project.manager import ProjectManager
from ghrah.subject.project.models import ProjectRecord, ShellAllowEntry
from ghrah.subject.project.store import ConcurrentModificationError, ProjectStore
from ghrah.subject.recovery.desired_state import DesiredStateRecord, DesiredStateStore
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    DESIRED_STATE_STORE,
    MANIFEST_STORE,
    PROJECT_MANAGER,
    PROJECT_STORE,
    TASK_MANAGER,
    TASK_STORE,
    WORKSPACE_MANAGER,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._commands import PROJECT_COMMANDS
from ghrah.subject.units.ability_control import AbilityApiError, _authorized

__all__ = ["ProjectUnit", "build_project_shell_allow_router"]

logger = logging.getLogger(__name__)

# 不改变 ProjectStore 权威状态的命令：跳过 DesiredStateStore 全量重投影。
_READ_ONLY_COMMANDS = frozenset({"project_get", "project_list", "agent_entity_list"})


def build_project_shell_allow_router(*, config: SubjectConfig, store: ProjectStore) -> APIRouter:
    """提供项目 Shell 记忆增删查，直接复用 ProjectStore。"""
    router = APIRouter(prefix="/api/v1/projects/{project_id}/shell-allow")

    async def project_record(request: Request, project_id: str) -> ProjectRecord:
        if not config.observer_http_api_enabled:
            raise AbilityApiError("capability_unavailable", "Observer HTTP API is disabled")
        if not _authorized(request, config):
            raise AbilityApiError("access_denied", "A valid Bearer token is required")
        record = await store.get(project_id)
        if record is None:
            raise AbilityApiError("not_found", "Project not found")
        return record

    @router.get("")
    async def get_shell_allow(project_id: str, request: Request) -> dict[str, Any]:
        """读取项目记忆并附带只读内置规则说明。"""
        record = await project_record(request, project_id)
        return {
            "project_id": project_id,
            "entries": [entry.model_dump(mode="json") for entry in record.shell_allow],
            "builtins": [
                {"key": "uv:run:pytest", "label": "uv run pytest", "readonly": True},
                {
                    "key": "uv:run:ruff",
                    "label": "uv run ruff (recognized subcommands)",
                    "readonly": True,
                },
                {"key": "pnpm:script:test", "label": "pnpm test", "readonly": True},
            ],
        }

    @router.post("")
    async def post_shell_allow(
        project_id: str, request: Request, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """校验并追加一条记忆，相同 kind/key 不重复写入。"""
        record = await project_record(request, project_id)
        try:
            entry = ShellAllowEntry.model_validate({**payload, "created_at": datetime.now(UTC)})
        except ValidationError as error:
            raise AbilityApiError("invalid_request", str(error)) from error
        command = entry.key
        if entry.kind == "class":
            parts = entry.key.split(":", 2)
            prefixes = {
                "uv:run": ["uv", "run"],
                "pnpm:script": ["pnpm", "run"],
                "npm:script": ["npm", "run"],
            }
            if len(parts) != 3:
                raise AbilityApiError("invalid_request", "Invalid shell class key")
            family, name = ":".join(parts[:2]), parts[2]
            if family not in prefixes or not name:
                raise AbilityApiError("invalid_request", "Invalid shell class key")
            command = shlex.join([*prefixes[family], name])
        expression = classify_command_expression(command)
        reason = irreversible_reason(expression)
        if reason is not None:
            raise AbilityApiError("invalid_request", reason)
        if entry.kind == "class" and extract_shell_class(expression) != entry.key:
            raise AbilityApiError("invalid_request", "Unsupported shell class")
        if is_builtin_shell_expression(expression):
            raise AbilityApiError("invalid_request", "Builtin rules do not need project memory")
        for existing in record.shell_allow:
            if (existing.kind, existing.key) == (entry.kind, entry.key):
                return existing.model_dump(mode="json")
        try:
            await store.update(
                project_id,
                record.version,
                lambda current: current.model_copy(
                    update={"shell_allow": [*current.shell_allow, entry]}
                ),
            )
        except ConcurrentModificationError as error:
            raise AbilityApiError("conflict", str(error)) from error
        return entry.model_dump(mode="json")

    @router.delete("/{kind}/{key:path}", status_code=204)
    async def delete_shell_allow(
        project_id: str, kind: Literal["exact", "class"], key: str, request: Request
    ) -> Response:
        """只删除匹配 kind/key 的一条记忆。"""
        record = await project_record(request, project_id)
        entries = [entry for entry in record.shell_allow if (entry.kind, entry.key) != (kind, key)]
        if len(entries) == len(record.shell_allow):
            raise AbilityApiError("not_found", "Shell memory not found")
        try:
            await store.update(
                project_id,
                record.version,
                lambda current: current.model_copy(update={"shell_allow": entries}),
            )
        except ConcurrentModificationError as error:
            raise AbilityApiError("conflict", str(error)) from error
        return Response(status_code=204)

    return router


class ProjectUnit(SubjectUnit):
    """Owns ProjectStore + ProjectManager and project command/event routes.

    requires 中的 ``WORKSPACE_MANAGER``（具体 WorkspaceManager，非 WorkspaceService
    Protocol）经 WorkspaceUnit 提供，供 ProjectManager 的 register_workspace /
    list_records / get_record 使用。
    """

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._store: ProjectStore | None = None
        self._desired_store: DesiredStateStore | None = None
        self._task_store: Any | None = None
        self._manager: ProjectManager | None = None
        self._cluster_transport: Any | None = None
        self._meta = UnitMeta(
            name="project",
            requires=frozenset(
                {
                    WORKSPACE_MANAGER,
                    TASK_MANAGER,
                    TASK_STORE,
                    CORE_CLUSTER_REGISTRY,
                    MANIFEST_STORE,
                    DESIRED_STATE_STORE,
                }
            ),
            provides=frozenset({PROJECT_MANAGER, PROJECT_STORE}),
            routes=RouteSpec(commands=PROJECT_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> ProjectManager:
        if self._manager is None:
            raise RuntimeError("ProjectUnit has not been initialized.")
        return self._manager

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = ProjectStore(self._config.persistence.db_path)
        self._desired_store = ctx.get(DESIRED_STATE_STORE.name)
        workspace_mgr = ctx.get(WORKSPACE_MANAGER.name)
        task_mgr = ctx.get(TASK_MANAGER.name)
        self._task_store = ctx.get(TASK_STORE.name)
        cluster_transport = ctx.get(CORE_CLUSTER_REGISTRY.name)
        self._cluster_transport = cluster_transport
        manifest_store = ctx.get(MANIFEST_STORE.name)
        self._manager = ProjectManager(
            self._store,
            workspace_mgr,
            task_mgr,
            cluster_transport,
            manifest_store,
            on_event=self._emit_event,
            bootstrap_workspace_locator=self._config.project.bootstrap_workspace_locator,
            default_root_locator_template=self._config.project.default_root_locator_template,
            shell_allowed_roots=(
                *self._config.ability.filesystem_roots,
                self._config.sandbox.workspace_root,
            ),
        )
        self._manager.register_scoped_resource(self._task_store)
        ctx.provide(PROJECT_MANAGER.name, self._manager)
        ctx.provide(PROJECT_STORE.name, self._store)

    async def start(self) -> None:
        if self._store is not None:
            await self._store.start()
        if self._manager is not None:
            await self._manager.migrate_legacy_project_roots()
        if self._task_store is not None and self._store is not None:
            archived_projects = await self._store.list(archived=True)
            for project in archived_projects:
                self._task_store.register_project_root(
                    project.project_id, project.project_root_locator
                )
                await self._task_store.close_project(project.project_id)
            projects = await self._store.list()
            for project in projects:
                self._task_store.register_project_root(
                    project.project_id, project.project_root_locator
                )
                await self._task_store.migrate_project(project.project_id)

        remember = getattr(self._cluster_transport, "remember_locator", None)
        if self._store is not None and remember is not None:
            for project in await self._store.list(archived=None):
                remember(project.project_id, project.project_root_locator)

    async def stop(self) -> None:
        if self._store is not None:
            await self._store.stop()

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        result = await self.service.handle_command(command, payload)
        # 只读命令不触发 desired-state 全量重投影（写放大收敛）。
        operation = payload.get("operation")
        entity_write = (
            command == "agent_entity"
            and isinstance(operation, dict)
            and operation.get("type") in {"start", "stop", "abilities.configure"}
        )
        # 生命周期失败也可能已保存停止意图；投影始终从 ProjectStore 重建。
        if entity_write or (
            result.get("success")
            and command not in _READ_ONLY_COMMANDS
            and command != "agent_entity"
        ):
            await self._save_desired()
        return result

    async def _save_desired(self) -> None:
        """命令成功后用 ProjectStore 权威状态刷新 DesiredStateStore 派生缓存。"""
        if self._store is None or self._desired_store is None or self._ctx is None:
            return
        try:
            projects = await self._store.list()
            await self._desired_store.save(
                DesiredStateRecord(
                    subject_id=self._config.recovery.subject_id,
                    projects=projects,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("ProjectUnit: save desired-state failed")

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """ProjectManager 的 ``on_event`` 回调：保留 async 签名，体内同步 ctx.emit。"""

        ctx = self._ctx
        if ctx is None:
            return
        ctx.emit(f"event/{event_type}", payload)
