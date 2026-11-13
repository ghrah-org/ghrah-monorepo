# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 能力清单与请求信封。

请求信封里的 ``project_id`` 由装配层注入，消费方传不进来——
所以信封是模块内部形状，不进契约包。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityInstancesView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
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
from ghrah.protocol.views.common import (
    AckView,
    AgentIdView,
    BranchIdView,
    DeliveryIdView,
    MessageIdView,
    NodeIdView,
    Page,
    SessionIdView,
)
from ghrah.protocol.views.core import (
    AgentRuntimeView,
    AgentSpawnSpec,
    AgentSpecView,
    AgentTemplateSpec,
    AgentView,
    BranchListView,
    ChainMetaView,
    ContentReadResult,
    DeliveryView,
    HistoryView,
    MailboxSummaryView,
    MessageIdListView,
    MessageView,
    NodeView,
    PublishedMessageView,
    SessionCatalogView,
    ToolListView,
)
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CORE_INTERFACES",
    "AbilitiesAdminRequest",
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

    project_id: str
    agent_id: str
    node_id: str


class SessionsCatalogRequest(BaseModel):
    """``sessions.catalog`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class SessionsHistoryRequest(BaseModel):
    """``sessions.history`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    branch_id: str
    limit: int = -1


class SessionsHeadRequest(BaseModel):
    """``sessions.head`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    branch_id: str


class SessionsMetaRequest(BaseModel):
    """``sessions.meta`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class BranchesListRequest(BaseModel):
    """``branches.list`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str


class MailboxSummaryRequest(BaseModel):
    """``mailbox.summary`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class MailboxListRequest(BaseModel):
    """``mailbox.list`` 的请求信封。

    ``cursor`` 是上一页的 ``next_cursor``（内部即递减序号）。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    inbox: bool = True
    limit: int = 50
    cursor: str | None = None


class MailboxGetRequest(BaseModel):
    """``mailbox.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    message_id: str


class MailboxPublishExternalRequest(BaseModel):
    """``mailbox.publish_external`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    sender: str
    recipient_ids: list[str] = Field(default_factory=list)
    content: str
    idempotency_key: str
    metadata: dict[str, Any] | None = None


class MailboxPublishNotificationRequest(BaseModel):
    """``mailbox.publish_notification`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    sender_id: str
    content: str
    kind: str = "notification"
    idempotency_key: str
    metadata: dict[str, Any] | None = None


class MailboxRecoverProjectionsRequest(BaseModel):
    """``mailbox.recover_room_projections`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class DeliveriesGetRequest(BaseModel):
    """``deliveries.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    delivery_id: str


class AbilitiesTreeRequest(BaseModel):
    """``abilities.tree`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class AbilitiesAdminRequest(BaseModel):
    """``abilities.admin`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class AbilitiesContentRequest(BaseModel):
    """``abilities.content`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    alias: str
    member: str
    offset: int = 0
    limit: int = 65536


class AbilitiesChangesRequest(BaseModel):
    """``abilities.changes`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    after_seq: int = 0
    limit: int = 100


class AgentCreateRequest(BaseModel):
    """``agents.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    spec: AgentSpawnSpec


class AgentCreateFromTemplateRequest(BaseModel):
    """``agents.create_from_template`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    template: AgentTemplateSpec
    agent_id: str | None = None


class AgentGetRequest(BaseModel):
    """``agents.get`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class AgentListRequest(BaseModel):
    """``agents.list`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class AgentDeleteRequest(BaseModel):
    """``agents.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class AgentRuntimeRequest(BaseModel):
    """``agents.runtime`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str


class SessionsCreateRequest(BaseModel):
    """``sessions.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    name: str | None = None
    system_prompt: str | None = None


class SessionsActivateRequest(BaseModel):
    """``sessions.activate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str


class SessionsArchiveRequest(BaseModel):
    """``sessions.archive`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str


class SessionsDeleteRequest(BaseModel):
    """``sessions.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str


class BranchesCreateRequest(BaseModel):
    """``branches.create`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    name: str
    from_node_id: str | None = None
    parent_branch_id: str | None = None


class BranchesActivateRequest(BaseModel):
    """``branches.activate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    branch_id: str


class BranchesArchiveRequest(BaseModel):
    """``branches.archive`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    branch_id: str


class BranchesDeleteRequest(BaseModel):
    """``branches.delete`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    session_id: str
    branch_id: str


class MessagesAppendRequest(BaseModel):
    """``messages.append`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    target: str
    content: str
    metadata: dict[str, Any] | None = None


class MessagesBroadcastRequest(BaseModel):
    """``messages.broadcast`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    content: str
    sender: str = ""


class MessagesDelegateRequest(BaseModel):
    """``messages.delegate`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    from_agent_id: str
    to_agent_id: str
    content: str
    timeout: float | None = None


class ManifestsResolveRequest(BaseModel):
    """``manifests.resolve`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    manifest_ref: str


class AbilitiesPackagesRequest(BaseModel):
    """``abilities.packages`` 的请求信封（全局读面，不绑定项目）。"""

    model_config = ConfigDict(extra="forbid")


class AbilitiesInstancesRequest(BaseModel):
    """``abilities.instances`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class AbilitiesPackageDetailRequest(BaseModel):
    """``abilities.package_detail`` 的请求信封（全局读面）。"""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    name: str


class AbilitiesPackageGroupsRequest(BaseModel):
    """``abilities.package_groups`` 的请求信封（全局视图可省略项目）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str = ""
    agent_id: str | None = None


class DeliveriesRetryRequest(BaseModel):
    """``deliveries.retry`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    delivery_id: str


class DeliveriesResumeRequest(BaseModel):
    """``deliveries.resume`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    delivery_id: str


class RoomsRecoverRequest(BaseModel):
    """``rooms.recover`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class AbilitiesStatusRequest(BaseModel):
    """``abilities.status`` 的请求信封（项目级，无 Agent）。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str


class AbilitiesGrantRequest(BaseModel):
    """``abilities.grant`` 的请求信封；``change`` 为协议里的授权请求本体。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    change: GrantAbilityRequest


class AbilitiesRevokeRequest(BaseModel):
    """``abilities.revoke`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    change: RevokeAbilityRequest


class AbilitiesEnableToolRequest(BaseModel):
    """``abilities.enable_tool`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    change: BindToolRequest


class AbilitiesDisableToolRequest(BaseModel):
    """``abilities.disable_tool`` 的请求信封。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    agent_id: str
    change: UnbindToolRequest


CORE_INTERFACES = InterfaceRegistry(
    (
        Interface("nodes.get", NodeGetRequest, NodeView),
        Interface("sessions.catalog", SessionsCatalogRequest, SessionCatalogView),
        Interface("sessions.history", SessionsHistoryRequest, HistoryView),
        Interface("sessions.head", SessionsHeadRequest, NodeIdView),
        Interface("sessions.meta", SessionsMetaRequest, ChainMetaView),
        Interface("branches.list", BranchesListRequest, BranchListView),
        Interface("mailbox.summary", MailboxSummaryRequest, MailboxSummaryView),
        Interface("mailbox.list", MailboxListRequest, Page[MessageView]),
        Interface("mailbox.get", MailboxGetRequest, MessageView),
        Interface("mailbox.publish_external", MailboxPublishExternalRequest, PublishedMessageView),
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
        Interface("deliveries.retry", DeliveriesRetryRequest, AckView),
        Interface("deliveries.resume", DeliveriesResumeRequest, AckView),
        Interface("rooms.recover", RoomsRecoverRequest, AckView),
        Interface("manifests.resolve", ManifestsResolveRequest, AgentSpecView),
        Interface("abilities.tree", AbilitiesTreeRequest, AbilityTreeView),
        Interface("abilities.changes", AbilitiesChangesRequest, AbilityChangesPage),
        Interface("abilities.admin", AbilitiesAdminRequest, AbilityAdminView),
        Interface("abilities.packages", AbilitiesPackagesRequest, AbilityPackageCatalogView),
        Interface(
            "abilities.package_detail",
            AbilitiesPackageDetailRequest,
            AbilityPackageDetailView,
        ),
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
        Interface("abilities.enable_tool", AbilitiesEnableToolRequest, AbilityTreeChangeResult),
        Interface("abilities.disable_tool", AbilitiesDisableToolRequest, AbilityTreeChangeResult),
        Interface("agents.create", AgentCreateRequest, AgentIdView),
        Interface("agents.create_from_template", AgentCreateFromTemplateRequest, AgentIdView),
        Interface("agents.get", AgentGetRequest, AgentView),
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
        Interface("messages.append", MessagesAppendRequest, MessageIdView),
        Interface("messages.broadcast", MessagesBroadcastRequest, MessageIdListView),
        Interface("messages.delegate", MessagesDelegateRequest, DeliveryIdView),
    )
)
"""Core 能力的唯一权威枚举。

它是 mypy 之外的第二道契约（运行时可查）、契约测试的分母、parity 测试的比对对象。
"""
