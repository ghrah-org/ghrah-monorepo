# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``nodes`` 分组的处理器。"""

from __future__ import annotations

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import NodeView

from ghrah.core.api.capabilities import NodeGetRequest
from ghrah.core.api.errors import CoreApiError
from ghrah.core.read.chain import ChainReadStore

__all__ = ["make_node_handlers"]


def make_node_handlers(store: ChainReadStore) -> dict[str, Handler]:
    """构造 ``nodes`` 分组处理器。

    Args:
        store: 只读链数据源

    Returns:
        能力名到处理器的映射
    """

    async def get(request: NodeGetRequest) -> NodeView:
        """读取单个节点。"""
        view = await store.node(
            project_id=request.project_id,
            agent_id=request.agent_id,
            node_id=request.node_id,
        )
        if view is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="nodes.get",
                    detail=(f"node {request.node_id!r} not found for agent {request.agent_id!r}"),
                ),
            )
        return view

    return {"nodes.get": get}
