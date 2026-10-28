# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Project 域载荷模型（schema、CRUD、reconcile 与事件）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghrah.protocol.enums import ProjectStatus, RecoveryAction

# ─── Project 载荷模型 ───


class PathGrantSchema(BaseModel):
    """Agent 对某 workspace 子路径的访问授权。"""

    workspace_id: str
    subpath: str = "."


class WorkspaceMountSchema(BaseModel):
    """Project 挂载的 workspace（对齐 ghrah-subject project/models.WorkspaceMount）。"""

    workspace_id: str
    role: str | None = None
    default_for_agents: bool = False


class WritableWorkspaceSpec(BaseModel):
    """project_create 时登记的默认批准读写 Workspace。"""

    locator: str
    name: str = ""
    role: str | None = None
    default_for_agents: bool = False


class AgentSpecSchema(BaseModel):
    """Project 内 agent desired-state 摘要。"""

    agent_id: str = ""
    name: str
    cluster_id: str
    agent_config_name: str | None = None
    manifest_ref: str = ""
    instance_manifest_path: str = ""
    system_prompt: str = ""
    abilities: list[str] | None = None
    desired_state: Literal["running", "stopped"] = "running"
    path_grants: list[PathGrantSchema] = Field(default_factory=list)
    runtime_status: str = "pending"
    runtime_error: str = ""


class IsolationSpecPayload(BaseModel):
    """Project 隔离配置的 wire 形态。"""

    agent_path_grants: dict[str, list[PathGrantSchema]] = Field(default_factory=dict)
    agent_private_dir: bool = True
    effect_allowlist: set[str] | None = None
    hitl_override: dict[str, Any] | None = None
    task_scope: bool = True


class ProjectInfoPayload(BaseModel):
    """Project 信息基类载荷。

    对齐 ghrah-subject project/models.ProjectRecord，用于 project_get/project_list
    结果项及 PROJECT_* 事件回执。version/archived_at/deleted_at/cluster_ids/workspaces/
    project_root_locator 为 Subject 独占内部状态 Root；workspaces 为 Agent 工作资源。
    Project 内部存储位置统一由 ``project_root_locator`` 派生。
    """

    project_id: str
    name: str
    description: str = ""
    project_root_locator: str = ""
    manifest_ref: str = ""
    cluster_ids: list[str] = Field(default_factory=list)
    workspaces: list[WorkspaceMountSchema] = Field(default_factory=list)
    agents: list[AgentSpecSchema] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    isolation: IsolationSpecPayload = Field(default_factory=IsolationSpecPayload)
    status: ProjectStatus = ProjectStatus.ACTIVE
    recovery: str = RecoveryAction.RESUME.value
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None
    # 仅用于旧软删（deleted_at）数据迁移；新生命周期不再写入该字段。
    deleted_at: str | None = None


class ProjectCreatePayload(BaseModel):
    """project_create 命令载荷。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    project_root_locator: str = ""
    writable_workspaces: list[WritableWorkspaceSpec] = Field(default_factory=list)
    manifest_ref: str = ""
    recovery: str = RecoveryAction.RESUME.value


class ProjectUpdatePayload(BaseModel):
    """project_update 命令载荷（乐观锁）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    name: str | None = None
    description: str | None = None
    manifest_ref: str | None = None
    expected_version: int | None = None


class ProjectIdPayload(BaseModel):
    """project_get / project_pause / project_resume / project_stop 命令载荷。"""

    project_id: str


class ProjectLifecyclePayload(ProjectIdPayload):
    """project_archive / project_restore 命令载荷（乐观锁）。"""

    expected_version: int


class ProjectDeletePayload(ProjectLifecyclePayload):
    """project_delete 命令载荷。"""

    cascade_rooms: bool = False


class ProjectListPayload(BaseModel):
    """project_list 命令载荷（运行状态与归档状态分轴过滤）。"""

    status: ProjectStatus | None = None
    archived: bool | None = False
    # 兼容读取旧 deleted_at 记录；迁移完成后删除。
    include_deleted: bool = False


class ProjectAddAgentPayload(BaseModel):
    """project_add_agent 命令载荷。"""

    project_id: str
    agent: AgentSpecSchema
    expected_version: int | None = None


class ProjectRemoveAgentPayload(BaseModel):
    """project_remove_agent 命令载荷。"""

    project_id: str
    agent_name: str
    agent_id: str
    expected_version: int | None = None


class ProjectLinkTaskPayload(BaseModel):
    """project_link_task 命令载荷。"""

    project_id: str
    task_id: str
    expected_version: int | None = None


class ProjectUnlinkTaskPayload(BaseModel):
    """project_unlink_task 命令载荷。"""

    project_id: str
    task_id: str
    expected_version: int | None = None


class ProjectSetRecoveryPayload(ProjectIdPayload):
    """project_set_recovery 命令载荷。"""

    recovery: RecoveryAction = RecoveryAction.RESUME
    expected_version: int | None = None


class ProjectListResultPayload(BaseModel):
    """project_list 命令结果载荷。"""

    projects: list[ProjectInfoPayload] = Field(default_factory=list)
    count: int = 0


class ReconcileNowPayload(BaseModel):
    """reconcile_now 命令载荷（触发一次 reconcile）。"""

    subject_id: str = "default"


class ReconcileStatusPayload(BaseModel):
    """reconcile_status 命令载荷（查询最近一次 reconcile 报告）。"""

    subject_id: str = "default"  # ─── Project 事件载荷模型 ───


class ProjectEventPayload(BaseModel):
    """project_* 事件载荷（created/updated/deleted/paused/resumed/stopped/
    recovery_set 通用基类）。

    携带变更后全量 ProjectInfoPayload 快照，Observer 据此更新本地 store。
    """

    project: ProjectInfoPayload


class ProjectAgentEventPayload(BaseModel):
    """project_agent_added / project_agent_removed 事件载荷。"""

    project: ProjectInfoPayload
    agent_name: str
    agent_id: str = ""


class ReconcileReportPayload(BaseModel):
    """subject_reconciled / reconcile_failed 事件载荷（reconcile 报告摘要）。"""

    subject_id: str
    success: bool
    projects_reconciled: int = 0
    agents_spawned: int = 0
    agents_restored: int = 0
    agents_initialized: int = 0
    agents_failed: int = 0
    agent_results: list[dict[str, Any]] = Field(default_factory=list)
    workspaces_adopted: int = 0
    errors: list[str] = Field(default_factory=list)
    bootstrap: bool = False
    paused: int = 0
    dropped: int = 0
    tasks_migrated: int = 0
