# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 资源视图。

字段逐项对齐 Core 现有序列化输出（``serialize_node`` / ``serialize_action_session`` /
``serialize_branch``），**不新增语义**，使消费方可以无损映射回自己的模型。

时间字段一律是 RFC3339 字符串，不是 ``datetime``——这是「只说 DTO」的直接后果。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AgentRuntimeView",
    "AgentStatus",
    "AgentAbilityRequest",
    "AgentContextPolicy",
    "AgentPermissionRequest",
    "AgentSpecView",
    "AgentTemplateModel",
    "AgentTemplatePrompt",
    "AgentTemplateSpec",
    "AgentSpawnSpec",
    "AgentAssemblyConfig",
    "AgentAssemblyContext",
    "AgentAssemblyLimits",
    "AgentAssemblyMember",
    "AgentAssemblyModel",
    "AgentAssemblyPermission",
    "AgentAssemblyPrompt",
    "AgentAssemblySpec",
    "AgentConfigAbilityView",
    "AgentConfigContextView",
    "AgentConfigCoreOnlyView",
    "AgentConfigGrantView",
    "AgentConfigModelView",
    "AgentConfigOperationView",
    "AgentConfigPathView",
    "AgentConfigPermissionView",
    "AgentConfigPersistenceView",
    "AgentConfigPromptView",
    "AgentConfigScriptView",
    "AgentConfigSkillView",
    "AgentConfigView",
    "AgentMemberAxes",
    "AgentMemberPermissions",
    "AgentStartView",
    "AgentSurfaceSpec",
    "AgentView",
    "BranchListView",
    "BranchView",
    "ChainMetaView",
    "ContentReadResult",
    "DeliveryView",
    "HistoryView",
    "MailboxSummaryView",
    "MessageIdListView",
    "MessageView",
    "PublishedMessageView",
    "NodeView",
    "SessionCatalogView",
    "SessionView",
    "ToolListView",
    "ToolView",
]


class NodeView(BaseModel):
    """单个 ActionChain 节点。

    Attributes:
        id: 节点标识
        parent_id: 父节点；根节点为 ``None``
        agent_name: 值是**稳定 Agent ID**（持久层分区键），非显示名
        session_id: 所属会话
        created_on_branch_id: 提交到哪个分支
        timestamp: 创建时间（RFC3339）
        iteration: 驱动循环迭代序号
        ability_names: 本节点涉及的能力名
        agent_state: 节点提交时的 Agent 状态快照
        messages_delta: 本节点新增的消息
        messages_snapshot: 本节点的完整消息快照；非快照节点为 ``None``
        is_snapshot: 是否快照节点
        action_results: 能力执行结果
        metadata: 节点元数据
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    parent_id: str | None = None
    agent_name: str = ""
    session_id: str = ""
    created_on_branch_id: str = ""
    timestamp: str = ""
    iteration: int = 0
    ability_names: list[str] = Field(default_factory=list)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    messages_delta: list[dict[str, Any]] = Field(default_factory=list)
    messages_snapshot: list[dict[str, Any]] | None = None
    is_snapshot: bool = False
    action_results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionView(BaseModel):
    """会话表示（对齐 ``serialize_action_session``）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    agent_name: str = ""
    name: str = "Session 1"
    root_node_id: str = ""
    active_branch_id: str = ""
    lifecycle: str = "open"
    system_prompt: str = ""
    origin_agent_name: str | None = None
    origin_session_id: str | None = None
    origin_node_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchView(BaseModel):
    """分支表示（对齐 ``serialize_branch``）。"""

    model_config = ConfigDict(extra="forbid")

    branch_id: str
    session_id: str = ""
    name: str = "main"
    head_node_id: str = ""
    lifecycle: str = "open"
    parent_branch_id: str | None = None
    fork_point_node_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionCatalogView(BaseModel):
    """会话目录（``sessions.catalog``）。

    语义对齐 Core 面向用户的读法：**过滤软删除**，无链数据时返回空目录
    （``active_session_id=None``），而不是报错。
    ``availability`` 由 Core 判定：在线取运行中 Actor，离线读持久层目录。
    消费方不再自己分支——这是把「在线/离线双路径」收进 Core 的关键字段。
    """

    model_config = ConfigDict(extra="forbid")

    availability: Literal["online", "offline"] = "offline"
    active_session_id: str | None = None
    sessions: list[SessionView] = Field(default_factory=list)
    branches: list[BranchView] = Field(default_factory=list)


class HistoryView(BaseModel):
    """链历史（``sessions.history``）。"""

    model_config = ConfigDict(extra="forbid")

    agent_name: str = ""
    agent_id: str = ""
    session_id: str = ""
    branch_id: str = ""
    active_session_id: str = ""
    nodes: list[NodeView] = Field(default_factory=list)


class ChainMetaView(BaseModel):
    """链目录元数据（``sessions.meta``）。

    只带 active 指针与「会话/分支」两张表，**不含节点载荷**——
    供「快速判断当前在哪个会话/分支」用。要节点请调 ``sessions.history``。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = ""
    active_session_id: str | None = None
    sessions: list[SessionView] = Field(default_factory=list)
    branches: list[BranchView] = Field(default_factory=list)


class BranchListView(BaseModel):
    """分支列表（``branches.list`` 的出参信封；Port 层拆壳成 ``list[BranchView]``）。"""

    model_config = ConfigDict(extra="forbid")

    branches: list[BranchView] = Field(default_factory=list)


class DeliveryView(BaseModel):
    """一条消息对某个收件人的投递记录（对齐 ``mm_deliveries``）。

    ``state`` 取数据库值，另加派生值 ``blocked``（``blocked_reason`` 非空时）。
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = 0
    delivery_id: str
    message_id: str = ""
    recipient_id: str = ""
    state: str = ""
    attempts: int = 0
    error: str | None = None
    consumed_node_id: str | None = None
    consumed_session_id: str | None = None
    target_session_id: str | None = None
    blocked_reason: str | None = None
    last_node_id: str | None = None
    resume_requested: bool = False


class MessageView(BaseModel):
    """一条消息及其（可见的）投递记录（对齐 ``mm_messages``）。

    收件人只能看到发给自己的投递记录——投影在读取层完成。
    """

    model_config = ConfigDict(extra="forbid")

    message_id: str
    project_id: str = ""
    sender_id: str = ""
    content: str = ""
    kind: Literal["request", "notification", "response"] = "notification"
    reply_to: str | None = None
    accepted_at: float = 0.0
    origin_node_id: str | None = None
    sender_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_sender: bool = False
    deliveries: list[DeliveryView] = Field(default_factory=list)


class PublishedMessageView(BaseModel):
    """一次发布的结果引用（只回稳定消息 id）。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str


class MailboxSummaryView(BaseModel):
    """信箱概览（``mailbox.summary``）。"""

    model_config = ConfigDict(extra="forbid")

    availability: Literal["available", "empty"] = "available"
    inbox_counts: dict[str, int] = Field(default_factory=dict)
    outbox_count: int = 0


class AgentView(BaseModel):
    """一个在线 Agent 的定义与状态（``agents.get``）。

    ``available_ability_types`` / ``context_usage`` 与 CoreUnit
    ``get_agent_info`` 的回执同源：C3 端口化把它们从回执里丢掉了，而观察端
    仍依赖这两个字段（用量快照恢复、能力弹窗）。这里作为可选字段补齐；
    ``context_usage`` 为 None 表示该单元暂无可恢复的用量快照。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    abilities: list[str] = Field(default_factory=list)
    available_ability_types: list[str] = Field(default_factory=list)
    context_usage: dict[str, Any] | None = None


class AgentRuntimeView(BaseModel):
    """Agent 的运行时状态（``agents.runtime``），对齐单元的实体运行时快照。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    availability: Literal["online", "offline"] = "offline"
    incarnation_id: str | None = None
    busy: bool | None = None
    loaded_abilities: list[str] | None = None


class ToolView(BaseModel):
    """Agent 当前装载的一个工具（``agents.tools``）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    alias: str = ""
    member: str = ""


class ToolListView(BaseModel):
    """工具列表信封（Port 层拆壳成 ``list[ToolView]``）。"""

    model_config = ConfigDict(extra="forbid")

    tools: list[ToolView] = Field(default_factory=list)


class AgentSpawnSpec(BaseModel):
    """创建一个 Agent 所需的完整规格（对应 Core 的 spawn 载荷）。

    这是模板解析后的产物：模板层（template_id/revision/ability_request/
    permission_request）不在这里，先把模板解析成这份规格再创建。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    agent_id: str = ""
    manifest_ref: str = ""
    endpoint_name: str | None = None
    description: str = ""
    system_prompt: str = ""
    max_iterations: int = 10
    communication_timeout: float = 300.0
    window: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    model_overrides: dict[str, Any] | None = None
    validate_model: bool = False


class AgentSurfaceSpec(BaseModel):
    """Agent 工具表面覆盖，对应 Core 的 ``SurfaceConfig``。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "static", "progressive"] = "auto"
    static: list[str] = Field(default_factory=list)
    max_static: int | None = None


class AgentAssemblyModel(BaseModel):
    """装配输入的模型选择：端点引用 + 请求参数覆盖。"""

    model_config = ConfigDict(extra="forbid")

    endpoint_name: str = Field(min_length=1)
    model_overrides: dict[str, Any] | None = None


class AgentAssemblyPrompt(BaseModel):
    """装配输入的提示词。新建时由 Subject 保证非空。"""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1)


class AgentAssemblyLimits(BaseModel):
    """装配输入的迭代与通信上限；``-1`` 表示不限制。"""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = Field(ge=-1)
    communication_timeout: float = Field(ge=-1)


class AgentAssemblyContext(BaseModel):
    """装配输入的上下文窗口与持久化覆盖。

    ``persistence`` 只承载模板三键（``type``/``compress``/``auto_persist``）；
    ``root_dir`` / ``run_id`` 由 Subject 渲染时补齐。
    """

    model_config = ConfigDict(extra="forbid")

    window: dict[str, Any] | None = None
    persistence: dict[str, Any] | None = None


class AgentAssemblyPermission(BaseModel):
    """该 Agent 的 HITL 覆盖；部署策略是底线，只能收紧。"""

    model_config = ConfigDict(extra="forbid")

    auto_approve: list[str] = Field(default_factory=list)
    require_approval_by_default: bool = True
    safe_extra_commands: list[str] = Field(default_factory=list)
    safe_extra_sub_commands: list[tuple[str, list[str]]] | None = None


class AgentAssemblyConfig(BaseModel):
    """装配输入的配置段：模型、提示词、上限、上下文、表面与审批覆盖。"""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    model: AgentAssemblyModel
    prompt: AgentAssemblyPrompt
    limits: AgentAssemblyLimits
    context: AgentAssemblyContext | None = None
    surface: AgentSurfaceSpec | None = None
    mcp_refs: list[str] = Field(default_factory=list)
    permission: AgentAssemblyPermission | None = None


class AgentMemberPermissions(BaseModel):
    """成员级 L2 申请（只能收紧包声明）。

    九个维度**全部可空**：``null`` 表示「本成员未就该维度提出申请」，Core 据此
    沿用包声明上限；显式的 ``false`` 才是收紧。用 ``null`` 而不是默认值，是为了
    让 ``PermissionFlags.model_fields_set`` 的语义能跨 wire 保留。
    """

    model_config = ConfigDict(extra="forbid")

    allowed_paths: list[str] | None = None
    denied_paths: list[str] | None = None
    require_hitl: bool | None = None
    fs_read_only: bool | None = None
    fs_write: bool | None = None
    shell_access: bool | None = None
    net_access: bool | None = None
    allowed_commands: list[str] | None = None
    denied_commands: list[str] | None = None


class AgentMemberAxes(BaseModel):
    """成员的操作轴可见/可用开关。"""

    model_config = ConfigDict(extra="forbid")

    visible: bool = True
    available: bool = True


class AgentAssemblyMember(BaseModel):
    """一个已授予成员及其 L2 申请；未授予成员不进这里。

    ``whole_container`` 为真时 ``member`` 是**包名**（``namespace.name``），由 Core
    按自己的能力来源展开成该包的全部成员——「整包」需要包目录，而 Subject 侧没有目录，
    只能在 Core 侧展开。
    """

    model_config = ConfigDict(extra="forbid")

    member: str = Field(min_length=1)
    whole_container: bool = False
    permissions: AgentMemberPermissions | None = None
    axes: AgentMemberAxes | None = None


class AgentAssemblySpec(BaseModel):
    """``agents.create`` 的装配输入（v3）。

    ``agent_id`` 为空表示**新建**：``config`` 必填，``members`` 只含已授予成员。
    ``agent_id`` 非空表示**恢复**：``config`` 与 ``members`` 必须为空，配置来自
    Core 自己的实例快照，不从 Subject 重放模板。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    agent_id: str = ""
    config: AgentAssemblyConfig | None = None
    members: list[AgentAssemblyMember] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_mode_shape(self) -> AgentAssemblySpec:
        """恢复模式不带配置；新建模式必须有配置。"""
        if self.agent_id:
            if self.config is not None:
                raise ValueError("recovery mode must not carry config")
            if self.members:
                raise ValueError("recovery mode must not carry members")
        elif self.config is None:
            raise ValueError("create mode requires config")
        return self


class AgentStartView(BaseModel):
    """``agents.create`` 的就绪回执。返回即代表实例已可用。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str = ""
    recovery_mode: Literal["created", "resumed"] = "created"
    incarnation_id: str | None = None


class AgentConfigModelView(BaseModel):
    """``AgentConfigView`` 的模型段（生效值）。"""

    model_config = ConfigDict(extra="forbid")

    endpoint_name: str | None = None
    model_overrides: dict[str, Any] | None = None


class AgentConfigPromptView(BaseModel):
    """``AgentConfigView`` 的提示词段（生效值）。"""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = ""


class AgentConfigPersistenceView(BaseModel):
    """持久化生效值；``root_dir`` / ``run_id`` 由渲染期补齐。"""

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    compress: bool | None = None
    auto_persist: bool | None = None
    root_dir: str | None = None
    run_id: str | None = None


class AgentConfigContextView(BaseModel):
    """``AgentConfigView`` 的上下文段。"""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = -1
    communication_timeout: float = 300.0
    window: dict[str, Any] | None = None
    persistence: AgentConfigPersistenceView | None = None


class AgentConfigOperationView(BaseModel):
    """一个操作成员轴。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    visible: bool = True
    available: bool = True


class AgentConfigScriptView(BaseModel):
    """一个技能脚本轴；``exec`` 表示是否允许执行。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    visible: bool = True
    exec: bool = False


class AgentConfigSkillView(BaseModel):
    """一个技能成员及其脚本轴。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    visible: bool = True
    scripts: list[AgentConfigScriptView] = Field(default_factory=list)


class AgentConfigGrantView(BaseModel):
    """一组授权事实：按 alias 聚合的成员轴与硬沙盒根。"""

    model_config = ConfigDict(extra="forbid")

    alias: str = ""
    package: str = ""
    whole_container: bool = False
    operations: list[AgentConfigOperationView] = Field(default_factory=list)
    skills: list[AgentConfigSkillView] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)


class AgentConfigAbilityView(BaseModel):
    """``AgentConfigView`` 的能力段（授权事实，不是模板申请）。"""

    model_config = ConfigDict(extra="forbid")

    surface: AgentSurfaceSpec | None = None
    grants: list[AgentConfigGrantView] = Field(default_factory=list)
    mcp_refs: list[str] = Field(default_factory=list)


class AgentConfigPermissionView(BaseModel):
    """部署策略与该 Agent 覆盖合并后的有效审批策略。"""

    model_config = ConfigDict(extra="forbid")

    auto_approve: list[str] = Field(default_factory=list)
    require_approval_by_default: bool = True
    safe_extra_commands: list[str] = Field(default_factory=list)
    safe_extra_sub_commands: list[tuple[str, list[str]]] | None = None


class AgentConfigPathView(BaseModel):
    """已解析的路径事实（绝对根）。"""

    model_config = ConfigDict(extra="forbid")

    root: str
    access: Literal["read", "write"] = "read"
    deny: bool = False


class AgentConfigCoreOnlyView(BaseModel):
    """不由模板填充、只用于诊断的 Core 侧事实。"""

    model_config = ConfigDict(extra="forbid")

    workspace_root: str = ""
    resources: dict[str, Any] = Field(default_factory=dict)
    cluster_context_injection: bool = False
    environment_context_injection: bool = False


class AgentConfigView(BaseModel):
    """``agents.config(agent_id)`` 返回的只读配置事实。

    按 ``agent_id`` 取，**不含 ``project_id``**（Core 没有项目身份）；运行态仍走
    ``agents.runtime``。``ability.grants`` 是授权事实，模板里的申请不在这里回放。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str = ""
    description: str = ""
    model: AgentConfigModelView = Field(default_factory=AgentConfigModelView)
    prompt: AgentConfigPromptView = Field(default_factory=AgentConfigPromptView)
    context: AgentConfigContextView = Field(default_factory=AgentConfigContextView)
    ability: AgentConfigAbilityView = Field(default_factory=AgentConfigAbilityView)
    permission: AgentConfigPermissionView = Field(default_factory=AgentConfigPermissionView)
    paths: list[AgentConfigPathView] = Field(default_factory=list)
    core_only: AgentConfigCoreOnlyView | None = None


class AgentTemplateModel(BaseModel):
    """模板中的模型选择。"""

    model_config = ConfigDict(extra="forbid")

    endpoint_name: str = ""
    model_overrides: dict[str, Any] = Field(default_factory=dict)


class AgentTemplatePrompt(BaseModel):
    """模板中的提示词。"""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = ""


class AgentContextPolicy(BaseModel):
    """模板中的上下文策略。"""

    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 10
    communication_timeout: float = 300.0
    window: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None


class AgentAbilityRequest(BaseModel):
    """模板中的能力选择。

    形状已固定；**语义尚未实现**——带有实际选择时由 Core 明确拒绝，
    不静默忽略。
    """

    model_config = ConfigDict(extra="forbid")

    surface: dict[str, Any] | None = None
    packages: list[dict[str, Any]] = Field(default_factory=list)
    mcp_refs: list[str] = Field(default_factory=list)


class AgentPermissionRequest(BaseModel):
    """模板中的审批策略。

    形状已固定；**语义尚未实现**——带有实际策略时由 Core 明确拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    auto_approve: list[str] = Field(default_factory=list)
    require_approval_by_default: bool = True
    safe_extra_commands: list[str] = Field(default_factory=list)
    safe_extra_sub_commands: list[str] | None = None


class AgentTemplateSpec(BaseModel):
    """创建 Agent 的模板文档（比 spawn 规格高一层，需要解析）。

    解析产物是 :class:`AgentSpawnSpec`；``ability_request`` 与
    ``permission_request`` 目前只固定形状，语义未实现。
    """

    model_config = ConfigDict(extra="forbid")

    template_id: str = ""
    revision: int = 0
    title: str = ""
    description: str = ""
    name: str = ""
    manifest_ref: str = ""
    model: AgentTemplateModel | None = None
    prompt: AgentTemplatePrompt | None = None
    context_policy: AgentContextPolicy | None = None
    ability_request: AgentAbilityRequest | None = None
    permission_request: AgentPermissionRequest | None = None


class AgentSpecView(BaseModel):
    """解析后的 Agent 规格（``manifests.resolve``）。

    只描述「这个 manifest 声明了什么」，不落盘、不创建。
    """

    model_config = ConfigDict(extra="forbid")

    manifest_ref: str
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    abilities: list[str] = Field(default_factory=list)


class ContentReadResult(BaseModel):
    """能力成员内容分块（``abilities.content``）。

    与 ``ghrah.ability.content.ContentReadResult`` 字段一一对应——
    契约包里必须有一份自己的形状，协议不得依赖 Core 的实现包。
    """

    model_config = ConfigDict(extra="forbid")

    instance_id: str = ""
    version_digest: str = ""
    member: str = ""
    path: str = ""
    media_type: str = ""
    digest: str = ""
    total_bytes: int = 0
    offset: int = 0
    byte_count: int = 0
    next_offset: int | None = None
    encoding: Literal["utf-8", "base64"] = "utf-8"
    content: str = ""


class MessageIdListView(BaseModel):
    """消息 id 列表信封（Port 层拆壳成 ``list[MessageId]``）。"""

    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(default_factory=list)


class MessageReplyView(BaseModel):
    """一条同步消息的回复。"""

    model_config = ConfigDict(extra="forbid")

    content: str


class BroadcastReplyView(MessageReplyView):
    """广播中一名实际回复的 Agent。"""

    responder: str


class BroadcastRepliesView(BaseModel):
    """广播收到的回复。"""

    model_config = ConfigDict(extra="forbid")

    responses: list[BroadcastReplyView]


class AgentStatus(BaseModel):
    """Agent 的一次性状态快照——不是对象句柄。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    state: Literal["running", "stopped", "failed"] = "stopped"
    updated_at: str = ""
