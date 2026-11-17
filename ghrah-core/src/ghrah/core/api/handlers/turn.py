# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""轮次与审批查询能力。"""

from __future__ import annotations

from ghrah.protocol.interface import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import HitlRequestListView, HitlRequestView, TurnListView, TurnView

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.interfaces import (
    HitlGetRequest,
    HitlListRequest,
    TurnGetRequest,
    TurnListRequest,
)
from ghrah.core.read.turn import TurnReadStore


def make_turn_handlers(store: TurnReadStore) -> dict[str, Handler]:
    """将轮次查询装入端口处理器表。"""

    async def get(request: TurnGetRequest) -> TurnView:
        view = await store.turn(agent_id=request.agent_id, turn_id=request.turn_id)
        if view is None:
            raise CoreApiError(
                Problem.make(ProblemCode.NOT_FOUND, capability="turns.get", detail="Turn not found")
            )
        return view

    async def list_turns(request: TurnListRequest) -> TurnListView:
        return await store.turns(
            agent_id=request.agent_id,
            session_id=request.session_id,
            status=request.status,
            limit=request.limit,
        )

    return {"turns.get": get, "turns.list": list_turns}


def make_hitl_handlers(store: TurnReadStore) -> dict[str, Handler]:
    """将审批查询装入端口处理器表。"""

    async def get(request: HitlGetRequest) -> HitlRequestView:
        view = await store.hitl_request(agent_id=request.agent_id, promise_id=request.promise_id)
        if view is None:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.NOT_FOUND, capability="hitl.get", detail="HITL request not found"
                )
            )
        return view

    async def list_pending(request: HitlListRequest) -> HitlRequestListView:
        return await store.pending_hitl(agent_id=request.agent_id, session_id=request.session_id)

    return {"hitl.get": get, "hitl.list_pending": list_pending}
