# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 资源与 Agent 模板的观察端 REST 契约。

规格、运行快照和能力摘要放在同一次读取里。能力授权的明细仍走
``ability_control``，本模块不接收授权字段。

模板部分（``AgentTemplate*``）是实例级资源 ``agent-templates`` 的 wire
形状：metadata 七字段 + 五组配置 + ``path_request``。**不复用** Core 端口
DTO ``AgentTemplateSpec``；模板的持久化与 CRUD 由 Subject 持有。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ghrah.protocol.ability_tree import TreeModel

__all__ = [
    "AgentAbilitySummary",
    "AgentAdminView",
    "AgentCreateRequest",
    "AgentDeletedView",
    "AgentDesiredStateRequest",
    "AgentListView",
    "AgentPromptSummary",
    "AgentPresentationView",
    "AgentOriginView",
    "AgentRuntimeView",
    "AgentSpecPatch",
    "AgentSpecificationSummary",
    "AgentSummaryView",
    "AgentTemplateAbilityRequestView",
    "AgentTemplateArchiveRequest",
    "AgentTemplateContextOverridesView",
    "AgentTemplateContextPolicyView",
    "AgentTemplateCreateRequest",
    "AgentTemplateDetailView",
    "AgentTemplateListView",
    "AgentTemplateMetadataView",
    "AgentTemplateModelView",
    "AgentTemplateOperationView",
    "AgentTemplatePackageView",
    "AgentTemplatePathRequestView",
    "AgentTemplatePersistenceView",
    "AgentTemplatePermissionRequestView",
    "AgentTemplatePromptView",
    "AgentTemplateReferenceView",
    "AgentTemplateReferencesView",
    "AgentTemplateRevisionSummaryView",
    "AgentTemplateRevisionsView",
    "AgentTemplateScriptView",
    "AgentTemplateSkillView",
    "AgentTemplateSummaryView",
    "AgentTemplateSurfaceView",
    "AgentTemplateUpdateRequest",
]

#: 模板命名空间/名称正则（与旧 Manifest ``AgentMetadata`` 同形）。
TEMPLATE_NAMESPACE_PATTERN = r"^[a-z][a-z0-9_.]*[a-z0-9]$"
TEMPLATE_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class AgentPromptSummary(TreeModel):
    """提示词正文的摘要。列表不返回正文。"""

    digest: str
    size_bytes: int = Field(ge=0)


class AgentRuntimeView(TreeModel):
    """进程是否在线。不含会话和邮箱。"""

    availability: Literal["online", "offline", "unknown"]
    incarnation_id: str | None = None
    busy: bool | None = None
    error: str | None = None


class AgentAbilitySummary(TreeModel):
    """能力关系的摘要。明细仍读取 ability-admin。"""

    revision: int = Field(ge=0)
    state: Literal["running", "stopped", "unassembled"]
    requires_respawn: bool = False
    pending_reasons: list[str] = Field(default_factory=list)


class AgentPresentationView(TreeModel):
    """记录里的展示字段快照（不影响装配）。"""

    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    deprecated: bool = False


class AgentOriginView(TreeModel):
    """记录的来源：模板身份 + 创建时使用的修订。"""

    template_id: str
    revision: int = Field(ge=1)


class AgentSpecificationSummary(TreeModel):
    """列表用的记录摘要。

    不含任何配置字段：模型/提示词/上下文/能力的权威在 Core 的实例快照，
    由 ``/agents/{agent_id}/config`` 单独读。
    """

    agent_id: str
    name: str
    presentation: AgentPresentationView
    origin: AgentOriginView | None = None
    desired_state: Literal["running", "stopped"]
    runtime_status: str
    last_error: str = ""
    project_version: int = Field(ge=0)


class AgentSummaryView(TreeModel):
    """列表中的一个 Agent。"""

    format: Literal["ghrah.agent-control.summary.v1"] = "ghrah.agent-control.summary.v1"
    project_id: str
    specification: AgentSpecificationSummary
    runtime: AgentRuntimeView
    ability: AgentAbilitySummary | None = None
    ability_unavailable: str | None = None


class AgentListView(TreeModel):
    """项目内的 Agent 列表。"""

    format: Literal["ghrah.agent-control.list.v1"] = "ghrah.agent-control.list.v1"
    project_id: str
    agents: list[AgentSummaryView] = Field(default_factory=list)


class AgentAdminView(TreeModel):
    """一次读取返回的完整 Agent 资源。"""

    format: Literal["ghrah.agent-control.v1"] = "ghrah.agent-control.v1"
    project_id: str
    specification: AgentSpecificationSummary
    runtime: AgentRuntimeView
    ability: AgentAbilitySummary | None = None
    ability_unavailable: str | None = None
    effective: Literal["next_start", "stored"] | None = None
    spec_overrides_template: bool = False


class AgentCreateRequest(TreeModel):
    """按模板创建 Agent；配置不进请求体，默认停止。"""

    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    desired_state: Literal["running", "stopped"] = "stopped"


class AgentSpecPatch(TreeModel):
    """修改记录的展示字段；配置由 Core 快照承载，不可经此改写。"""

    expected_revision: int = Field(ge=0)
    presentation: AgentPresentationView | None = None

    @model_validator(mode="after")
    def require_one_field(self) -> AgentSpecPatch:
        """没有可写字段时拒绝。"""
        if self.presentation is None:
            raise ValueError("至少提供 presentation")
        return self


class AgentDesiredStateRequest(TreeModel):
    """声明期望的运行状态。"""

    expected_revision: int = Field(ge=0)
    state: Literal["running", "stopped"]


class AgentDeletedView(TreeModel):
    """删除成功后的项目版本，供下一次条件写使用。"""

    format: Literal["ghrah.agent-control.deleted.v1"] = "ghrah.agent-control.deleted.v1"
    project_id: str
    agent_id: str
    project_version: int = Field(ge=0)


# ─────────────────────────── Agent 模板（agent-templates） ───────────────────────────


class AgentTemplateMetadataView(TreeModel):
    """模板元数据（7 字段）；``namespace``/``name`` 创建后不可改。"""

    namespace: str = Field(pattern=TEMPLATE_NAMESPACE_PATTERN)
    name: str = Field(pattern=TEMPLATE_NAME_PATTERN)
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    deprecated: bool = False

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not tag.strip():
                raise ValueError("tags 的元素必须是非空字符串")
        return value


class AgentTemplateModelView(TreeModel):
    """模型引用（端点名，不是凭据；草稿可空）。"""

    endpoint_name: str | None = None
    model_overrides: dict[str, Any] | None = None

    @field_validator("endpoint_name")
    @classmethod
    def _non_empty_endpoint(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("endpoint_name 提供时不得为空串")
        return value


class AgentTemplatePromptView(TreeModel):
    """系统提示词（草稿允许空串）。"""

    system_prompt: str = ""


class AgentTemplatePersistenceView(TreeModel):
    """上下文持久化覆盖三键（均可空）。"""

    type: str | None = None
    compress: bool | None = None
    auto_persist: bool | None = None


class AgentTemplateContextOverridesView(TreeModel):
    """``context_policy.overrides``：只允许 ``persistence``。"""

    persistence: AgentTemplatePersistenceView | None = None


class AgentTemplateContextPolicyView(TreeModel):
    """上下文策略；``max_iterations`` 默认 ``-1``（关闭限制）。"""

    max_iterations: int = Field(default=-1, ge=-1)
    communication_timeout: float = Field(default=300, ge=-1)
    window: dict[str, Any] | None = None
    overrides: AgentTemplateContextOverridesView | None = None


class AgentTemplateSurfaceView(TreeModel):
    """能力可见面。"""

    mode: Literal["auto", "static", "progressive"] = "auto"
    static: list[str] = Field(default_factory=list)
    max_static: int | None = Field(default=None, ge=1)


class AgentTemplateOperationView(TreeModel):
    """operation 轴申请；``visible=false && available=true`` 非法。"""

    key: str = Field(min_length=1)
    visible: bool = True
    available: bool = True

    @model_validator(mode="after")
    def _check_axes(self) -> AgentTemplateOperationView:
        if not self.visible and self.available:
            raise ValueError("visible=false 的 operation 不能同时 available=true")
        return self


class AgentTemplateScriptView(TreeModel):
    """skill 下的脚本申请（``exec`` 才是可执行性）。"""

    key: str = Field(min_length=1)
    visible: bool = True
    exec: bool = False


class AgentTemplateSkillView(TreeModel):
    """skill 申请；skill 没有 ``available``。"""

    key: str = Field(min_length=1)
    visible: bool = True
    scripts: list[AgentTemplateScriptView] = Field(default_factory=list)


class AgentTemplatePackageView(TreeModel):
    """包级申请；整包与成员二选一。"""

    package: str = Field(min_length=1)
    origin: Literal["builtin", "installed", "mcp"]
    whole_container: bool = False
    operations: list[AgentTemplateOperationView] = Field(default_factory=list)
    skills: list[AgentTemplateSkillView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_container(self) -> AgentTemplatePackageView:
        if self.whole_container and (self.operations or self.skills):
            raise ValueError("whole_container=true 时不能同时申请 operations/skills")
        return self


class AgentTemplateAbilityRequestView(TreeModel):
    """能力申请（模板层只做形状校验，不判断成员是否存在）。"""

    surface: AgentTemplateSurfaceView = Field(default=AgentTemplateSurfaceView())
    packages: list[AgentTemplatePackageView] = Field(default_factory=list)
    mcp_refs: list[str] = Field(default_factory=list)


class AgentTemplatePermissionRequestView(TreeModel):
    """审批策略覆盖（L4 口径，只能收紧部署策略）。"""

    auto_approve: list[str] = Field(default_factory=list)
    require_approval_by_default: bool = True
    safe_extra_commands: list[str] = Field(default_factory=list)
    safe_extra_sub_commands: list[tuple[str, list[str]]] | None = None

    @field_validator("safe_extra_sub_commands", mode="before")
    @classmethod
    def _coerce_pairs(cls, value: Any) -> Any:
        """JSON 数组 → 二元组；strict 模型不会自动做 list → tuple。"""
        if isinstance(value, list):
            return [tuple(item) if isinstance(item, list) else item for item in value]
        return value


class AgentTemplatePathRequestView(TreeModel):
    """路径申请（``scope=agent`` 时 ``package`` 必须为 null）。"""

    scope: Literal["agent", "package"]
    package: str | None = None
    workspace_role: str = Field(min_length=1)
    subpath: str = Field(min_length=1)
    access: Literal["read", "write"]
    deny: bool

    @field_validator("subpath")
    @classmethod
    def _check_subpath(cls, value: str) -> str:
        text = value.replace("\\", "/")
        if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
            raise ValueError("subpath 必须是 workspace 内相对路径")
        if any(part == ".." for part in text.split("/")):
            raise ValueError("subpath 不得包含 ..")
        return value

    @model_validator(mode="after")
    def _check_scope(self) -> AgentTemplatePathRequestView:
        if self.scope == "package":
            if not self.package:
                raise ValueError("scope=package 时 package 必填")
        elif self.package is not None:
            raise ValueError("scope=agent 时 package 必须为 null")
        return self


class AgentTemplateSummaryView(TreeModel):
    """列表项摘要：不含五组配置正文。"""

    format: Literal["ghrah.agent-templates.summary.v1"] = "ghrah.agent-templates.summary.v1"
    template_id: str
    revision: int = Field(ge=1)
    metadata: AgentTemplateMetadataView
    created_at: str
    updated_at: str
    archived_at: str | None = None
    reference_count: int = Field(default=0, ge=0)


class AgentTemplateListView(TreeModel):
    """模板列表。"""

    format: Literal["ghrah.agent-templates.list.v1"] = "ghrah.agent-templates.list.v1"
    templates: list[AgentTemplateSummaryView] = Field(default_factory=list)


class AgentTemplateDetailView(TreeModel):
    """模板详情：metadata + 五组 + ``path_request``。"""

    format: Literal["ghrah.agent-templates.v1"] = "ghrah.agent-templates.v1"
    template_id: str
    revision: int = Field(ge=1)
    metadata: AgentTemplateMetadataView
    model: AgentTemplateModelView
    prompt: AgentTemplatePromptView
    context_policy: AgentTemplateContextPolicyView
    ability_request: AgentTemplateAbilityRequestView
    permission_request: AgentTemplatePermissionRequestView
    path_request: list[AgentTemplatePathRequestView] = Field(default_factory=list)
    created_at: str
    updated_at: str
    archived_at: str | None = None
    reference_count: int = Field(default=0, ge=0)


class AgentTemplateRevisionSummaryView(TreeModel):
    """版本列表项：一行一个版本（不返回配置正文）。"""

    revision: int = Field(ge=1)
    created_at: str
    is_latest: bool
    reference_count: int = Field(ge=0)


class AgentTemplateRevisionsView(TreeModel):
    """修订历史列表。"""

    format: Literal["ghrah.agent-templates.revisions.v1"] = "ghrah.agent-templates.revisions.v1"
    template_id: str
    revisions: list[AgentTemplateRevisionSummaryView] = Field(default_factory=list)


class AgentTemplateReferenceView(TreeModel):
    """引用该模板的 Agent（v0 恒空，T2 接入）。"""

    project_id: str
    agent_id: str
    name: str


class AgentTemplateReferencesView(TreeModel):
    """模板引用列表。"""

    format: Literal["ghrah.agent-templates.references.v1"] = "ghrah.agent-templates.references.v1"
    template_id: str
    agents: list[AgentTemplateReferenceView] = Field(default_factory=list)


class AgentTemplateCreateRequest(TreeModel):
    """创建模板：只强制 ``metadata.namespace`` + ``metadata.name``。"""

    metadata: AgentTemplateMetadataView
    model: AgentTemplateModelView | None = None
    prompt: AgentTemplatePromptView | None = None
    context_policy: AgentTemplateContextPolicyView | None = None
    ability_request: AgentTemplateAbilityRequestView | None = None
    permission_request: AgentTemplatePermissionRequestView | None = None
    path_request: list[AgentTemplatePathRequestView] = Field(default_factory=list)


class AgentTemplateUpdateRequest(TreeModel):
    """更新模板：``metadata`` 展示字段按子字段 patch，其余组整组替换。"""

    expected_revision: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    icon: str | None = None
    deprecated: bool | None = None
    model: AgentTemplateModelView | None = None
    prompt: AgentTemplatePromptView | None = None
    context_policy: AgentTemplateContextPolicyView | None = None
    ability_request: AgentTemplateAbilityRequestView | None = None
    permission_request: AgentTemplatePermissionRequestView | None = None
    path_request: list[AgentTemplatePathRequestView] | None = None

    @model_validator(mode="after")
    def require_one_field(self) -> AgentTemplateUpdateRequest:
        """省略全部可写字段时没有可保存的变更。"""
        provided = self.model_fields_set - {"expected_revision"}
        if not provided:
            raise ValueError("至少提供一个可写字段")
        return self


class AgentTemplateArchiveRequest(TreeModel):
    """归档模板（CAS）。"""

    expected_revision: int = Field(ge=1)
