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

from pydantic import BaseModel, ConfigDict, Field

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
    """一个在线 Agent 的定义与状态（``agents.get``）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    project_id: str = ""
    name: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    abilities: list[str] = Field(default_factory=list)


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
