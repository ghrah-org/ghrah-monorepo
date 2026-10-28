# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ReconciliationService：Subject 全栈 reconcile。

在装配层末尾触发（assemble_subject，所有 unit ACTIVE 后）：加载 desired-state →
对每个 desired project 校验 workspace/cluster/agent/实例 manifest 一致性 →
首次启动 bootstrap default project（建立 default cluster + default workspace + 迁移
sentinel task + 归入现有 agent）→ 发送 ``subject_reconciled``/``reconcile_failed``
事件。

依赖经构造注入，不直接 import service_keys，便于独立单测
（mock Protocol）。全程 try/except：失败仅发 ``reconcile_failed`` + log，不会崩溃退出
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ghrah.protocol.types import ProjectStatus, RecoveryAction

from ghrah.subject.config import RecoveryConfig
from ghrah.subject.recovery.desired_state import (
    DesiredStateRecord,
    DesiredStateStore,
)

if TYPE_CHECKING:
    from ghrah.subject.manifest_store.store import ManifestStore
    from ghrah.subject.project.models import AgentSpec, ProjectRecord
    from ghrah.subject.runtime.service_keys import (
        CoreClusterRegistryService as ClusterRegistryProtocol,
    )
    from ghrah.subject.runtime.service_keys import (
        ProjectManagerService,
    )
    from ghrah.subject.sandbox.workspace import WorkspaceManager
    from ghrah.subject.task.store import TaskStore

logger = logging.getLogger(__name__)

__all__ = ["ReconcileReport", "ReconciliationService"]

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class ReconcileReport:
    """reconcile 结果摘要（内部富类型，发事件时映射为 ReconcileReportPayload）。"""

    subject_id: str
    success: bool = True
    bootstrap: bool = False
    projects_reconciled: int = 0
    agents_spawned: int = 0
    agents_restored: int = 0
    agents_initialized: int = 0
    agents_failed: int = 0
    agent_results: list[dict[str, str]] = field(default_factory=list)
    workspaces_adopted: int = 0
    tasks_migrated: int = 0
    paused: int = 0
    dropped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """映射为 protocol ``ReconcileReportPayload`` 形态 dict。"""
        return {
            "subject_id": self.subject_id,
            "success": self.success,
            "projects_reconciled": self.projects_reconciled,
            "agents_spawned": self.agents_spawned,
            "agents_restored": self.agents_restored,
            "agents_initialized": self.agents_initialized,
            "agents_failed": self.agents_failed,
            "agent_results": list(self.agent_results),
            "workspaces_adopted": self.workspaces_adopted,
            "errors": list(self.errors),
            "bootstrap": self.bootstrap,
            "paused": self.paused,
            "dropped": self.dropped,
            "tasks_migrated": self.tasks_migrated,
        }


class ReconciliationService:
    """Subject 全栈 reconcile 服务。

    Args:
        desired_store: DesiredStateStore（ProjectStore 的可重建派生缓存）。
        project_mgr: ProjectManagerService（提供 bootstrap_default_project /
            adopt_existing_agents + 13 命令；reconcile 首启经其 bootstrap）。
        workspace_mgr: WorkspaceManager（列已登记 workspace + 注册/重建 default ws）。
        task_store: TaskStore（reassign_project_id 迁移 sentinel task）。
        cluster_registry: CoreClusterRegistryService（ensure_cluster + get_handle）。
        manifest_store: ManifestStore（实例 manifest 校验，MVP 仅 os.path.exists）。
        subject_id: 对账归属 subject 标识。
        on_event: 事件回调（unit 注入，转 internal event_bus）。
        config: RecoveryConfig（on_unknown_workspace / bootstrap_default_project）。
    """

    def __init__(
        self,
        desired_store: DesiredStateStore,
        project_mgr: ProjectManagerService,
        workspace_mgr: WorkspaceManager,
        task_store: TaskStore,
        cluster_registry: ClusterRegistryProtocol,
        manifest_store: ManifestStore,
        *,
        subject_id: str,
        on_event: OnEvent | None = None,
        config: RecoveryConfig,
    ) -> None:
        self._desired_store = desired_store
        self._project_mgr = project_mgr
        self._workspace_mgr = workspace_mgr
        self._task_store = task_store
        self._cluster_transport = cluster_registry
        self._manifest_store = manifest_store
        self._subject_id = subject_id
        self._on_event = on_event
        self._config = config
        self._last_report: ReconcileReport | None = None

    async def reconcile(self) -> ReconcileReport:
        """执行全栈 reconcile。幂等可重复。不 raise。"""
        report = ReconcileReport(subject_id=self._subject_id)
        try:
            await self._desired_store.start()
            projects = await self._load_projects()
            needs_bootstrap = not projects and self._config.bootstrap_default_project
            if needs_bootstrap:
                await self._bootstrap(report)
                projects = await self._load_projects()
            # ProjectStore 是权威；每次启动都由其刷新 desired cache，绝不反向覆盖。
            await self._desired_store.save(
                DesiredStateRecord(subject_id=self._subject_id, projects=projects)
            )
            for project in projects:
                await self._reconcile_project(project, report)
        except Exception as exc:  # noqa: BLE001 — 不阻断启动
            logger.exception("reconcile failed")
            report.success = False
            report.errors.append(f"reconcile error: {exc}")
        report.success = report.success and not report.errors
        await self._emit(report)
        self._last_report = report
        return report

    async def reconcile_status(self) -> ReconcileReport | None:
        """返回最近一次 reconcile 报告（供 reconcile_status 命令）。"""
        return self._last_report

    async def _load_projects(self) -> list[ProjectRecord]:
        """从 ProjectManager/ProjectStore 读取恢复权威状态。"""
        result = await self._project_mgr.handle_command("project_list", {"include_deleted": False})
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "project_list failed")
        from ghrah.subject.project.models import ProjectRecord

        raw_projects = (result.get("data") or {}).get("projects", [])
        return [ProjectRecord.model_validate(project) for project in raw_projects]

    # ─── bootstrap（首启） ───

    async def _bootstrap(self, report: ReconcileReport) -> None:
        """首启 bootstrap：建 default project + 迁移 sentinel task + 归入现有 agent。

        前置：desired-state 为空 且 无已登记 workspace（WorkspaceManager.list_records 空）。
        """
        if self._workspace_mgr.list_records():
            logger.info("reconcile: workspace(s) already registered, skip bootstrap")
            return
        logger.info("reconcile: first-start bootstrap default project")
        project = await self._project_mgr.bootstrap_default_project()
        report.bootstrap = True
        report.projects_reconciled += 1
        if project.cluster_ids:
            cluster_id = project.cluster_ids[0]
            agents = await self._project_mgr.adopt_existing_agents(project.project_id, cluster_id)
            report.agents_spawned += len(agents)
        migrated = await self._task_store.reassign_project_id(
            "", project.project_id, include_terminal=True, include_deleted=True
        )
        report.tasks_migrated = migrated
        await self._save_desired()

    async def _save_desired(self) -> None:
        """从 ProjectStore 经 project_mgr 拉全量 projects 保存 desired-state。

        ``project_mgr.handle_command("project_list", {})`` 返回 list 结果；
        为避免依赖 ProjectStore 直连，经 project_mgr 命令取。此处用 list 命令。
        """
        result = await self._project_mgr.handle_command("project_list", {"include_deleted": False})
        projects_data = result.get("data", {}).get("projects", []) if result.get("success") else []
        from ghrah.subject.project.models import ProjectRecord

        projects = [ProjectRecord.model_validate(p) for p in projects_data]
        await self._desired_store.save(
            DesiredStateRecord(subject_id=self._subject_id, projects=projects)
        )

    # ─── 单 project reconcile ───

    async def _reconcile_project(self, project: ProjectRecord, report: ReconcileReport) -> None:
        """对账单个 project：workspace / cluster / 实例 manifest / agent。"""
        report.projects_reconciled += 1
        if project.status != ProjectStatus.ACTIVE:
            report.paused += len(project.agents)
            return
        # workspace：经 WorkspaceManager 已登记记录比对（不重复扫描，复用 orphan adopt）
        registered_ids = {r.workspace_id for r in self._workspace_mgr.list_records()}
        for mount in project.workspaces:
            if mount.workspace_id in registered_ids:
                continue
            await self._handle_unknown_workspace(project, mount, report)
        # cluster：幂等 init
        for cluster_id in project.cluster_ids:
            try:
                await self._cluster_transport.ensure_cluster(
                    cluster_id,
                    project_id=project.project_id,
                    project_root_locator=project.project_root_locator,
                )
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"project {project.project_id}: ensure_cluster({cluster_id}) failed: {exc}"
                )
        # agent：spawn 缺失的（recovery=resume）
        if project.recovery.on_restart != RecoveryAction.RESUME:
            if project.recovery.on_restart == RecoveryAction.PAUSE:
                report.paused += len(project.agents)
            elif project.recovery.on_restart == RecoveryAction.DROP:
                report.dropped += len(project.agents)
            return
        await self._reconcile_agents(project, report)

    async def _handle_unknown_workspace(
        self,
        project: ProjectRecord,
        mount: Any,
        report: ReconcileReport,
    ) -> None:
        """desired 存在但 workspace 未登记：按 config.on_unknown_workspace 处理。

        RESUME：经 WorkspaceManager.register_workspace 重建（需 locator，MVP
            仅记录错误，locator 未在 desired-state 持久化，记 TODO）。
        PAUSE：report.paused++。
        DROP：report.dropped++。
        """
        action = self._config.on_unknown_workspace
        if action == RecoveryAction.PAUSE:
            report.paused += 1
            logger.warning(
                "reconcile: workspace %s (project %s) not registered, paused",
                getattr(mount, "workspace_id", "?"),
                project.project_id,
            )
        elif action == RecoveryAction.DROP:
            report.dropped += 1
            logger.warning(
                "reconcile: workspace %s (project %s) not registered, dropped",
                getattr(mount, "workspace_id", "?"),
                project.project_id,
            )
        else:  # RESUME
            # MVP: locator 未持久化于 desired-state，无法 register；记 TODO
            report.errors.append(
                f"project {project.project_id}: workspace "
                f"{getattr(mount, 'workspace_id', '?')} not registered; "
                "RESUME rebuild requires locator (TODO manifest-instance)"
            )

    async def _reconcile_agents(self, project: ProjectRecord, report: ReconcileReport) -> None:
        """对账 agent：校验实例 manifest（os.path.exists）+ spawn 缺失 agent。"""
        legacy_name_counts: dict[str, int] = {}
        for agent in project.agents:
            if not agent.agent_id:
                legacy_name_counts[agent.name] = legacy_name_counts.get(agent.name, 0) + 1
        ambiguous_legacy = {name for name, count in legacy_name_counts.items() if count > 1}
        for cluster_id in project.cluster_ids:
            try:
                handle = self._cluster_transport.get_handle(cluster_id)
            except KeyError:
                try:
                    handle = await self._cluster_transport.ensure_cluster(
                        cluster_id,
                        project_id=project.project_id,
                        project_root_locator=project.project_root_locator,
                    )
                except Exception as exc:  # noqa: BLE001
                    report.errors.append(
                        f"project {project.project_id}: ensure_cluster({cluster_id}) "
                        f"for agents failed: {exc}"
                    )
                    continue
            try:
                existing_agents = await handle.list_agents(project.project_id)
                existing_ids = {
                    str(a.get("agent_id")) for a in existing_agents if a.get("agent_id")
                }
                existing_names = {a.get("name") for a in existing_agents}
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"project {project.project_id}: list_agents({cluster_id}) failed: {exc}"
                )
                continue
            for agent in project.agents:
                if agent.cluster_id != cluster_id or agent.desired_state == "stopped":
                    continue
                if not agent.agent_id and agent.name in ambiguous_legacy:
                    report.agents_failed += 1
                    error = "ambiguous legacy name has no stable agent_id"
                    report.errors.append(
                        f"project {project.project_id} cluster {cluster_id} agent "
                        f"{agent.name}: {error}"
                    )
                    report.agent_results.append(
                        {
                            "project_id": project.project_id,
                            "cluster_id": cluster_id,
                            "agent_id": "",
                            "agent_name": agent.name,
                            "outcome": "failed",
                            "error": error,
                        }
                    )
                    continue
                # 实例 manifest 校验（当前仅 os.path.exists，缺失 warning 不渲染）
                if agent.instance_manifest_path:
                    if not os.path.exists(agent.instance_manifest_path):
                        logger.warning(
                            "reconcile: instance manifest missing for agent %s "
                            "(project %s): %s — TODO manifest-instance render",
                            agent.name,
                            project.project_id,
                            agent.instance_manifest_path,
                        )
                if (agent.agent_id and agent.agent_id in existing_ids) or (
                    not agent.agent_id and agent.name in existing_names
                ):
                    continue
                recovery_mode, spawn_error = await self._spawn_agent(project.project_id, agent)
                if recovery_mode == "skipped":
                    continue
                if recovery_mode is not None:
                    report.agents_spawned += 1
                    if recovery_mode == "restored":
                        report.agents_restored += 1
                    elif recovery_mode == "initialized":
                        report.agents_initialized += 1
                    report.agent_results.append(
                        {
                            "project_id": project.project_id,
                            "cluster_id": cluster_id,
                            "agent_id": agent.agent_id,
                            "agent_name": agent.name,
                            "outcome": recovery_mode,
                        }
                    )
                else:
                    report.agents_failed += 1
                    message = (
                        f"project {project.project_id} cluster {cluster_id} agent "
                        f"{agent.agent_id or agent.name}: {spawn_error or 'spawn failed'}"
                    )
                    report.errors.append(message)
                    report.agent_results.append(
                        {
                            "project_id": project.project_id,
                            "cluster_id": cluster_id,
                            "agent_id": agent.agent_id,
                            "agent_name": agent.name,
                            "outcome": "failed",
                            "error": spawn_error or "spawn failed",
                        }
                    )

    async def _spawn_agent(
        self, project_id: str, agent: AgentSpec
    ) -> tuple[str | None, str | None]:
        """spawn 单个 agent；返回 ``(outcome, error)``。"""
        try:
            return await self._project_mgr.restore_agent_if_desired(
                project_id, agent.agent_id, agent.name
            )
        except Exception as exc:
            logger.warning("reconcile: restore %s failed: %s", agent.name, exc)
            return None, str(exc)

    # ─── 事件 ───

    async def _emit(self, report: ReconcileReport) -> None:
        if self._on_event is None:
            return
        event_type = "subject_reconciled" if report.success else "reconcile_failed"
        try:
            await self._on_event(event_type, report.to_payload())
        except Exception:  # noqa: BLE001
            logger.exception("reconcile: emit event failed")
