# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``sessions`` 分组的处理器。"""

from __future__ import annotations

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import NodeIdView
from ghrah.protocol.views.core import ChainMetaView, HistoryView, SessionCatalogView

from ghrah.core.api.capabilities import (
    SessionsCatalogRequest,
    SessionsHeadRequest,
    SessionsHistoryRequest,
    SessionsMetaRequest,
)
from ghrah.core.api.errors import CoreApiError
from ghrah.core.read.chain import ChainReadStore

__all__ = ["make_session_handlers"]


def make_session_handlers(store: ChainReadStore) -> dict[str, Handler]:
    """构造 ``sessions`` 分组处理器。

    Args:
        store: 只读链数据源

    Returns:
        能力名到处理器的映射
    """

    async def catalog(request: SessionsCatalogRequest) -> SessionCatalogView:
        """读取会话目录与分支表。无链数据时返回空目录，不报错。"""
        return await store.catalog(
            project_id=request.project_id,
            agent_id=request.agent_id,
        )

    async def history(request: SessionsHistoryRequest) -> HistoryView:
        """读取某分支上的链历史。"""
        view = await store.history(
            project_id=request.project_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            branch_id=request.branch_id,
            limit=request.limit,
        )
        if view is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="sessions.history",
                    detail=(
                        f"session {request.session_id!r} not found for agent {request.agent_id!r}"
                    ),
                ),
            )
        return view

    async def head(request: SessionsHeadRequest) -> NodeIdView:
        """读取某分支的当前头节点 id。"""
        node_id = await store.head(
            project_id=request.project_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            branch_id=request.branch_id,
        )
        if not node_id:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="sessions.head",
                    detail=(
                        f"branch {request.branch_id!r} in session "
                        f"{request.session_id!r} not found for agent {request.agent_id!r}"
                    ),
                ),
            )
        return NodeIdView(node_id=node_id)

    async def meta(request: SessionsMetaRequest) -> ChainMetaView:
        """读取链目录元数据。无链数据时返回空表，不报错。"""
        return await store.meta(
            project_id=request.project_id,
            agent_id=request.agent_id,
        )

    return {
        "sessions.catalog": catalog,
        "sessions.history": history,
        "sessions.head": head,
        "sessions.meta": meta,
    }
