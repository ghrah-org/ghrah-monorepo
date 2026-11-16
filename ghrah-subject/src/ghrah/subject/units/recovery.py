# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Recovery built-in Subject unit.

持有 :class:`ReconciliationService` 并提供 ``reconcile_now`` / ``reconcile_status``
命令。reconcile 实际触发在装配层末尾（assemble_subject 显式调用），不在
unit.start；本 unit 仅装配服务 + 透传 reconcile 命令。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ghrah.subject.config import SubjectConfig
from ghrah.subject.recovery.desired_state import DesiredStateStore
from ghrah.subject.recovery.reconciler import ReconciliationService
from ghrah.subject.runtime.service_keys import (
    CORE_CLUSTER_REGISTRY,
    DESIRED_STATE_STORE,
    MANIFEST_STORE,
    PROJECT_MANAGER,
    RECONCILIATION_SERVICE,
    RECOVERY_START,
    TASK_STORE,
    WORKSPACE_MANAGER,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta
from ghrah.subject.units._commands import RECONCILE_COMMANDS

__all__ = ["DesiredStateUnit", "RecoveryUnit"]

logger = logging.getLogger(__name__)


class DesiredStateUnit(SubjectUnit):
    """Owns DesiredStateStore and provides the DESIRED_STATE_STORE service.

    无命令路由、无 requires（独立持久化层）。ProjectUnit / RecoveryUnit 均依赖之，
    故必须在两者之前 init/start（topological 排序经 provides/requires 保证）。
    """

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._store: DesiredStateStore | None = None
        self._meta = UnitMeta(
            name="desired_state",
            provides=frozenset({DESIRED_STATE_STORE}),
            routes=RouteSpec(commands=frozenset()),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> DesiredStateStore:
        if self._store is None:
            raise RuntimeError("DesiredStateUnit has not been initialized.")
        return self._store

    async def init(self, ctx: Any) -> None:
        self._store = DesiredStateStore(self._config.persistence.db_path)
        ctx.provide(DESIRED_STATE_STORE.name, self._store)

    async def start(self) -> None:
        if self._store is not None:
            await self._store.start()

    async def stop(self) -> None:
        if self._store is not None:
            await self._store.stop()

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        return {"success": False, "data": None, "error": "desired_state has no commands"}


class RecoveryUnit(SubjectUnit):
    """Owns ReconciliationService and reconcile command routes.

    requires 含 ``TASK_STORE``（具体 TaskStore，非 TaskManagerService Protocol），
    供 reconciler 的 ``reassign_project_id`` 迁移 sentinel task；``WORKSPACE_MANAGER``
    （具体 WorkspaceManager）供 reconciler 列已登记 workspace + 注册/重建 default ws。
    """

    def __init__(self, config: SubjectConfig) -> None:
        self._config = config
        self._ctx: Any | None = None
        self._service: ReconciliationService | None = None
        self._startup_task: asyncio.Task[None] | None = None
        self._meta = UnitMeta(
            name="recovery",
            requires=frozenset(
                {
                    DESIRED_STATE_STORE,
                    PROJECT_MANAGER,
                    WORKSPACE_MANAGER,
                    TASK_STORE,
                    CORE_CLUSTER_REGISTRY,
                    MANIFEST_STORE,
                }
            ),
            provides=frozenset({RECONCILIATION_SERVICE, RECOVERY_START}),
            routes=RouteSpec(commands=RECONCILE_COMMANDS),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def service(self) -> ReconciliationService:
        if self._service is None:
            raise RuntimeError("RecoveryUnit has not been initialized.")
        return self._service

    async def init(self, ctx: Any) -> None:
        self._ctx = ctx
        desired_store = ctx.get(DESIRED_STATE_STORE.name)
        project_mgr = ctx.get(PROJECT_MANAGER.name)
        workspace_mgr = ctx.get(WORKSPACE_MANAGER.name)
        task_store = ctx.get(TASK_STORE.name)
        cluster_transport = ctx.get(CORE_CLUSTER_REGISTRY.name)
        manifest_store = ctx.get(MANIFEST_STORE.name)
        self._service = ReconciliationService(
            desired_store,
            project_mgr,
            workspace_mgr,
            task_store,
            cluster_transport,
            manifest_store,
            subject_id=self._config.recovery.subject_id,
            on_event=self._emit_event,
            config=self._config.recovery,
        )
        ctx.provide(RECONCILIATION_SERVICE.name, self._service)
        ctx.provide(RECOVERY_START.name, self.start_recovery)

    def start_recovery(self) -> None:
        """全部单元与插件就绪后启动一次后台恢复。"""
        if self._startup_task is None:
            self._startup_task = asyncio.create_task(
                self._reconcile_on_start(), name="subject-reconcile-on-start"
            )

    async def _reconcile_on_start(self) -> None:
        try:
            await self.service.reconcile()
        except Exception:
            logger.exception("reconcile on start failed (non-fatal)")

    async def stop(self) -> None:
        task = self._startup_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._startup_task = None

    async def handle_command(
        self,
        command: str,
        payload: dict[str, Any],
        cmd_ctx: CommandContext,
    ) -> dict[str, Any]:
        if command == "reconcile_now":
            report = await self.service.reconcile()
            return {"success": report.success, "data": report.to_payload(), "error": None}
        if command == "reconcile_status":
            status = await self.service.reconcile_status()
            if status is None:
                return {"success": True, "data": None, "error": None}
            return {"success": True, "data": status.to_payload(), "error": None}
        return {"success": False, "data": None, "error": f"unknown command: {command}"}

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """ReconciliationService 的 ``on_event`` 回调：保留 async 签名，体内同步 ctx.emit。"""

        ctx = self._ctx
        if ctx is None:
            return
        ctx.emit(f"event/{event_type}", payload)
