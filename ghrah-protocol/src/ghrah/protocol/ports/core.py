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
from typing import Any, Literal, Protocol

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
from ghrah.protocol.shell_allow import ShellAllowValidationRequest, ShellBuiltinRulesView
from ghrah.protocol.views.common import Page
from ghrah.protocol.views.core import (
    AgentAssemblySpec,
    AgentConfigView,
    AgentRuntimeView,
    AgentSpawnSpec,
    AgentSpecView,
    AgentStartView,
    AgentTemplateSpec,
    AgentView,
    BranchView,
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
    ToolView,
    TurnAbortView,
    TurnListView,
    TurnRollbackPreviewView,
    TurnRollbackView,
    TurnView,
)
from ghrah.protocol.views.observer_room import TurnDetailView

__all__ = [
    "AbilityOperations",
    "AgentId",
    "AgentOperations",
    "BranchId",
    "BranchOperations",
    "CoreCatalogPort",
    "CorePort",
    "TurnOperations",
    "HitlOperations",
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
        content_blocks: Sequence[dict[str, Any]] | None = None,
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

    async def queue(self, *, agent_id: AgentId, session_id: str | None) -> DeliveryQueueView:
        """读取会话中未收敛的投递。"""
        ...

    async def cancel(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """取消尚未领取的排队投递。"""
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

    async def package_groups(
        self,
        *,
        agent_id: AgentId | None = None,
    ) -> AbilityPackageGroupsView:
        """本项目的能力包分组视图（可限定到某 Agent）；全局形态见目录端口。"""
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

    async def write_grant(
        self, *, agent_id: AgentId, change: GrantAbilityRequest
    ) -> AbilityGrantWriteView:
        """只写授权事实，不做运行投影。

        装配期专用（Agent 尚未注册时先落授权）：与 :meth:`grant` 的区别是
        **不读运行实例、不产出工具表投影**，因此只回报关系修订。写入来源记为
        ``assembly``，与管理员写入在审计上可区分。
        """
        ...

    async def write_revoke(
        self, *, agent_id: AgentId, change: RevokeAbilityRequest
    ) -> AbilityGrantWriteView:
        """只撤销授权事实，不做运行投影；用于创建失败时的补偿回滚。"""
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

    async def create_assembly(self, *, spec: AgentAssemblySpec) -> AgentStartView:
        """按装配输入 v3 创建或恢复 Agent；返回即代表实例已可用。

        ``spec.agent_id`` 为空时新建、非空时恢复（配置来自 Core 的实例快照）。
        与 :meth:`create` 并存到 T3-b：后者仍吃 ``manifest_ref``，本方法在
        创建成功后把配置写进 Core 自己的实例快照，此后不再回读模板。
        """
        ...

    async def start_assembly(self, *, agent_id: AgentId) -> AgentStartView:
        """从实例快照启动一个 Agent（幂等）。

        配置完全来自 Core 的实例快照，不接收模板；已在线时直接返回当前 incarnation。
        快照缺失抛 ``NOT_FOUND``。
        """
        ...

    async def stop_assembly(self, *, agent_id: AgentId) -> None:
        """停止一个 Agent 的进程，**保留**实例快照以便再次启动（幂等）。"""
        ...

    async def update_assembly(
        self, *, agent_id: AgentId, spec: AgentAssemblySpec
    ) -> AgentStartView:
        """用新的装配输入覆盖实例快照并应用（在线重启、离线下次启动生效）。

        ``spec`` 是新的配置事实；写入后快照即为权威，不再回读模板。
        """
        ...

    async def update_model(
        self,
        *,
        agent_id: AgentId,
        endpoint_name: str,
        model_overrides: dict[str, Any] | None = None,
    ) -> AgentStartView:
        """只改实例快照里的模型段（端点引用 + 请求参数），其余配置保持现值。

        在线实例重启生效；离线时只落库，下次启动生效。对应旧的
        ``model.configure`` 操作。
        """
        ...

    async def delete_assembly(self, *, agent_id: AgentId) -> None:
        """停止（若在线）并删除实例快照；不存在时幂等成功。

        与 :meth:`delete` 的区别：后者只要求在线、且不动快照。
        """
        ...

    async def get(self, *, agent_id: AgentId) -> AgentView:
        """读取在线 Agent 的定义与状态；不在线抛 ``NOT_FOUND``。"""
        ...

    async def config(self, *, agent_id: AgentId) -> AgentConfigView:
        """读取 Agent 的配置事实（只读）。

        与 :meth:`runtime` 分工：本方法返回配置事实（模型、提示词、上限、
        上下文、能力表面、审批策略、路径），运行态仍由 :meth:`runtime` 返回；
        **不含 ``project_id``**（Core 没有项目身份）。查无或实例快照缺失抛
        ``NOT_FOUND``，由调用方决定是否标记为丢失。
        """
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
        delivery_policy: Literal["inject", "queue"] = "queue",
        metadata: dict[str, Any] | None = None,
        content_blocks: Sequence[dict[str, Any]] | None = None,
    ) -> MessageReplyView | MessageAcceptanceView:
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


class TurnOperations(Protocol):
    """查询、预览和回滚轮次。"""

    async def get(self, *, agent_id: AgentId, turn_id: str) -> TurnView:
        """读取单个轮次。"""
        ...

    async def detail(self, *, agent_id: AgentId, turn_id: str) -> TurnDetailView:
        """读取所属轮次的节点、导航锚点与统计。"""
        ...

    async def list(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> TurnListView:
        """列出轮次。"""
        ...

    async def abort(
        self,
        *,
        agent_id: AgentId,
        turn_id: str | None = None,
        session_id: SessionId | None = None,
        reason: str = "",
    ) -> TurnAbortView:
        """中止显式指定或可唯一确定的活动轮次。"""
        ...

    async def rollback(
        self,
        *,
        agent_id: AgentId,
        turn_id: str,
        target: Literal["base", "node"] = "base",
        node_id: str | None = None,
    ) -> TurnRollbackView:
        """从指定轮次节点创建并激活回滚分支。"""
        ...

    async def rollback_preview(
        self,
        *,
        agent_id: AgentId,
        turn_id: str,
        target: Literal["base", "node"] = "base",
        node_id: str | None = None,
    ) -> TurnRollbackPreviewView:
        """只读计算回滚目标及其影响。"""
        ...


class HitlOperations(Protocol):
    """审批请求的只读访问。"""

    async def get(self, *, agent_id: AgentId, promise_id: str) -> HitlRequestView:
        """查询审批当前或历史状态。"""
        ...

    async def list_pending(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId | None = None,
    ) -> HitlRequestListView:
        """查询当前待审批请求。"""
        ...


class CorePort(Protocol):
    """Core 作用域端口：一个端口实例对应一个作用域。

    作用域由装配层在构造实例时注入，能力签名里不出现；
    全局读面（能力包目录等）见 :class:`CoreCatalogPort`。
    """

    @property
    def nodes(self) -> NodeOperations: ...

    @property
    def turns(self) -> TurnOperations: ...

    @property
    def hitl(self) -> HitlOperations: ...

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

    async def aclose(self) -> None:
        """关闭本端口实例（停单元 + 释放读面资源；幂等）。"""
        ...


class CoreCatalogPort(Protocol):
    """Core 全局目录端口（无作用域）。

    目录读面不打开数据库、不建单元，只读安装根与受信配置，
    因此在零作用域（尚未创建任何项目）时也可用。
    """

    async def validate_shell_allow(self, *, request: ShellAllowValidationRequest) -> None:
        """校验待保存的命令记忆；拒绝时抛 ApiError，不读写项目存储。"""
        ...

    async def shell_builtin_rules(self) -> ShellBuiltinRulesView:
        """读取 Core 内置放行规则的只读说明。"""
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
        self,
        *,
        namespace: str,
        name: str,
        version: str,
        request: AbilityMcpProbeRequest,
    ) -> AbilityMcpProbeView:
        """只读探测指定能力包版本声明的 MCP 服务。"""
        ...

    async def package_groups(self, *, agent_id: AgentId | None = None) -> AbilityPackageGroupsView:
        """能力包分组视图（全局形态，不绑定项目）。"""
        ...

    async def package_import(
        self, *, archive_path: str, source_name: str
    ) -> AbilityPackageImportView:
        """导入一个能力包归档。

        Args:
            archive_path: 归档在本机的绝对路径（调用方负责落地与清理）
            source_name: 用户看到的原始文件名，只用于识别格式与写进安装记录

        Returns:
            安装结果；解包、格式校验、安装与安装记录都由 Core 完成

        Raises:
            ApiError: 归档格式不支持、包内容不合法或安装失败
        """
        ...

    async def aclose(self) -> None:
        """关闭目录端口（不触碰数据库；幂等）。"""
        ...
