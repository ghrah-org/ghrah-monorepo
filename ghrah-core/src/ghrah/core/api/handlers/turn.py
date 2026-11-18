# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""轮次与审批查询能力。"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.interface import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import (
    HitlRequestListView,
    HitlRequestView,
    TurnListView,
    TurnRollbackPreviewView,
    TurnRollbackView,
    TurnView,
)

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.handlers.session_write import resolve_agent_name
from ghrah.core.api.interfaces import (
    HitlGetRequest,
    HitlListRequest,
    TurnGetRequest,
    TurnListRequest,
    TurnRollbackPreviewRequest,
    TurnRollbackRequest,
)
from ghrah.core.read.turn import TurnReadStore
from ghrah.core.units.registry import RuntimeRegistry

_ROLLBACK_CODES = {
    "turn_not_found": ProblemCode.NOT_FOUND,
    "rollback_target_not_found": ProblemCode.NOT_FOUND,
    "turn_active": ProblemCode.BUSY,
    "session_has_active_turn": ProblemCode.BUSY,
    "turn_busy": ProblemCode.BUSY,
    "already_rolled_back": ProblemCode.CONFLICT,
    "rollback_conflict": ProblemCode.CONFLICT,
}


def make_turn_handlers(store: TurnReadStore, agents: RuntimeRegistry) -> dict[str, Handler]:
    """将轮次操作装入端口处理器表。"""

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

    async def call_rollback(
        request: TurnRollbackRequest, *, capability: str, dry_run: bool
    ) -> dict[str, Any]:
        name = await resolve_agent_name(agents, request.agent_id)
        result = await agents.handle_command(
            command="turn_rollback",
            payload={**request.model_dump(), "agent_name": name, "dry_run": dry_run},
        )
        if not result.get("success"):
            reason = str(result.get("error") or "rejected")
            raise CoreApiError(
                Problem.make(
                    _ROLLBACK_CODES.get(reason, ProblemCode.FAILED_PRECONDITION),
                    capability=capability,
                    detail=reason,
                    details=result.get("details"),
                )
            )
        return dict(result["data"])

    async def rollback(request: TurnRollbackRequest) -> TurnRollbackView:
        data = await call_rollback(request, capability="turns.rollback", dry_run=False)
        return TurnRollbackView.model_validate(data)

    async def rollback_preview(request: TurnRollbackPreviewRequest) -> TurnRollbackPreviewView:
        data = await call_rollback(request, capability="turns.rollback_preview", dry_run=True)
        return TurnRollbackPreviewView.model_validate(data)

    return {
        "turns.get": get,
        "turns.list": list_turns,
        "turns.rollback": rollback,
        "turns.rollback_preview": rollback_preview,
    }


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
