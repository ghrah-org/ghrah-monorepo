# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project 隔离原语数据模型。

对齐 ``ghrah.protocol.types`` 的 ``ProjectInfoPayload`` schema：
``ProjectStatus`` / ``RecoveryAction`` 直接复用 protocol 枚举（单一真相源），
避免双定义漂移。``ProjectRecord`` 内部用 ``datetime``，序列化为 ISO str 以
匹配 wire payload；扩展 ``version``（乐观锁）、``archived_at``，并仅兼容
读取旧 ``deleted_at``。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from ghrah.plugin.assembly import PluginAssembly
from ghrah.protocol.types import ProjectStatus, RecoveryAction
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

__all__ = [
    "PROJECT_TRANSITIONS",
    "AGENT_TRANSITIONS_NOTE",
    "AgentSpec",
    "IsolationSpec",
    "PathGrant",
    "ProjectRecord",
    "ProjectRuntimeConfig",
    "ProjectStatus",
    "RecoveryAction",
    "RecoverySpec",
    "WorkspaceMount",
    "WritableWorkspaceSpec",
    "can_transition",
    "make_project_record",
    "normalize_status",
]


class RecoverySpec(BaseModel):
    """Project 恢复策略。

    Attributes:
        on_restart: Subject 重启后该 project 的恢复动作。默认 resume（重建
            cluster + agent desired-state）。pause=暂停待人工；drop=丢弃。
    """

    model_config = ConfigDict(frozen=True)

    on_restart: RecoveryAction = RecoveryAction.RESUME


class PathGrant(BaseModel):
    """Agent 对某 workspace 子路径的访问授权。

    Attributes:
        workspace_id: 授权目标 workspace（须在 project.workspaces 内）。
        subpath: workspace 内子路径；``"."`` 表示整个 workspace 根。
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: str
    subpath: str = "."


class WorkspaceMount(BaseModel):
    """Project 挂载的 workspace。

    Attributes:
        workspace_id: 被挂载的 workspace 主键。
        role: 自由文本角色（如 "default"/"scratch"），不预设语义。
        default_for_agents: 是否为 agent 所属 workspace 隐式落点
            （决策 B：MVP 强制全 project 至多一个 ``True``）。
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: str
    role: str | None = None
    default_for_agents: bool = False

    @staticmethod
    def validate_single_default(mounts: list[WorkspaceMount]) -> WorkspaceMount | None:
        """校验 ``default_for_agents=True`` 唯一性（决策 B）。

        Returns:
            唯一的 default mount（若有），否则 None。

        Raises:
            ValueError: 多于一个 default mount。
        """
        defaults = [m for m in mounts if m.default_for_agents]
        if len(defaults) > 1:
            raise ValueError(
                "At most one WorkspaceMount may have default_for_agents=True "
                f"(found {len(defaults)})"
            )
        return defaults[0] if defaults else None


class WritableWorkspaceSpec(BaseModel):
    """创建 Project 时使用的可读写 Workspace 输入。"""

    model_config = ConfigDict(frozen=True)

    locator: str
    name: str = ""
    role: str | None = None
    default_for_agents: bool = False


class AgentSpec(BaseModel):
    """Project 内 agent desired-state 摘要。

    不含 ``default_workspace_id``（决策 B）：agent 所属 workspace = project 内
    ``default_for_agents=True`` 的 WorkspaceMount（MVP 唯一）。

    Attributes:
        agent_id: 跨 Room/cluster 稳定不变的业务身份（UUID hex）。
        name: agent 名（cluster 内唯一）。
        cluster_id: 所属 Core 集群 id（须在 project.cluster_ids 内）。
        manifest_ref: Subject 级模板库引用（~/.ghrah/manifests）。
        instance_manifest_path: 实例 manifest 落点（default workspace 内
            ``.ghrah/agents/<name>.yaml``）。
        system_prompt: 系统提示词覆盖（空则沿用模板）。
        abilities: ability 全名列表；None 表示沿用模板默认。
        path_grants: 对各 workspace 子路径的写授权列表。
    """

    model_config = ConfigDict(frozen=True)

    name: str
    cluster_id: str
    agent_id: str = ""
    manifest_ref: str = ""
    instance_manifest_path: str = ""
    system_prompt: str = ""
    abilities: list[str] | None = None
    desired_state: Literal["running", "stopped"] = "running"
    path_grants: list[PathGrant] = Field(default_factory=list)
    # 运行诊断（desired-state 的一部分，随定义持久化，重启后可读）：
    # pending = 定义已写入、runtime 尚未确认；running = 已在 cluster 上启动；
    # error = 启动失败，runtime_error 记录可诊断原因（reconcile 可重试收敛）。
    runtime_status: str = "pending"
    runtime_error: str = ""


class IsolationSpec(BaseModel):
    """Project 隔离配置（Foxtrail Stage 3b 施加依据，MVP 仅记录）。

    Attributes:
        agent_path_grants: agent_name → 其 PathGrant 列表（runtime 写操作前
            校验目标路径落在该 agent grants 内）。
        agent_private_dir: 是否为每个 agent 在 default workspace 内分配
            ``<workspace_root>/<agent_name>/`` 私有目录。
        effect_allowlist: 允许的 effect action 类名集合（收紧自全局）。
            MVP 用 ``set[str]`` 占位；Foxtrail Stage 3b 收紧为
            ``frozenset[ActionClass]``。None 表示不收紧（继承全局）。
        hitl_override: project 级 HITL 策略覆盖键值。
        task_scope: 是否将 agent 执行限定在 project 关联 task 作用域。
    """

    model_config = ConfigDict(frozen=True)

    agent_path_grants: dict[str, list[PathGrant]] = Field(default_factory=dict)
    agent_private_dir: bool = True
    effect_allowlist: set[str] | None = None
    hitl_override: dict[str, Any] | None = None
    task_scope: bool = True


PROJECT_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.ACTIVE: frozenset(
        {ProjectStatus.PAUSED, ProjectStatus.STOPPED, ProjectStatus.FAILED}
    ),
    ProjectStatus.PAUSED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.STOPPED}),
    ProjectStatus.STOPPED: frozenset({ProjectStatus.ACTIVE}),
    ProjectStatus.FAILED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.STOPPED}),
}

# 文档性常量：用于在注释/校验信息中引用状态机不变量，非运行期消费。
AGENT_TRANSITIONS_NOTE = (
    "ACTIVE<->PAUSED, ACTIVE/PAUSED->STOPPED, ACTIVE->FAILED, STOPPED/FAILED->ACTIVE"
)


def can_transition(current: ProjectStatus, target: ProjectStatus) -> bool:
    """current → target 是否为合法状态流转。"""
    return target in PROJECT_TRANSITIONS.get(current, frozenset())


def normalize_status(value: str | ProjectStatus) -> ProjectStatus:
    """str | ProjectStatus → ProjectStatus。"""
    if isinstance(value, ProjectStatus):
        return value
    return ProjectStatus(value)


class ProjectRuntimeConfig(BaseModel):
    """Project 运行期配置（内部装配语义，不进 wire）。

    与 ``ghrah.subject.config.ProjectConfig``（Unit 配置切片 dataclass）
    分属两层：此处是 ProjectRecord 持久化的 per-project 配置权威，
    ``plugins`` 是插件装配清单。
    """

    model_config = ConfigDict(frozen=True)

    plugins: PluginAssembly = Field(default_factory=PluginAssembly)


class ProjectRecord(BaseModel):
    """Subject 内部 project 记录（对齐 protocol ``ProjectInfoPayload``）。

    独立定义（不继承 ``ProjectInfoPayload``）以使用 subject 侧
    ``WorkspaceMount``/``AgentSpec`` 强类型与 ``IsolationSpec`` 扩展字段；
    ``to_wire()`` 输出 ``ProjectInfoPayload`` 形态 dict 保证 wire 一一对应：
    - ``project_root_locator`` 是 Subject 独占内部状态 Root，不属于 workspaces；
    - 时间戳内部用 ``datetime(UTC)``，序列化为 ISO str。
    - ``recovery`` 内部用 ``RecoverySpec``，序列化为 ``RecoveryAction.value``。
    - ``archived_at`` 是资源可用性轴；``deleted_at`` 仅保留旧数据兼容读取。
    - ``config`` 是内部装配语义（plugins 装配清单），``to_wire()`` 不输出。
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: str
    name: str
    description: str = ""
    project_root_locator: str = ""
    manifest_ref: str = ""
    cluster_ids: list[str] = Field(default_factory=list)
    workspaces: list[WorkspaceMount] = Field(default_factory=list)
    agents: list[AgentSpec] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    isolation: IsolationSpec = Field(default_factory=IsolationSpec)
    status: ProjectStatus = ProjectStatus.ACTIVE
    recovery: RecoverySpec = Field(default_factory=RecoverySpec)
    config: ProjectRuntimeConfig | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = 1
    archived_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_serializer("created_at", "updated_at", "archived_at", "deleted_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @field_validator("created_at", "updated_at", "archived_at", "deleted_at", mode="before")
    @classmethod
    def _coerce_dt(cls, value: Any) -> Any:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value

    @field_serializer("recovery")
    def _ser_recovery(self, value: RecoverySpec) -> str:
        return value.on_restart.value

    @field_validator("recovery", mode="before")
    @classmethod
    def _coerce_recovery(cls, value: Any) -> Any:
        if isinstance(value, RecoverySpec):
            return value
        if isinstance(value, str):
            return RecoverySpec(on_restart=RecoveryAction(value))
        if isinstance(value, dict):
            return RecoverySpec.model_validate(value)
        return value

    def to_wire(self) -> dict[str, Any]:
        """输出 ``ProjectInfoPayload`` 形态 dict。

        recovery 为 ``RecoveryAction.value`` 字符串，时间戳 ISO str，含
        version/archived_at/deleted_at/cluster_ids/workspaces/agents/task_ids。
        ``isolation`` 已纳入 ``ProjectInfoPayload``，Observer 可对运行时实际返回的
        隔离配置进行强类型读取。``config``（内部装配语义）不进 wire。
        """
        return self.model_dump(mode="json", exclude={"config"})


def make_project_record(
    *,
    name: str,
    description: str = "",
    project_root_locator: str = "",
    manifest_ref: str = "",
    recovery: RecoverySpec | None = None,
) -> ProjectRecord:
    """创建新 project 记录：生成 project_id（uuid4 hex）、status=ACTIVE、
    created_at=updated_at=now(UTC)、空 cluster_ids/workspaces/agents/task_ids。"""
    now = datetime.now(UTC)
    return ProjectRecord(
        project_id=uuid4().hex,
        name=name,
        description=description,
        project_root_locator=project_root_locator,
        manifest_ref=manifest_ref,
        status=ProjectStatus.ACTIVE,
        recovery=recovery if recovery is not None else RecoverySpec(),
        created_at=now,
        updated_at=now,
    )
