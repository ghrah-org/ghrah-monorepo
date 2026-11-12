# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``branches`` 分组的处理器。"""

from __future__ import annotations

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import BranchListView

from ghrah.core.api.capabilities import BranchesListRequest
from ghrah.core.api.errors import CoreApiError
from ghrah.core.read.chain import ChainReadStore

__all__ = ["make_branch_handlers"]


def make_branch_handlers(store: ChainReadStore) -> dict[str, Handler]:
    """构造 ``branches`` 分组处理器。

    Args:
        store: 只读链数据源

    Returns:
        能力名到处理器的映射
    """

    async def list_branches(request: BranchesListRequest) -> BranchListView:
        """列出某会话下的分支。"""
        branches = await store.branches(
            project_id=request.project_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
        )
        if branches is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND,
                    capability="branches.list",
                    detail=(
                        f"session {request.session_id!r} not found for agent {request.agent_id!r}"
                    ),
                ),
            )
        return BranchListView(branches=branches)

    return {"branches.list": list_branches}
