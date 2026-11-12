# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``CorePort`` 的唯一实现。

门面刻意**很薄**：方法只做三件事——装箱 → 转交 → 建模型。
不含业务判断、不直接碰存储、不决定错误语义（那些在 handler 与 ``translate()`` 里）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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
from ghrah.protocol.ports.core import (
    AbilityOperations,
    AgentId,
    AgentOperations,
    BranchId,
    BranchOperations,
    CorePort,
    DeliveriesOperations,
    DeliveryId,
    MailboxOperations,
    ManifestOperations,
    MessageId,
    MessageOperations,
    NodeId,
    NodeOperations,
    RoomOperations,
    SessionId,
    SessionOperations,
)
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import (
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
    BranchView,
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
    ToolView,
)

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.runtime import CoreRuntime

__all__ = ["CoreFacade"]


class CoreFacade:
    """``CorePort`` 的实现，每个应用实例一个。

    Args:
        runtime: 模块内收敛点
        project_id: 项目作用域；``None`` 表示未绑定（只有 :meth:`project` 可调用）
        closer: 关闭内部资源的回调（只有装配层创建的根实例持有）
    """

    def __init__(
        self,
        *,
        runtime: CoreRuntime,
        project_id: str | None = None,
        closer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._project_id = project_id
        self._closer = closer
        self._nodes = _Nodes(self)
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

    def project(self, project_id: str) -> CorePort:
        """返回绑定了项目作用域的端口视图。

        视图是轻量对象：共享同一个收敛点与只读连接池，只额外记住 ``project_id``。
        连接池归根端口所有——关闭任一视图都会释放它（可再懒重开）。

        Args:
            project_id: 项目标识

        Returns:
            绑定后的端口
        """
        return CoreFacade(
            runtime=self._runtime,
            project_id=project_id,
            closer=self._closer,
        )

    # ── 生命周期 ──

    async def aclose(self) -> None:
        """释放内部资源（幂等；关闭任一视图等价于关闭共享连接池）。"""
        if self._closer is not None:
            await self._closer()

    # ── 内部 ──

    async def _call(self, capability: str, **kwargs: Any) -> dict[str, Any]:
        """装箱并转交给收敛点。

        Args:
            capability: 能力名
            **kwargs: 领域参数（``project_id`` 由这里注入，调用方从未参与）

        Returns:
            JSON 形状的出参

        Raises:
            CoreApiError: 端口未绑定项目作用域时抛 ``FAILED_PRECONDITION``
        """
        if self._project_id is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability=capability,
                    detail="capability requires a project-scoped port: call project(id) first",
                ),
            )
        return await self._runtime.invoke(
            capability,
            {"project_id": self._project_id, **kwargs},
        )

    async def _call_global(self, capability: str, **kwargs: Any) -> Any:
        """调用不绑定项目作用域的全局能力（如能力包目录）。"""
        return await self._runtime.invoke(capability, dict(kwargs))


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
    ) -> SessionId:
        """新建会话。"""
        raw = await self._facade._call(
            "sessions.create",
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
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
    ) -> BranchId:
        """分叉出新分支。"""
        raw = await self._facade._call(
            "branches.create",
            agent_id=agent_id,
            session_id=session_id,
            name=name,
            from_node_id=from_node_id,
            parent_branch_id=parent_branch_id,
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
    ) -> PublishedMessageView:
        """以外部（人）身份发布消息。"""
        raw = await self._facade._call(
            "mailbox.publish_external",
            sender=sender,
            recipient_ids=list(recipient_ids),
            content=content,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        return PublishedMessageView.model_validate(raw)

    async def publish_notification(
        self,
        *,
        sender_id: str,
        content: str,
        kind: str = "notification",
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedMessageView:
        """发布通知类消息。"""
        raw = await self._facade._call(
            "mailbox.publish_notification",
            sender_id=sender_id,
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
        metadata: dict[str, Any] | None = None,
    ) -> MessageId:
        """发送一条消息。"""
        raw = await self._facade._call(
            "messages.append",
            agent_id=agent_id,
            target=target,
            content=content,
            metadata=metadata,
        )
        return MessageIdView.model_validate(raw).message_id

    async def broadcast(self, *, content: str, sender: str = "") -> list[MessageId]:
        """广播。"""
        raw = await self._facade._call(
            "messages.broadcast",
            content=content,
            sender=sender,
        )
        return MessageIdListView.model_validate(raw).message_ids

    async def delegate(
        self,
        *,
        from_agent_id: AgentId,
        to_agent_id: AgentId,
        content: str,
        timeout: float | None = None,
    ) -> DeliveryId:
        """委派任务。"""
        raw = await self._facade._call(
            "messages.delegate",
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            content=content,
            timeout=timeout,
        )
        return DeliveryIdView.model_validate(raw).delivery_id


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

    async def packages(self) -> AbilityPackageCatalogView:
        """已安装能力包目录。"""
        raw = await self._facade._call_global("abilities.packages")
        return AbilityPackageCatalogView.model_validate(raw)

    async def package_detail(self, *, namespace: str, name: str) -> AbilityPackageDetailView:
        """单个能力包详情。"""
        raw = await self._facade._call_global(
            "abilities.package_detail",
            namespace=namespace,
            name=name,
        )
        return AbilityPackageDetailView.model_validate(raw)

    async def package_groups(
        self,
        *,
        agent_id: AgentId | None = None,
        scoped: bool = True,
    ) -> AbilityPackageGroupsView:
        """能力包分组视图；``scoped=False`` 走全局通道。"""
        call = self._facade._call if scoped else self._facade._call_global
        raw = await call("abilities.package_groups", agent_id=agent_id)
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
