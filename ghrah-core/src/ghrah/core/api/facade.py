# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``CorePort`` 的唯一实现。

门面刻意**很薄**：方法只做三件事——装箱 → 转交 → 建模型。
不含业务判断、不直接碰存储、不决定错误语义（那些在 handler 与 ``translate()`` 里）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from ghrah.protocol.ability_control import (
    AbilityAdminView,
    AbilityApiStatus,
    AbilityChangesPage,
    AbilityInstancesView,
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
from ghrah.protocol.ports.core import (
    AbilityOperations,
    AgentId,
    AgentOperations,
    BranchId,
    BranchOperations,
    DeliveriesOperations,
    DeliveryId,
    HitlOperations,
    MailboxOperations,
    ManifestOperations,
    MessageId,
    MessageOperations,
    NodeId,
    NodeOperations,
    RoomOperations,
    SessionId,
    SessionOperations,
    TurnOperations,
)
from ghrah.protocol.views.common import (
    AgentIdView,
    BranchIdView,
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
    ToolListView,
    ToolView,
    TurnAbortView,
    TurnListView,
    TurnRollbackPreviewView,
    TurnRollbackView,
    TurnView,
)

from ghrah.core.api.runtime import CoreRuntime

__all__ = ["CoreFacade"]


class CoreFacade:
    """``CorePort`` 的实现：一个实例对应一个作用域。

    ``scope_id`` 由装配层在构造时确定，对 Core 是不透明 token——它不出现在能力
    请求里，也不由调用方传入。无作用域的全局读面见 :class:`CoreCatalog`。

    Args:
        runtime: 模块内收敛点
        scope_id: 作用域 token
        closer: 关闭内部资源的回调（由装配层持有）
    """

    def __init__(
        self,
        *,
        runtime: CoreRuntime,
        scope_id: str,
        closer: Callable[[], Awaitable[None]] | None = None,
        registry: Any = None,
    ) -> None:
        self._runtime = runtime
        self._scope_id = scope_id
        self._closer = closer
        self._registry = registry
        self._nodes = _Nodes(self)
        self._turns = _Turns(self)
        self._hitl = _Hitl(self)
        self._sessions = _Sessions(self)
        self._branches = _Branches(self)
        self._mailbox = _Mailbox(self)
        self._deliveries = _Deliveries(self)
        self._rooms = _Rooms(self)
        self._manifests = _Manifests(self)
        self._abilities = _Abilities(self)
        self._agents = _Agents(self)
        self._messages = _Messages(self)

    # ── CorePort ──

    @property
    def nodes(self) -> NodeOperations:
        """链节点读取。"""
        return self._nodes

    @property
    def turns(self) -> TurnOperations:
        """轮次读取。"""
        return self._turns

    @property
    def hitl(self) -> HitlOperations:
        """待审批请求读取。"""
        return self._hitl

    @property
    def sessions(self) -> SessionOperations:
        """会话目录、历史与元数据。"""
        return self._sessions

    @property
    def branches(self) -> BranchOperations:
        """分支读取。"""
        return self._branches

    @property
    def mailbox(self) -> MailboxOperations:
        """信箱读取。"""
        return self._mailbox

    @property
    def deliveries(self) -> DeliveriesOperations:
        """投递记录。"""
        return self._deliveries

    @property
    def rooms(self) -> RoomOperations:
        """Room 投影维护。"""
        return self._rooms

    @property
    def manifests(self) -> ManifestOperations:
        """manifest 解析。"""
        return self._manifests

    @property
    def abilities(self) -> AbilityOperations:
        """能力关系库读取。"""
        return self._abilities

    @property
    def agents(self) -> AgentOperations:
        """Agent 生命周期。"""
        return self._agents

    @property
    def messages(self) -> MessageOperations:
        """消息投递。"""
        return self._messages

    # ── 生命周期 ──

    async def aclose(self) -> None:
        """释放内部资源（幂等）。"""
        if self._closer is not None:
            await self._closer()

    async def dispatch_command(
        self, command: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """把旧协议命令直达已启动的单元（过渡期回程通道）。

        供装配层转发 Observer 回程命令（如 ``hitl_response``）：不懒启动
        本作用域单元；单元未启动或按穿透约定返回 ``None`` 时返回 ``None``，
        由调用方继续扇出到其他作用域或兜底失败回执。命令成功被认领时
        返回单元的结果字典。

        Args:
            command: 命令名（``CommandType`` 的值字符串）
            payload: 命令载荷（作用域由单元在入口补齐）

        Returns:
            结果字典；未被认领时为 ``None``
        """
        if self._registry is None:
            return None
        return await self._registry.try_dispatch_command(command=command, payload=payload)

    # ── 内部 ──

    async def _call(self, capability: str, **kwargs: Any) -> dict[str, Any]:
        """装箱并转交给收敛点。

        Args:
            capability: 能力名
            **kwargs: 领域参数（作用域由实例在构造时确定，调用方从未参与）

        Returns:
            JSON 形状的出参
        """
        return await self._runtime.invoke(capability, dict(kwargs))


class _Turns:
    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def get(self, *, agent_id: AgentId, turn_id: str) -> TurnView:
        return TurnView.model_validate(
            await self._facade._call("turns.get", agent_id=agent_id, turn_id=turn_id)
        )

    async def list(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> TurnListView:
        return TurnListView.model_validate(
            await self._facade._call(
                "turns.list",
                agent_id=agent_id,
                session_id=session_id,
                status=status,
                limit=limit,
            )
        )

    async def abort(
        self,
        *,
        agent_id: AgentId,
        turn_id: str | None = None,
        session_id: SessionId | None = None,
        reason: str = "",
    ) -> TurnAbortView:
        """中止显式指定或可唯一确定的活动轮次。"""
        return TurnAbortView.model_validate(
            await self._facade._call(
                "turns.abort",
                agent_id=agent_id,
                turn_id=turn_id,
                session_id=session_id,
                reason=reason,
            )
        )

    async def rollback(
        self,
        *,
        agent_id: AgentId,
        turn_id: str,
        target: Literal["base", "node"] = "base",
        node_id: str | None = None,
    ) -> TurnRollbackView:
        return TurnRollbackView.model_validate(
            await self._facade._call(
                "turns.rollback",
                agent_id=agent_id,
                turn_id=turn_id,
                target=target,
                node_id=node_id,
            )
        )

    async def rollback_preview(
        self,
        *,
        agent_id: AgentId,
        turn_id: str,
        target: Literal["base", "node"] = "base",
        node_id: str | None = None,
    ) -> TurnRollbackPreviewView:
        return TurnRollbackPreviewView.model_validate(
            await self._facade._call(
                "turns.rollback_preview",
                agent_id=agent_id,
                turn_id=turn_id,
                target=target,
                node_id=node_id,
            )
        )


class _Hitl:
    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def get(self, *, agent_id: AgentId, promise_id: str) -> HitlRequestView:
        return HitlRequestView.model_validate(
            await self._facade._call("hitl.get", agent_id=agent_id, promise_id=promise_id)
        )

    async def list_pending(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId | None = None,
    ) -> HitlRequestListView:
        return HitlRequestListView.model_validate(
            await self._facade._call(
                "hitl.list_pending",
                agent_id=agent_id,
                session_id=session_id,
            )
        )


class _Nodes:
    """``nodes`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def get(self, *, agent_id: AgentId, node_id: NodeId) -> NodeView:
        """按 id 读取单个节点。"""
        raw = await self._facade._call("nodes.get", agent_id=agent_id, node_id=node_id)
        return NodeView.model_validate(raw)


class _Sessions:
    """``sessions`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def catalog(self, *, agent_id: AgentId) -> SessionCatalogView:
        """按 id 读取会话目录。"""
        raw = await self._facade._call("sessions.catalog", agent_id=agent_id)
        return SessionCatalogView.model_validate(raw)

    async def history(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
        branch_id: BranchId,
        limit: int = -1,
    ) -> HistoryView:
        """按 id 读取链历史。"""
        raw = await self._facade._call(
            "sessions.history",
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
            limit=limit,
        )
        return HistoryView.model_validate(raw)

    async def head(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
        branch_id: BranchId,
    ) -> NodeId:
        """读取某分支的当前头节点 id。"""
        raw = await self._facade._call(
            "sessions.head",
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
        )
        return NodeIdView.model_validate(raw).node_id

    async def meta(self, *, agent_id: AgentId) -> ChainMetaView:
        """读取链目录元数据。"""
        raw = await self._facade._call("sessions.meta", agent_id=agent_id)
        return ChainMetaView.model_validate(raw)

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
        """新建会话。"""
        raw = await self._facade._call(
            "sessions.create",
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
            origin_session_id=origin_session_id,
            origin_node_id=origin_node_id,
            metadata=metadata or {},
        )
        return SessionIdView.model_validate(raw).session_id

    async def activate(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """激活会话。"""
        await self._facade._call("sessions.activate", agent_id=agent_id, session_id=session_id)

    async def archive(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """归档会话。"""
        await self._facade._call("sessions.archive", agent_id=agent_id, session_id=session_id)

    async def delete(self, *, agent_id: AgentId, session_id: SessionId) -> None:
        """软删除会话。"""
        await self._facade._call("sessions.delete", agent_id=agent_id, session_id=session_id)


class _Branches:
    """``branches`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def list(
        self,
        *,
        agent_id: AgentId,
        session_id: SessionId,
    ) -> list[BranchView]:
        """列出某会话下的分支。"""
        raw = await self._facade._call(
            "branches.list",
            agent_id=agent_id,
            session_id=session_id,
        )
        return BranchListView.model_validate(raw).branches

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
        """分叉出新分支。"""
        raw = await self._facade._call(
            "branches.create",
            agent_id=agent_id,
            session_id=session_id,
            name=name,
            from_node_id=from_node_id,
            parent_branch_id=parent_branch_id,
            metadata=metadata or {},
        )
        return BranchIdView.model_validate(raw).branch_id

    async def activate(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """激活分支。"""
        await self._facade._call(
            "branches.activate",
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
        )

    async def archive(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """归档分支。"""
        await self._facade._call(
            "branches.archive",
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
        )

    async def delete(
        self, *, agent_id: AgentId, session_id: SessionId, branch_id: BranchId
    ) -> None:
        """软删除分支。"""
        await self._facade._call(
            "branches.delete",
            agent_id=agent_id,
            session_id=session_id,
            branch_id=branch_id,
        )


class _Mailbox:
    """``mailbox`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def summary(self, *, agent_id: AgentId) -> MailboxSummaryView:
        """读取信箱概览。"""
        raw = await self._facade._call("mailbox.summary", agent_id=agent_id)
        return MailboxSummaryView.model_validate(raw)

    async def list(
        self,
        *,
        agent_id: AgentId,
        inbox: bool = True,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[MessageView]:
        """分页列出收件箱/发件箱。"""
        raw = await self._facade._call(
            "mailbox.list",
            agent_id=agent_id,
            inbox=inbox,
            limit=limit,
            cursor=cursor,
        )
        return Page[MessageView].model_validate(raw)

    async def get(self, *, agent_id: AgentId, message_id: MessageId) -> MessageView:
        """读取单条可见消息。"""
        raw = await self._facade._call(
            "mailbox.get",
            agent_id=agent_id,
            message_id=message_id,
        )
        return MessageView.model_validate(raw)

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
        """以外部（人）身份发布消息。"""
        raw = await self._facade._call(
            "mailbox.publish_external",
            sender=sender,
            recipient_ids=list(recipient_ids),
            content=content,
            idempotency_key=idempotency_key,
            metadata=metadata,
            content_blocks=content_blocks,
        )
        return PublishedMessageView.model_validate(raw)

    async def publish_notification(
        self,
        *,
        sender_id: str,
        recipient_ids: Sequence[str] = (),
        content: str,
        kind: str = "notification",
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedMessageView:
        """发布通知类消息。"""
        raw = await self._facade._call(
            "mailbox.publish_notification",
            sender_id=sender_id,
            recipient_ids=list(recipient_ids),
            content=content,
            kind=kind,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        return PublishedMessageView.model_validate(raw)

    async def recover_room_projections(self) -> None:
        """补齐 Room 投影日志。"""
        await self._facade._call("mailbox.recover_room_projections")


class _Deliveries:
    """``deliveries`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def get(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> DeliveryView:
        """读取单条可见投递。"""
        raw = await self._facade._call(
            "deliveries.get",
            agent_id=agent_id,
            delivery_id=delivery_id,
        )
        return DeliveryView.model_validate(raw)

    async def queue(self, *, agent_id: AgentId, session_id: str | None) -> DeliveryQueueView:
        """读取会话队列及活动轮次。"""
        return DeliveryQueueView.model_validate(
            await self._facade._call(
                "deliveries.queue",
                agent_id=agent_id,
                session_id=session_id,
            )
        )

    async def cancel(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """取消未领取的排队投递。"""
        await self._facade._call("deliveries.cancel", agent_id=agent_id, delivery_id=delivery_id)

    async def retry(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """重试一条失败投递。"""
        await self._facade._call(
            "deliveries.retry",
            agent_id=agent_id,
            delivery_id=delivery_id,
        )

    async def resume(self, *, agent_id: AgentId, delivery_id: DeliveryId) -> None:
        """恢复一条中断投递。"""
        await self._facade._call(
            "deliveries.resume",
            agent_id=agent_id,
            delivery_id=delivery_id,
        )


class _Manifests:
    """``manifests`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def resolve(self, *, manifest_ref: str) -> AgentSpecView:
        """解析 manifest 引用。"""
        raw = await self._facade._call("manifests.resolve", manifest_ref=manifest_ref)
        return AgentSpecView.model_validate(raw)


class _Rooms:
    """``rooms`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def recover(self) -> None:
        """修复 Room 投影。"""
        await self._facade._call("rooms.recover")


class _Messages:
    """``messages`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

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
        """发送一条消息。"""
        raw = await self._facade._call(
            "messages.append",
            agent_id=agent_id,
            target=target,
            content=content,
            sender=sender,
            timeout=timeout,
            delivery_policy=delivery_policy,
            metadata=metadata,
            content_blocks=content_blocks,
        )
        if delivery_policy == "inject":
            return MessageAcceptanceView.model_validate(raw)
        return MessageReplyView.model_validate(raw)

    async def broadcast(self, *, content: str, sender: str = "user") -> BroadcastRepliesView:
        """广播。"""
        raw = await self._facade._call(
            "messages.broadcast",
            content=content,
            sender=sender,
        )
        return BroadcastRepliesView.model_validate(raw)

    async def delegate(
        self,
        *,
        from_agent_id: AgentId,
        to_agent_id: AgentId,
        content: str,
        timeout: float | None = None,
    ) -> MessageReplyView:
        """委派任务并等待回复。"""
        raw = await self._facade._call(
            "messages.delegate",
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            content=content,
            timeout=timeout,
        )
        return MessageReplyView.model_validate(raw)


class _Agents:
    """``agents`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def create(self, *, spec: AgentSpawnSpec) -> AgentId:
        """创建并启动一个 Agent。"""
        raw = await self._facade._call("agents.create", spec=spec.model_dump(mode="json"))
        return AgentIdView.model_validate(raw).agent_id

    async def create_from_template(
        self,
        *,
        template: AgentTemplateSpec,
        agent_id: str | None = None,
    ) -> AgentId:
        """按模板创建 Agent。"""
        raw = await self._facade._call(
            "agents.create_from_template",
            template=template.model_dump(mode="json"),
            agent_id=agent_id,
        )
        return AgentIdView.model_validate(raw).agent_id

    async def get(self, *, agent_id: AgentId) -> AgentView:
        """读取在线 Agent 的定义与状态。"""
        raw = await self._facade._call("agents.get", agent_id=agent_id)
        return AgentView.model_validate(raw)

    async def list(self) -> Page[AgentId]:
        """列出项目内在线 Agent 的 id。"""
        raw = await self._facade._call("agents.list")
        return Page[AgentId].model_validate(raw)

    async def delete(self, *, agent_id: AgentId) -> None:
        """停止并移除一个 Agent。"""
        await self._facade._call("agents.delete", agent_id=agent_id)

    async def runtime(self, *, agent_id: AgentId) -> AgentRuntimeView:
        """读取 Agent 运行时状态。"""
        raw = await self._facade._call("agents.runtime", agent_id=agent_id)
        return AgentRuntimeView.model_validate(raw)

    async def tools(self, *, agent_id: AgentId) -> list[ToolView]:
        """列出 Agent 当前装载的工具。"""
        raw = await self._facade._call("agents.tools", agent_id=agent_id)
        return ToolListView.model_validate(raw).tools

    async def compact(self, *, agent_id: AgentId) -> None:
        """触发上下文压缩。"""
        await self._facade._call("agents.compact", agent_id=agent_id)

    async def reset(self, *, agent_id: AgentId) -> None:
        """重置 Agent 上下文。"""
        await self._facade._call("agents.reset", agent_id=agent_id)


class _Abilities:
    """``abilities`` 分组的门面实现。"""

    def __init__(self, facade: CoreFacade) -> None:
        self._facade = facade

    async def tree(self, *, agent_id: AgentId) -> AbilityTreeView:
        """读取某 Agent 的能力树。"""
        raw = await self._facade._call("abilities.tree", agent_id=agent_id)
        return AbilityTreeView.model_validate(raw)

    async def admin(self, *, agent_id: AgentId) -> AbilityAdminView:
        """装配能力管理总览。"""
        raw = await self._facade._call("abilities.admin", agent_id=agent_id)
        return AbilityAdminView.model_validate(raw)

    async def content(
        self,
        *,
        agent_id: AgentId,
        alias: str,
        member: str,
        offset: int = 0,
        limit: int = 65536,
    ) -> ContentReadResult:
        """读取某授权下的成员内容。"""
        raw = await self._facade._call(
            "abilities.content",
            agent_id=agent_id,
            alias=alias,
            member=member,
            offset=offset,
            limit=limit,
        )
        return ContentReadResult.model_validate(raw)

    async def status(self) -> AbilityApiStatus:
        """读取能力管理面可用性。"""
        raw = await self._facade._call("abilities.status")
        return AbilityApiStatus.model_validate(raw)

    async def package_groups(self, *, agent_id: AgentId | None = None) -> AbilityPackageGroupsView:
        """作用域内的能力包分组视图（含实例事实）。"""
        raw = await self._facade._call("abilities.package_groups", agent_id=agent_id)
        return AbilityPackageGroupsView.model_validate(raw)

    async def instances(self) -> AbilityInstancesView:
        """本项目下的能力实例清单。"""
        raw = await self._facade._call("abilities.instances")
        return AbilityInstancesView.model_validate(raw)

    async def grant(
        self, *, agent_id: AgentId, change: GrantAbilityRequest
    ) -> AbilityTreeChangeResult:
        """授予能力。"""
        raw = await self._facade._call(
            "abilities.grant",
            agent_id=agent_id,
            change=change.model_dump(mode="json"),
        )
        return AbilityTreeChangeResult.model_validate(raw)

    async def revoke(
        self, *, agent_id: AgentId, change: RevokeAbilityRequest
    ) -> AbilityTreeChangeResult:
        """撤销能力。"""
        raw = await self._facade._call(
            "abilities.revoke",
            agent_id=agent_id,
            change=change.model_dump(mode="json"),
        )
        return AbilityTreeChangeResult.model_validate(raw)

    async def enable_tool(
        self, *, agent_id: AgentId, change: BindToolRequest
    ) -> AbilityTreeChangeResult:
        """绑定工具。"""
        raw = await self._facade._call(
            "abilities.enable_tool",
            agent_id=agent_id,
            change=change.model_dump(mode="json"),
        )
        return AbilityTreeChangeResult.model_validate(raw)

    async def disable_tool(
        self, *, agent_id: AgentId, change: UnbindToolRequest
    ) -> AbilityTreeChangeResult:
        """解绑工具。"""
        raw = await self._facade._call(
            "abilities.disable_tool",
            agent_id=agent_id,
            change=change.model_dump(mode="json"),
        )
        return AbilityTreeChangeResult.model_validate(raw)

    async def changes(self, *, after_seq: int = 0, limit: int = 100) -> AbilityChangesPage:
        """读取关系库变更流。"""
        raw = await self._facade._call(
            "abilities.changes",
            after_seq=after_seq,
            limit=limit,
        )
        return AbilityChangesPage.model_validate(raw)
