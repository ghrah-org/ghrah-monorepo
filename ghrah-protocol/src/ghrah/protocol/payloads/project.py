# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Project 域载荷模型（schema、CRUD、reconcile 与事件）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghrah.protocol.enums import ProjectStatus, RecoveryAction
from ghrah.protocol.payloads.model import EndpointReferenceModel

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


class AgentPresentationSchema(BaseModel):
    """展示字段快照；只影响观察端，不参与装配。"""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    deprecated: bool = False


class AgentOriginSchema(BaseModel):
    """来源审计：来自哪个模板的哪一版；运行期不回读模板。"""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    revision: int = Field(ge=1)


class AgentRecordSchema(EndpointReferenceModel):
    """Project 内一个 Agent 的记录：身份 + 展示 + 编排状态 + 来源。

    **不承载配置**：模型/提示词/上下文/能力的权威在 Core 的实例快照，模板只在
    创建时填一次。旧元素（含 ``manifest_ref`` 等字段）不兼容，必须由
    ``scripts/purge_legacy_agents.py`` 清空后才会被读取。
    """

    agent_id: str = ""
    project_id: str = ""
    name: str
    presentation: AgentPresentationSchema = Field(default_factory=AgentPresentationSchema)
    origin: AgentOriginSchema | None = None
    desired_state: Literal["running", "stopped"] = "running"
    runtime_status: Literal["pending", "running", "stopped", "failed", "lost"] = "pending"
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""


class AgentCreateSpecSchema(BaseModel):
    """按模板创建一个 Agent 的请求体（不含任何配置字段）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    desired_state: Literal["running", "stopped"] = "running"
    initial_bindings: list[str] | None = None
    """创建时选中的成员全名；``None`` 表示按模板默认轴全部绑定。"""


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
    结果项及 PROJECT_* 事件回执。version/archived_at/deleted_at/workspaces/
    project_root_locator 为 Subject 独占内部状态 Root；workspaces 为 Agent 工作资源。
    Project 内部存储位置统一由 ``project_root_locator`` 派生。
    """

    project_id: str
    name: str
    description: str = ""
    project_root_locator: str = ""
    shell_cwd: str = ""
    manifest_ref: str = ""
    workspaces: list[WorkspaceMountSchema] = Field(default_factory=list)
    agents: list[AgentRecordSchema] = Field(default_factory=list)
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
    # 空字符串表示不设置；创建时若仍为空且登记了 default workspace，由 Subject 推导。
    shell_cwd: str = ""


class ProjectUpdatePayload(BaseModel):
    """project_update 命令载荷（乐观锁）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    name: str | None = None
    description: str | None = None
    manifest_ref: str | None = None
    expected_version: int | None = None
    # None 表示本次不改；空字符串表示清空，恢复为不覆盖进程 cwd。
    shell_cwd: str | None = None


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
    """project_add_agent 命令载荷：按模板创建（不再携带任何配置）。"""

    project_id: str
    agent: AgentCreateSpecSchema
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


# ─── Project REST 请求/响应模型（观察端管理面，非 WS wire） ───


class ProjectUpdateRequest(BaseModel):
    """PATCH /api/v1/projects/{project_id} 的请求体。

    路径携带项目身份；body 只含可改字段与乐观锁版本。四个可改字段至少
    提供一个，避免产生空更新事件。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    manifest_ref: str | None = None
    shell_cwd: str | None = None
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _require_change(self) -> ProjectUpdateRequest:
        if all(
            value is None
            for value in (self.name, self.description, self.manifest_ref, self.shell_cwd)
        ):
            raise ValueError("至少需要一个可改字段")
        return self


class ProjectLifecycleRequest(BaseModel):
    """POST .../archive、.../restore 的请求体（无条件写）。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class ProjectTransitionRequest(BaseModel):
    """POST .../pause、.../resume、.../stop 的请求体。

    本轮 REST 建议必填；字段保持可选以复用 WS 命令语义。
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


class ProjectDeletedView(BaseModel):
    """DELETE /api/v1/projects/{project_id} 的 200 响应。

    REST 面专用视图，不进入 WS wire；与 ProjectManager._handle_delete 的
    ``data`` 字段一一对应。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    deleted: bool
    storage_purged: bool
    rooms_deleted: int = Field(ge=0)


class ProjectTransitionPayload(ProjectIdPayload):
    """project_pause / project_resume / project_stop 命令载荷。

    在 ProjectIdPayload 上补可选 expected_version；未提供时沿用「以当前
    版本为准」的历史行为（Manager 侧校验）。
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


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
