# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 处理器使用的存储查询契约。"""

from typing import Protocol

from ghrah.protocol.views.core import (
    BranchView,
    ChainMetaView,
    DeliveryQueueView,
    DeliveryView,
    HistoryView,
    HitlRequestListView,
    HitlRequestView,
    MailboxSummaryView,
    MessageView,
    NodeView,
    SessionCatalogView,
    TurnListView,
    TurnView,
)
from ghrah.protocol.views.observer_room import RoomFactsQuery, TranscriptFactsView, TurnDetailView


class ChainQueries(Protocol):
    """作用域内的链、会话与分支查询。"""

    async def node(self, *, agent_id: str, node_id: str) -> NodeView | None: ...

    async def catalog(self, *, agent_id: str) -> SessionCatalogView: ...

    async def meta(self, *, agent_id: str) -> ChainMetaView: ...

    async def branches(self, *, agent_id: str, session_id: str) -> list[BranchView] | None: ...

    async def head(self, *, agent_id: str, session_id: str, branch_id: str) -> str | None: ...

    async def history(
        self,
        *,
        agent_id: str,
        session_id: str,
        branch_id: str,
        limit: int = -1,
    ) -> HistoryView | None: ...


class TurnQueries(Protocol):
    """作用域内的轮次和审批查询。"""

    async def turn(self, *, agent_id: str, turn_id: str) -> TurnView | None: ...

    async def turns(
        self,
        *,
        agent_id: str,
        session_id: str | None,
        status: str | None,
        limit: int,
    ) -> TurnListView: ...

    async def detail(self, *, agent_id: str, turn_id: str) -> TurnDetailView | None: ...

    async def hitl_request(self, *, agent_id: str, promise_id: str) -> HitlRequestView | None: ...

    async def pending_hitl(
        self, *, agent_id: str, session_id: str | None
    ) -> HitlRequestListView: ...


class MailboxQueries(Protocol):
    """按可见实体查询收发信和顺序门。"""

    async def summary(self, *, agent_id: str) -> MailboxSummaryView: ...

    async def get_message(self, *, agent_id: str, message_id: str) -> MessageView: ...

    async def get_delivery(self, *, agent_id: str, delivery_id: str) -> DeliveryView: ...

    async def queue(self, *, agent_id: str, session_id: str | None) -> DeliveryQueueView: ...

    async def list_messages(
        self,
        *,
        agent_id: str,
        inbox: bool,
        limit: int,
        before_seq: int | None,
    ) -> tuple[list[MessageView], int | None]: ...


class TranscriptQueries(Protocol):
    """读取 Room 页引用的组合快照。"""

    async def facts(self, query: RoomFactsQuery) -> TranscriptFactsView: ...
