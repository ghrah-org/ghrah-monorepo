# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 端口：Core 对外能力的唯一入口。

约定：

- 项目作用域由装配层注入，能力签名里不出现；
- 所有返回值都是 DTO、id 或 ``None``，不含 Core 内部对象；
- 所有失败都抛 ``ApiError`` 子类，载荷是 ``Problem``。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityInstancesView,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityPackageCatalogView,
    AbilityPackageDetailView,
    AbilityPackageGroupsView,
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
from ghrah.protocol.views.common import Page
from ghrah.protocol.views.core import (
    AgentRuntimeView,
    AgentSpawnSpec,
    AgentSpecView,
    AgentTemplateSpec,
    AgentView,
    BranchView,
    BroadcastRepliesView,
    ChainMetaView,
    ContentReadResult,
    DeliveryView,
    HistoryView,
    MailboxSummaryView,
    MessageReplyView,
    MessageView,
    NodeView,
    PublishedMessageView,
    SessionCatalogView,
    ToolView,
)

__all__ = [
    "AbilityOperations",
    "AgentId",
    "AgentOperations",
    "BranchId",
    "BranchOperations",
    "CorePort",
    "DeliveriesOperations",
    "DeliveryId",
    "MailboxOperations",
    "ManifestOperations",
    "MessageId",
    "NodeId",
    "NodeOperations",
    "RoomOperations",
    "SessionId",
    "SessionOperations",
]

type AgentId = str
type BranchId = str
type DeliveryId = str
type MessageId = str
type NodeId = str
type SessionId = str


class NodeOperations(Protocol):
    """链节点读取（不可变数据，返回快照）。"""

    async def get(self, *, agent_id: AgentId, node_id: NodeId) -> NodeView:
        """读取单个节点；不存在时抛 ``NOT_FOUND``。"""
        ...


class SessionOperations(Protocol):
    """会话目录、历史与元数据。"""

    async def catalog(self, *, agent_id: AgentId) -> SessionCatalogView:
        """读取会话目录与分支表；无链数据时返回空目录。"""
        ...

    async def history(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
        branch_id: BranchId,
        limit: int = -1,
    ) -> HistoryView:
        """读取某分支上的链历史；``limit=-1`` 表示全部。"""
        ...

    async def head(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
        branch_id: BranchId,
    ) -> NodeId:
        """读取某分支的当前头节点 id；分支不存在时抛 ``NOT_FOUND``。"""
        ...

    async def meta(self, *, agent_id: AgentId) -> ChainMetaView:
        """读取链目录元数据（active 指针 + 会话/分支表，不含节点）。"""
        ...

    async def create(
        self,
        *,
        agent_id: AgentId,
        name: str | None = None,
        system_prompt: str | None = None,
        origin_session_id: str | None = None,
        origin_node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionId:
        """新建会话并返回其 id。"""
        ...

    async def activate(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """把会话设为 active。"""
        ...

    async def archive(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """归档会话（保留数据，不再参与 active 选择）。"""
        ...

    async def delete(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """软删除会话。"""
        ...


class BranchOperations(Protocol):
    """分支读取。"""

    async def list(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
    ) -> list[BranchView]:
        """列出某会话下的分支；会话不存在时抛 ``NOT_FOUND``。"""
        ...

    async def create(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
        name: str,
        from_node_id: str | None = None,
        parent_branch_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BranchId:
        """从某节点（或当前头）分叉出新分支并返回其 id。"""
        ...

    async def activate(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """把分支设为会话的活动分支。"""
        ...

    async def archive(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """归档分支。"""
        ...

    async def delete(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """软删除分支。"""
        ...


class MailboxOperations(Protocol):
    """信箱读取（只读投影，替代消费方直连消息库）。"""

    async def summary(self, *, agent_id: AgentId) -> MailboxSummaryView:
        """读取信箱概览：各状态收件数 + 发出总数。"""
        ...

    async def list(
        self,
        *,
        agent_id: AgentId,
        inbox: bool = True,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[MessageView]:
        """分页列出收件箱（``inbox=True``）或发件箱；``cursor`` 为上一页的 ``next_cursor``。"""
        ...

    async def get(self, *, agent_id: AgentId, message_id: MessageId) -> MessageView:
        """读取本实体可见的单条消息（含投递记录）；不可见时抛 ``NOT_FOUND``。"""
        ...

    async def publish_external(
        self,
        *,
        sender: str,
        recipient_ids: Sequence[str],
        content: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedMessageView:
        """以外部（人）身份发布一条消息；``idempotency_key`` 保证重复提交幂等。"""
        ...

    async def publish_notification(
        self,
        *,
        sender_id: str,
        recipient_ids: Sequence[str] = (),
        content: str,
        kind: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedMessageView:
        """发布通知类消息，可指定收件人。"""
        ...

    async def recover_room_projections(self) -> int:
        """补齐缺失的 Room 投影日志，返回补齐条数。"""
        ...


class DeliveriesOperations(Protocol):
    """投递记录（读取与重试/恢复）。"""

    async def get(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> DeliveryView:
        """读取本实体可见的单条投递；不可见时抛 ``NOT_FOUND``。"""
        ...

    async def retry(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """重试一条失败投递；不可见时抛 ``NOT_FOUND``。"""
        ...

    async def resume(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """恢复一条中断投递；不可见时抛 ``NOT_FOUND``。"""
        ...


class AbilityOperations(Protocol):
    """能力关系库读取（项目作用域）。

    ``abilities.tree`` 依赖活跃 run（视图含运行时装配状态），
    与 ``agents.runtime`` 一起留到在线批次；本组当前只有纯读的变更流。
    """

    async def tree(self, *, agent_id: AgentId) -> AbilityTreeView:
        """读取某 Agent 的能力树（需要该 Agent 在线）。"""
        ...

    async def changes(self, *, after_seq: int = 0, limit: int = 100) -> AbilityChangesPage:
        """读取关系库变更流（项目级，不按 Agent 过滤）。"""
        ...

    async def admin(self, *, agent_id: AgentId) -> AbilityAdminView:
        """装配某 Agent 的能力管理总览。"""
        ...

    async def content(
        self,
        *,
        agent_id: AgentId,
        alias: str,
        member: str,
        offset: int = 0,
        limit: int = 65536,
    ) -> ContentReadResult:
        """读取某授权下的成员内容；未授权抛 ``NOT_FOUND``。"""
        ...

    async def packages(self) -> AbilityPackageCatalogView:
        """已安装能力包目录（全局）。"""
        ...

    async def package_detail(self, *, namespace: str, name: str) -> AbilityPackageDetailView:
        """单个能力包详情（全局）；不存在抛 ``NOT_FOUND``。"""
        ...

    async def package_version_profile(
        self, *, namespace: str, name: str, version: str
    ) -> AbilityPackageVersionProfileView:
        """读取单个能力包版本的画像。"""
        ...

    async def package_mcp_probe(
        self, *, namespace: str, name: str, version: str, request: AbilityMcpProbeRequest
    ) -> AbilityMcpProbeView:
        """只读探测指定能力包版本声明的 MCP 服务。"""
        ...

    async def package_groups(
        self,
        *,
        agent_id: AgentId | None = None,
        scoped: bool = True,
    ) -> AbilityPackageGroupsView:
        """能力包分组视图；``scoped=False`` 时是全局视图（不绑定项目）。"""
        ...

    async def instances(self) -> AbilityInstancesView:
        """本项目下的能力实例清单。"""
        ...

    async def status(self) -> AbilityApiStatus:
        """读取能力管理面的可用性（项目级）。"""
        ...

    async def grant(
        self, *, agent_id: AgentId, change: GrantAbilityRequest
    ) -> AbilityTreeChangeResult:
        """授予能力；返回写后的视图与修订号。"""
        ...

    async def revoke(
        self, *, agent_id: AgentId, change: RevokeAbilityRequest
    ) -> AbilityTreeChangeResult:
        """撤销能力；返回写后的视图与修订号。"""
        ...

    async def enable_tool(
        self, *, agent_id: AgentId, change: BindToolRequest
    ) -> AbilityTreeChangeResult:
        """绑定工具；返回写后的视图与修订号。"""
        ...

    async def disable_tool(
        self, *, agent_id: AgentId, change: UnbindToolRequest
    ) -> AbilityTreeChangeResult:
        """解绑工具；返回写后的视图与修订号。"""
        ...


class AgentOperations(Protocol):
    """Agent 生命周期（创建/删除/查询在线实例）。"""

    async def create(self, *, spec: AgentSpawnSpec) -> AgentId:
        """创建并启动一个 Agent（同步完成激活）；失败抛 ``ApiError``。"""
        ...

    async def create_from_template(
        self,
        *,
        template: AgentTemplateSpec,
        agent_id: str | None = None,
    ) -> AgentId:
        """按模板创建 Agent；模板中尚未实现的策略抛 ``UNAVAILABLE``。"""
        ...

    async def get(self, *, agent_id: AgentId) -> AgentView:
        """读取在线 Agent 的定义与状态；不在线抛 ``NOT_FOUND``。"""
        ...

    async def delete(self, *, agent_id: AgentId) -> None:
        """停止并移除一个 Agent；不存在抛 ``NOT_FOUND``。"""
        ...

    async def runtime(self, *, agent_id: AgentId) -> AgentRuntimeView:
        """读取 Agent 的运行时快照（不在线时 ``availability="offline"``）。"""
        ...

    async def tools(self, *, agent_id: AgentId) -> list[ToolView]:
        """列出 Agent 当前装载的工具；不在线抛 ``NOT_FOUND``。"""
        ...

    async def compact(self, *, agent_id: AgentId) -> None:
        """触发一次上下文压缩（空闲时同步执行，忙碌时排队）。"""
        ...

    async def reset(self, *, agent_id: AgentId) -> None:
        """重置 Agent 的对话上下文。"""
        ...

    async def list(self) -> Page[AgentId]:
        """列出项目内在线 Agent 的 id。"""
        ...


class MessageOperations(Protocol):
    """消息投递（发送/广播/委派）。"""

    async def append(
        self,
        *,
        agent_id: AgentId,
        target: str,
        content: str,
        sender: str = "user",
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageReplyView:
        """向指定 Agent 发送消息并等待回复。"""
        ...

    async def broadcast(self, *, content: str, sender: str = "user") -> BroadcastRepliesView:
        """向项目内全部 Agent 广播。"""
        ...

    async def delegate(
        self,
        *,
        from_agent_id: AgentId,
        to_agent_id: AgentId,
        content: str,
        timeout: float | None = None,
    ) -> MessageReplyView:
        """把一个任务委派给另一个 Agent 并等待回复。"""
        ...


class RoomOperations(Protocol):
    """Room 投影维护。"""

    async def recover(self) -> None:
        """修复 Room 投影与消息表的不一致。"""
        ...


class ManifestOperations(Protocol):
    """manifest 解析（只解析，不落盘）。"""

    async def resolve(self, *, manifest_ref: str) -> AgentSpecView:
        """解析 manifest 引用；解析源不可用时抛 ``FAILED_PRECONDITION``。"""
        ...


class CorePort(Protocol):
    """Core 对外能力的唯一入口。

    当前落地项目读面（``nodes`` / ``sessions`` / ``branches``）；
    其余分组随各自阶段追加。

    作用域：装配层提供一个**未绑定**的端口实例，消费方按项目取绑定视图。
    这样同一实例可服务多个项目，避免「一个进程只能有一个项目」的隐含约束。
    """

    @property
    def nodes(self) -> NodeOperations: ...

    @property
    def sessions(self) -> SessionOperations: ...

    @property
    def branches(self) -> BranchOperations: ...

    @property
    def mailbox(self) -> MailboxOperations: ...

    @property
    def deliveries(self) -> DeliveriesOperations: ...

    @property
    def abilities(self) -> AbilityOperations: ...

    @property
    def agents(self) -> AgentOperations: ...

    @property
    def messages(self) -> MessageOperations: ...

    @property
    def rooms(self) -> RoomOperations: ...

    @property
    def manifests(self) -> ManifestOperations: ...

    async def stop_project(self) -> None:
        """停止绑定项目的 Core 单元并释放写侧资源。"""
        ...

    def project(self, project_id: str) -> CorePort:
        """返回绑定了项目作用域的端口视图。

        Args:
            project_id: 项目标识

        Returns:
            绑定后的端口；未绑定时调用能力会抛 ``FAILED_PRECONDITION``
        """
        ...
