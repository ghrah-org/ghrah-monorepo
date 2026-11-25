# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 接口清单与请求信封。

请求信封里**没有任何作用域字段**：作用域由端口实例在构造时确定，
消费方与信封都不参与。因此信封是模块内部形状，不进契约包。
"""

from __future__ import annotations

from typing import Any, Literal, Self

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityGrantWriteView,
    AbilityInstancesView,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
    AbilityPackageImportView,
    AbilityPackageVersionProfileView,
    AbilityTreeChangeResult,
)
from ghrah.protocol.ability_tree import (
    AbilityTreeView,
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from ghrah.protocol.interface import Interface, InterfaceRegistry
from ghrah.protocol.payloads.content import InputContentBlock
from ghrah.protocol.shell_allow import (
    ShellAllowValidationRequest,
    ShellBuiltinRulesRequest,
    ShellBuiltinRulesView,
)
from ghrah.protocol.views.common import (
    AckView,
    AgentIdView,
    BranchIdView,
    NodeIdView,
    Page,
    SessionIdView,
)
from ghrah.protocol.views.core import (
    AgentAssemblySpec,
    AgentConfigView,
    AgentRuntimeView,
    AgentSpawnSpec,
    AgentSpecView,
    AgentStartView,
    AgentTemplateSpec,
    AgentView,
    BranchListView,
    BroadcastRepliesView,
    ChainMetaView,
    ContentReadResult,
    DeliveryQueueView,
    DeliveryView,
    HistoryView,
    HitlRequestListView,
    HitlRequestView,
    MailboxSummaryView,
    MessageAcceptanceView,
    MessageReplyView,
    MessageView,
    NodeView,
    PublishedMessageView,
    SessionCatalogView,
    ToolListView,
    TurnAbortView,
    TurnListView,
    TurnRollbackPreviewView,
    TurnRollbackView,
    TurnView,
)
from ghrah.protocol.views.observer_room import RoomFactsQuery, TranscriptFactsView, TurnDetailView
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

__all__ = [
    "CORE_CATALOG_INTERFACES",
    "CORE_INTERFACES",
    "AbilitiesAdminRequest",
    "AbilitiesPackageGroupsGlobalRequest",
    "AbilitiesChangesRequest",
    "AbilitiesContentRequest",
    "AbilitiesTreeRequest",
    "AgentCreateFromTemplateRequest",
    "AgentCreateRequest",
    "AgentDeleteRequest",
    "AgentGetRequest",
    "AgentListRequest",
    "AgentRuntimeRequest",
    "BranchesListRequest",
    "DeliveriesGetRequest",
    "DeliveriesResumeRequest",
    "DeliveriesRetryRequest",
    "MailboxGetRequest",
    "ManifestsResolveRequest",
    "MailboxListRequest",
    "MailboxSummaryRequest",
    "NodeGetRequest",
    "RoomsRecoverRequest",
    "SessionsCatalogRequest",
    "SessionsHeadRequest",
    "SessionsHistoryRequest",
    "SessionsMetaRequest",
]


class NodeGetRequest(BaseModel):
    """``nodes.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    node_id: str


class SessionsCatalogRequest(BaseModel):
    """``sessions.catalog`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class SessionsHistoryRequest(BaseModel):
    """``sessions.history`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    branch_id: str
    limit: int = -1


class SessionsHeadRequest(BaseModel):
    """``sessions.head`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    branch_id: str


class SessionsMetaRequest(BaseModel):
    """``sessions.meta`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class BranchesListRequest(BaseModel):
    """``branches.list`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str


class MailboxSummaryRequest(BaseModel):
    """``mailbox.summary`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class MailboxListRequest(BaseModel):
    """``mailbox.list`` 的请求信封。

    ``cursor`` 是上一页的 ``next_cursor``（内部即递减序号）。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    inbox: bool = True
    limit: int = 50
    cursor: str | None = None


class MailboxGetRequest(BaseModel):
    """``mailbox.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    message_id: str


class MailboxPublishExternalRequest(BaseModel):
    """``mailbox.publish_external`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    sender: str
    recipient_ids: list[str] = Field(default_factory=list)
    content: str
    idempotency_key: str
    metadata: dict[str, Any] | None = None
    content_blocks: list[InputContentBlock] | None = None
    delivery_policy: Literal["inject", "queue"] = "queue"
    await_reply: bool = True


class MailboxPublishNotificationRequest(BaseModel):
    """``mailbox.publish_notification`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    sender_id: str
    recipient_ids: list[str] = Field(default_factory=list)
    content: str
    kind: str = "notification"
    idempotency_key: str
    metadata: dict[str, Any] | None = None


class MailboxRecoverProjectionsRequest(BaseModel):
    """``mailbox.recover_room_projections`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")


class DeliveriesGetRequest(BaseModel):
    """``deliveries.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    delivery_id: str


class AbilitiesTreeRequest(BaseModel):
    """``abilities.tree`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class AbilitiesAdminRequest(BaseModel):
    """``abilities.admin`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class AbilitiesContentRequest(BaseModel):
    """``abilities.content`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    alias: str
    member: str
    offset: int = 0
    limit: int = 65536


class AbilitiesChangesRequest(BaseModel):
    """``abilities.changes`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    after_seq: int = 0
    limit: int = 100


class AgentCreateRequest(BaseModel):
    """``agents.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    spec: AgentSpawnSpec


class AgentCreateFromTemplateRequest(BaseModel):
    """``agents.create_from_template`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    template: AgentTemplateSpec
    agent_id: str | None = None


class AgentConfigRequest(BaseModel):
    """``agents.config`` 的请求信封：按 ``agent_id`` 读配置事实。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class AgentCreateAssemblyRequest(BaseModel):
    """``agents.create_assembly`` 的请求信封：装配输入 v3。

    与 ``agents.create``（旧 spawn 规格）并存到 T3-b；
    新链路只用这一条，配置由 Core 落实例快照。
    """

    model_config = ConfigDict(extra="forbid")

    spec: AgentAssemblySpec


class AgentAssemblyTargetRequest(BaseModel):
    """``agents.start_assembly`` / ``stop_assembly`` / ``delete_assembly`` 的请求信封。

    这些操作只认身份：配置一律来自 Core 的实例快照，不接收模板或装配输入。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)


class AgentAssemblyUpdateRequest(BaseModel):
    """``agents.update_assembly`` 的请求信封：用新的装配输入覆盖实例快照。

    ``agent_id`` 必须指向已有快照；``spec.config`` / ``spec.members`` 是**新的事实**，
    写入快照后应用到活实例（在线则重启，离线则只留下次启动生效）。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    spec: AgentAssemblySpec


class AgentModelUpdateRequest(BaseModel):
    """``agents.update_model`` 的请求信封：只改实例快照里的模型段。

    对应旧的 ``model.configure`` 操作：只携带端点引用与请求参数，其余配置
    （提示词、上限、上下文、能力面、路径）保持快照现值不动。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    endpoint_name: str = Field(min_length=1)
    model_overrides: dict[str, Any] | None = None


class AgentGetRequest(BaseModel):
    """``agents.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class AgentListRequest(BaseModel):
    """``agents.list`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")


class AgentDeleteRequest(BaseModel):
    """``agents.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class AgentRuntimeRequest(BaseModel):
    """``agents.runtime`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str


class SessionsCreateRequest(BaseModel):
    """``sessions.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str | None = None
    system_prompt: str | None = None
    origin_session_id: str | None = None
    origin_node_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionsActivateRequest(BaseModel):
    """``sessions.activate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str


class SessionsArchiveRequest(BaseModel):
    """``sessions.archive`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str


class SessionsDeleteRequest(BaseModel):
    """``sessions.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str


class BranchesCreateRequest(BaseModel):
    """``branches.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    name: str
    from_node_id: str | None = None
    parent_branch_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BranchesActivateRequest(BaseModel):
    """``branches.activate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    branch_id: str


class BranchesArchiveRequest(BaseModel):
    """``branches.archive`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    branch_id: str


class BranchesDeleteRequest(BaseModel):
    """``branches.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    branch_id: str


class MessagesAppendRequest(BaseModel):
    """``messages.append`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    target: str
    content: str
    sender: str = "user"
    timeout: float | None = None
    delivery_policy: Literal["inject", "queue"] = "queue"
    metadata: dict[str, Any] | None = None
    content_blocks: list[InputContentBlock] | None = None


class MessagesBroadcastRequest(BaseModel):
    """``messages.broadcast`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    content: str
    sender: str = "user"


class MessagesDelegateRequest(BaseModel):
    """``messages.delegate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    from_agent_id: str
    to_agent_id: str
    content: str
    timeout: float | None = None


class ManifestsResolveRequest(BaseModel):
    """``manifests.resolve`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    manifest_ref: str


class AbilitiesPackagesRequest(BaseModel):
    """``abilities.packages`` 的请求信封（全局读面，不绑定项目）。"""

    model_config = ConfigDict(extra="forbid")


class AbilitiesInstancesRequest(BaseModel):
    """``abilities.instances`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")


class AbilitiesPackageDetailRequest(BaseModel):
    """``abilities.package_detail`` 的请求信封（全局读面）。"""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str


class AbilitiesPackageVersionRequest(AbilitiesPackageDetailRequest):
    """指定接口包版本的全局请求。"""

    version: str


class AbilitiesPackageMcpProbeRequest(AbilitiesPackageVersionRequest):
    """指定接口包版本的 MCP 探测请求。"""

    request: AbilityMcpProbeRequest


class AbilitiesPackageGroupsRequest(BaseModel):
    """``abilities.package_groups`` 的请求信封（全局视图可省略项目）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None


class AbilitiesPackageImportRequest(BaseModel):
    """``abilities.package_import`` 的请求信封。

    归档已由 Subject 落成本机临时文件；Core 只认路径，不认字节。
    """

    model_config = ConfigDict(extra="forbid")

    archive_path: str
    source_name: str


class AbilitiesPackageGroupsGlobalRequest(BaseModel):
    """``abilities.package_groups`` 的目录侧请求信封（全局视图，无作用域）。"""

    model_config = ConfigDict(extra="forbid")


class DeliveriesQueueRequest(BaseModel):
    """查询指定会话的投递队列。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str | None


class DeliveriesCancelRequest(BaseModel):
    """取消一条尚未领取的排队投递。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    delivery_id: str


class DeliveriesRetryRequest(BaseModel):
    """``deliveries.retry`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    delivery_id: str


class DeliveriesResumeRequest(BaseModel):
    """``deliveries.resume`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    delivery_id: str


class RoomsRecoverRequest(BaseModel):
    """``rooms.recover`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")


class AbilitiesStatusRequest(BaseModel):
    """``abilities.status`` 的请求信封（项目级，无 Agent）。"""

    model_config = ConfigDict(extra="forbid")


class AbilitiesGrantRequest(BaseModel):
    """``abilities.grant`` 的请求信封；``change`` 为协议里的授权请求本体。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: GrantAbilityRequest


class AbilitiesRevokeRequest(BaseModel):
    """``abilities.revoke`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: RevokeAbilityRequest


class AbilitiesWriteGrantRequest(BaseModel):
    """``abilities.write_grant`` 的请求信封：只写事实，不做运行投影。

    形状与 :class:`AbilitiesGrantRequest` 相同，分开定义是为了让「写事实 ≠ 在线
    生效」这条语义出现在能力名与 docstring 上，而不是靠调用方记住布尔参数。
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: GrantAbilityRequest


class AbilitiesWriteRevokeRequest(BaseModel):
    """``abilities.write_revoke`` 的请求信封：只撤销事实，不做运行投影。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: RevokeAbilityRequest


class AbilitiesEnableToolRequest(BaseModel):
    """``abilities.enable_tool`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: BindToolRequest


class AbilitiesDisableToolRequest(BaseModel):
    """``abilities.disable_tool`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    change: UnbindToolRequest


class TurnGetRequest(BaseModel):
    """读取单个所属轮次。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    turn_id: str


class TurnListRequest(BaseModel):
    """按 Agent 列出轮次。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class TurnAbortRequest(BaseModel):
    """按 Agent、显式轮次或目标会话中止执行。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    turn_id: str | None = Field(default=None, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    reason: str = ""


class TurnRollbackRequest(BaseModel):
    """从终态轮次的起点前或轮内节点新建分支。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    turn_id: str = Field(min_length=1)
    target: Literal["base", "node"] = "base"
    node_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_target(self) -> Self:
        if (self.target == "node") != (self.node_id is not None):
            raise ValueError("node_id is required exactly when target is node")
        return self


class TurnRollbackPreviewRequest(TurnRollbackRequest):
    """使用同一目标契约请求只读预览。"""


class HitlGetRequest(BaseModel):
    """读取某条审批记录。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    promise_id: str


class HitlListRequest(BaseModel):
    """查询本 Agent 当前待审批项。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str | None = None


class MessageAppendResponse(RootModel[MessageReplyView | MessageAcceptanceView]):
    """同步回复或异步受理回执，按实际载荷校验。"""


CORE_INTERFACES = InterfaceRegistry(
    (
        Interface("nodes.get", NodeGetRequest, NodeView),
        Interface("turns.abort", TurnAbortRequest, TurnAbortView),
        Interface("turns.detail", TurnGetRequest, TurnDetailView),
        Interface("turns.get", TurnGetRequest, TurnView),
        Interface("turns.list", TurnListRequest, TurnListView),
        Interface("turns.rollback", TurnRollbackRequest, TurnRollbackView),
        Interface("turns.rollback_preview", TurnRollbackPreviewRequest, TurnRollbackPreviewView),
        Interface("hitl.get", HitlGetRequest, HitlRequestView),
        Interface("hitl.list_pending", HitlListRequest, HitlRequestListView),
        Interface("sessions.catalog", SessionsCatalogRequest, SessionCatalogView),
        Interface("sessions.history", SessionsHistoryRequest, HistoryView),
        Interface("sessions.head", SessionsHeadRequest, NodeIdView),
        Interface("sessions.meta", SessionsMetaRequest, ChainMetaView),
        Interface("branches.list", BranchesListRequest, BranchListView),
        Interface("mailbox.summary", MailboxSummaryRequest, MailboxSummaryView),
        Interface("mailbox.list", MailboxListRequest, Page[MessageView]),
        Interface("mailbox.get", MailboxGetRequest, MessageView),
        Interface("mailbox.publish_external", MailboxPublishExternalRequest, MessageAcceptanceView),
        Interface(
            "mailbox.publish_notification",
            MailboxPublishNotificationRequest,
            PublishedMessageView,
        ),
        Interface(
            "mailbox.recover_room_projections",
            MailboxRecoverProjectionsRequest,
            AckView,
        ),
        Interface("deliveries.get", DeliveriesGetRequest, DeliveryView),
        Interface("deliveries.queue", DeliveriesQueueRequest, DeliveryQueueView),
        Interface("deliveries.cancel", DeliveriesCancelRequest, AckView),
        Interface("deliveries.retry", DeliveriesRetryRequest, AckView),
        Interface("deliveries.resume", DeliveriesResumeRequest, AckView),
        Interface("rooms.recover", RoomsRecoverRequest, AckView),
        Interface("transcript.facts", RoomFactsQuery, TranscriptFactsView),
        Interface("manifests.resolve", ManifestsResolveRequest, AgentSpecView),
        Interface("abilities.tree", AbilitiesTreeRequest, AbilityTreeView),
        Interface("abilities.changes", AbilitiesChangesRequest, AbilityChangesPage),
        Interface("abilities.admin", AbilitiesAdminRequest, AbilityAdminView),
        Interface(
            "abilities.package_groups",
            AbilitiesPackageGroupsRequest,
            AbilityPackageGroupsView,
        ),
        Interface("abilities.instances", AbilitiesInstancesRequest, AbilityInstancesView),
        Interface("abilities.content", AbilitiesContentRequest, ContentReadResult),
        Interface("abilities.status", AbilitiesStatusRequest, AbilityApiStatus),
        Interface("abilities.grant", AbilitiesGrantRequest, AbilityTreeChangeResult),
        Interface("abilities.revoke", AbilitiesRevokeRequest, AbilityTreeChangeResult),
        Interface("abilities.write_grant", AbilitiesWriteGrantRequest, AbilityGrantWriteView),
        Interface("abilities.write_revoke", AbilitiesWriteRevokeRequest, AbilityGrantWriteView),
        Interface("abilities.enable_tool", AbilitiesEnableToolRequest, AbilityTreeChangeResult),
        Interface("abilities.disable_tool", AbilitiesDisableToolRequest, AbilityTreeChangeResult),
        Interface("agents.create", AgentCreateRequest, AgentIdView),
        Interface("agents.create_from_template", AgentCreateFromTemplateRequest, AgentIdView),
        Interface("agents.get", AgentGetRequest, AgentView),
        Interface("agents.config", AgentConfigRequest, AgentConfigView),
        Interface("agents.create_assembly", AgentCreateAssemblyRequest, AgentStartView),
        Interface("agents.start_assembly", AgentAssemblyTargetRequest, AgentStartView),
        Interface("agents.stop_assembly", AgentAssemblyTargetRequest, AckView),
        Interface("agents.update_assembly", AgentAssemblyUpdateRequest, AgentStartView),
        Interface("agents.delete_assembly", AgentAssemblyTargetRequest, AckView),
        Interface("agents.update_model", AgentModelUpdateRequest, AgentStartView),
        Interface("agents.list", AgentListRequest, Page[str]),
        Interface("agents.delete", AgentDeleteRequest, AckView),
        Interface("agents.runtime", AgentRuntimeRequest, AgentRuntimeView),
        Interface("agents.tools", AgentRuntimeRequest, ToolListView),
        Interface("agents.compact", AgentRuntimeRequest, AckView),
        Interface("agents.reset", AgentRuntimeRequest, AckView),
        Interface("sessions.create", SessionsCreateRequest, SessionIdView),
        Interface("sessions.activate", SessionsActivateRequest, AckView),
        Interface("sessions.archive", SessionsArchiveRequest, AckView),
        Interface("sessions.delete", SessionsDeleteRequest, AckView),
        Interface("branches.create", BranchesCreateRequest, BranchIdView),
        Interface("branches.activate", BranchesActivateRequest, AckView),
        Interface("branches.archive", BranchesArchiveRequest, AckView),
        Interface("branches.delete", BranchesDeleteRequest, AckView),
        Interface("messages.append", MessagesAppendRequest, MessageAppendResponse),
        Interface("messages.broadcast", MessagesBroadcastRequest, BroadcastRepliesView),
        Interface("messages.delegate", MessagesDelegateRequest, MessageReplyView),
    )
)
"""Core 接口的唯一权威枚举。

它是 mypy 之外的第二道契约（运行时可查）、契约测试的分母、parity 测试的比对对象。
"""

CORE_CATALOG_INTERFACES = InterfaceRegistry(
    (
        Interface("shell.validate_allow", ShellAllowValidationRequest, AckView),
        Interface("shell.builtin_rules", ShellBuiltinRulesRequest, ShellBuiltinRulesView),
        Interface("abilities.packages", AbilitiesPackagesRequest, AbilityPackageCatalogView),
        Interface(
            "abilities.package_detail",
            AbilitiesPackageDetailRequest,
            AbilityPackageDetailView,
        ),
        Interface(
            "abilities.package_version_profile",
            AbilitiesPackageVersionRequest,
            AbilityPackageVersionProfileView,
        ),
        Interface(
            "abilities.package_mcp_probe",
            AbilitiesPackageMcpProbeRequest,
            AbilityMcpProbeView,
        ),
        Interface(
            "abilities.package_groups",
            AbilitiesPackageGroupsGlobalRequest,
            AbilityPackageGroupsView,
        ),
        Interface(
            "abilities.package_import",
            AbilitiesPackageImportRequest,
            AbilityPackageImportView,
        ),
    )
)
"""Core 全局目录端口的能力清单（无作用域：只读安装根与受信配置，不打开数据库）。"""
