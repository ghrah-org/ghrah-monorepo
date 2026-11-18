# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""中止、失效回调与恢复分支的集成验收。"""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ghrah.protocol.problem import ProblemCode
from test_turn_rollback import append_node, send, snapshot

from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, ExecutionServices
from ghrah.ability.execution.executor import LocalAbilityExecutor
from ghrah.ability.execution.hooks import HookResult
from ghrah.ability.execution.invocation import MemberReference, OperationCall
from ghrah.communication.message_manager import _ATOMIC, _AtomicDelivery
from ghrah.context import ContextManager, turn
from ghrah.context.persistence import ContextChanges
from ghrah.core.api.errors import CoreApiError
from ghrah.core.events import HITLRequestEvent
from ghrah.core.message import AgentMessage

pytest_plugins = ["test_turn_rollback"]


@contextmanager
def atomic_scope(scope):
    token = _ATOMIC.set(scope)
    try:
        yield
    finally:
        _ATOMIC.reset(token)


async def new_approval(cm, promise="promise"):
    return await cm.open_hitl_request(
        promise_id=promise,
        ability_name="tool",
        tool_call_id="call",
        tool_call={},
        reason="approval",
        options={},
        deadline_at=None,
    )


async def test_abort_live_waiting_hitl_installs_recovery_and_releases_queue(
    rollback_runtime, monkeypatch
):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    entered = asyncio.Event()
    executor = LocalAbilityExecutor("display", hitl_timeout=60)
    requests = []

    async def publish(event):
        if isinstance(event, HITLRequestEvent):
            requests.append(event)
            entered.set()

    executor.update_event_publisher(SimpleNamespace(publish=publish))
    events = AsyncMock()
    monkeypatch.setattr(agent._event_publisher, "publish", events)
    calls = 0

    async def action(data):
        nonlocal calls
        calls += 1
        if calls == 2:
            await executor.handle_hitl_hook_result(
                HookResult(should_continue=False, requires_hitl=True),
                OperationCall(member=MemberReference(member="tool"), tool_call_id="call"),
                ExecutionServices({CONTEXT_MANAGER: cm}),
            )
        return {"results": []}

    monkeypatch.setattr(agent, "_action", action)
    monkeypatch.setattr(agent.config, "max_iterations", 2)
    opener = await send(mm, "opener")
    dispatch = asyncio.create_task(mm.dispatch_pending())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        turn_id = cm.active_turn_id
        head = cm.active_head
        assert (await port.turns.get(agent_id="agent", turn_id=turn_id)).status == "waiting_hitl"
        incoming = await mm.send_external(
            sender="user",
            recipient_ids=("agent",),
            content="injected",
            idempotency_key="inject",
            delivery_policy="inject",
            await_reply=False,
        )
        assert incoming.deliveries[0].turn_id == turn_id
        results = await asyncio.wait_for(
            asyncio.gather(
                port.turns.abort(agent_id="agent", turn_id=turn_id, reason="requested"),
                port.turns.abort(agent_id="agent", turn_id=turn_id, reason="requested"),
            ),
            3,
        )
        result = next(item for item in results if item.aborted)
        assert sum(item.aborted for item in results) == 1
        assert sum(item.already_terminal for item in results) == 1
        assert result.cancelled_hitl == result.released_deliveries == 1
        assert result.status == "aborted"
        assert result.closing_kind == "external_abort"
        assert result.recovery_branch_id and result.incomplete_node_id
        await asyncio.wait_for(dispatch, 3)
    finally:
        if not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)

    view = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert view.start_node_id == view.end_node_id == head.id
    assert view.end_branch_id == head.created_on_branch_id
    assert view.abort_reason == "requested"
    assert view.incomplete_node_id == result.incomplete_node_id
    assert view.recovery_branch_id == result.recovery_branch_id
    diagnosis = await backend.load_node("agent", view.incomplete_node_id)
    assert diagnosis.metadata["node_kind"] == "incomplete"
    assert diagnosis.metadata["phase"] == "waiting_hitl"
    assert diagnosis.ability_names == ["incomplete"] and not diagnosis.messages_delta
    assert diagnosis.turn_id == turn_id and diagnosis.parent_id == head.id
    branches = {item.branch_id: item for item in cm.list_branches(view.session_id)}
    assert branches[view.end_branch_id].head_node_id == diagnosis.id
    branch = branches[view.recovery_branch_id]
    assert branch.parent_branch_id == view.end_branch_id
    assert branch.fork_point_node_id == head.id
    assert branch.metadata["trigger"] == "external_abort"
    assert cm.get_active_session_branch().branch_id == branch.branch_id
    (old,) = await mm.get_deliveries(opener.message_id)
    assert old.state.value == "interrupted" and old.error == "aborted_by_user"
    assert not await mm.get_replies(opener.message_id)
    (later,) = await mm.get_deliveries(incoming.message_id)
    later_turn = await backend.load_turn("agent", later.turn_id)
    assert later_turn["base_node_id"] == head.id
    assert later_turn["turn_id"] != turn_id
    hitl = await port.hitl.get(agent_id="agent", promise_id=requests[0].promise_id)
    assert hitl.status == "cancelled"
    late = await cm.resolve_hitl_request(
        hitl.promise_id, approved=True, resolved_by="user", resolution={}
    )
    assert not late.changed and late.status == "cancelled"
    closed = [
        c.args[0]
        for c in events.await_args_list
        if c.args[0].event_type.value == "turn_closed" and c.args[0].turn_id == turn_id
    ]
    assert [(ev.status, ev.closing_kind) for ev in closed] == [("aborted", "external_abort")]
    for kind in ("branch_created", "branch_activated"):
        assert sum(c.args[0].event_type.value == kind for c in events.await_args_list) == 1


@pytest.mark.parametrize("with_mm", [False, True])
async def test_crash_scene_recovers_once_before_loading(rollback_runtime, with_mm):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    head = append_node(cm, "first")
    await cm.persist()
    await new_approval(cm)
    before = snapshot(backend.db_path)
    if with_mm:
        await mm.close()
        await mm.open(start_dispatcher=False)
        row = await backend.load_turn("agent", turn_id)
        assert row["status"] == "interrupted"
        assert json.loads(row["metadata"])["crash_diagnosis"]["last_correct_node_id"] == head.id
        marker_snapshot = snapshot(backend.db_path)
        assert marker_snapshot["nodes"] == before["nodes"]
        assert marker_snapshot["branches"] == before["branches"]
    recovered = ContextManager("agent", persistence=backend)
    await recovered.restore("agent")
    view = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert view.status == "interrupted" and view.end_node_id == head.id
    assert view.incomplete_node_id and view.recovery_branch_id
    assert recovered.active_head.id == head.id
    assert recovered.get_active_session_branch().branch_id == view.recovery_branch_id
    assert "crash_diagnosis" not in json.loads(
        (await backend.load_turn("agent", turn_id))["metadata"]
    )
    assert (await port.hitl.get(agent_id="agent", promise_id="promise")).status == "orphaned"
    after = snapshot(backend.db_path)
    await recovered.restore("agent")
    assert snapshot(backend.db_path) == after


async def test_late_atomic_writes_are_rejected_without_partial_effects(rollback_runtime):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    opener = await send(mm, "opener")
    opener_id = opener.deliveries[0].delivery_id
    turn_id = await cm.open_turn(opener_delivery_id=opener_id)
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, opener_id),
        )
    base = cm.active_head
    candidate = cm._commit_candidate()
    candidate.begin_iteration()
    node = candidate.commit_iteration()
    changes = ContextChanges(
        agent_name=cm.agent_name,
        nodes=(node,),
        sessions=(candidate.get_active_session(),),
        branches=(candidate.get_active_session_branch(),),
    )
    scope = _AtomicDelivery(mm, backend, opener_id, "agent", [], cm)
    result = await port.turns.abort(agent_id="agent", turn_id=turn_id)
    assert result.aborted and result.incomplete_node_id is None
    before = snapshot(backend.db_path)
    with pytest.raises(turn.TurnRevokedError):
        await mm._apply_atomic(scope, changes)
    with pytest.raises(turn.TurnRevokedError):
        await mm._complete_delivery(
            opener_id, AgentMessage(sender="agent", recipient="user", content="late")
        )
    with pytest.raises(turn.TurnRevokedError):
        await cm.commit_compact_node_async([])
    with atomic_scope(scope):
        with pytest.raises(turn.TurnRevokedError):
            await mm.prepare_send(
                recipient_ids=("agent",),
                content="late draft",
                idempotency_key="late",
                kind="notification",
            )
        with pytest.raises(turn.TurnRevokedError):
            await mm.wait_inline("late inline")
    with pytest.raises(turn.TurnRevokedError):
        await mm._finish_atomic(
            scope, AgentMessage(sender="agent", recipient="user", content="late")
        )
    assert cm.active_head.id == base.id
    assert snapshot(backend.db_path) == before


async def test_abort_defers_to_existing_result_and_reports_scope_errors(rollback_runtime):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    opener = await send(mm, "has-result")
    opener_id = opener.deliveries[0].delivery_id
    turn_id = await cm.open_turn(opener_delivery_id=opener_id)
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, opener_id),
        )
        await db.execute(
            "INSERT INTO mm_delivery_results (delivery_id,content,recorded_at) VALUES (?, 'ok', 1)",
            (opener_id,),
        )
        await turn.insert_turn_placeholder(
            db, turn.ActiveTurn("other", "agent", "other-session", None, "mm"), opened_at="now"
        )
    with pytest.raises(CoreApiError) as ambiguous:
        await port.turns.abort(agent_id="agent")
    assert ambiguous.value.problem.detail == "ambiguous_active_turn"
    with pytest.raises(CoreApiError) as wrong_session:
        await port.turns.abort(agent_id="agent", turn_id=turn_id, session_id="other-session")
    assert wrong_session.value.problem.code == ProblemCode.NOT_FOUND
    with pytest.raises(CoreApiError) as offline:
        await port.turns.abort(agent_id="missing", turn_id=turn_id)
    assert offline.value.problem.detail == "agent_not_online"
    before = snapshot(backend.db_path)
    receipt = await port.turns.abort(agent_id="agent", session_id=cm.active_session_id)
    assert receipt.result_pending and not receipt.aborted
    assert snapshot(backend.db_path) == before
    assert await mm.recover_completions() == 1
    assert (await mm.get_replies(opener.message_id))[0].content == "ok"
    receipt = await port.turns.abort(agent_id="agent", turn_id=turn_id)
    assert receipt.already_terminal and receipt.status == "closed"


async def test_turn_token_fences_old_iteration_after_attach(rollback_runtime):
    agent, _, _, _ = rollback_runtime
    cm = agent._context_manager
    cm.attach_turn(turn_id="old", session_id=cm.active_session_id, opener_delivery_id="first")
    cm.begin_iteration()
    cm.request_turn_abort("old")
    cm.attach_turn(turn_id="new", session_id=cm.active_session_id, opener_delivery_id="next")
    head = cm.active_head
    with pytest.raises(turn.TurnRevokedError):
        cm.commit_iteration()
    assert cm.active_head == head
    cm.ensure_iteration_closed()


@pytest.mark.parametrize("mode", ["pending_tools", "no_evidence", "failed_convergence"])
async def test_failure_converges_from_last_committed_member(rollback_runtime, monkeypatch, mode):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    completed_actions = 0

    async def action(data):
        nonlocal completed_actions
        completed_actions += 1
        if completed_actions > 1:
            raise RuntimeError("later action failed")
        metadata = {"pending_tool_call_ids": ["unfinished"]} if mode == "pending_tools" else {}
        return {"results": [], "llm_metadata": metadata}

    if mode == "failed_convergence":
        monkeypatch.setattr(cm, "converge_turn", AsyncMock(side_effect=RuntimeError("db busy")))
    monkeypatch.setattr(agent, "_action", action)
    monkeypatch.setattr(agent.config, "max_iterations", 2)
    before = snapshot(backend.db_path)
    request = await send(mm, "opener")
    await mm.dispatch_pending()
    (opener,) = await mm.get_deliveries(request.message_id)
    view = await port.turns.get(agent_id="agent", turn_id=opener.turn_id)
    assert opener.state.value == view.status == "failed"
    assert view.start_node_id == view.end_node_id == opener.last_node_id
    assert "later action failed" in opener.error
    after = snapshot(backend.db_path)
    if mode == "pending_tools":
        assert view.incomplete_node_id and view.recovery_branch_id
        diagnosis = await backend.load_node("agent", view.incomplete_node_id)
        assert diagnosis.metadata["pending_tool_call_ids"] == ["unfinished"]
        assert diagnosis.metadata["incomplete_reason"] == "error"
        assert diagnosis.parent_id == cm.active_head.id == view.end_node_id
        assert len(after["branches"]) == len(before["branches"]) + 1
    else:
        assert view.incomplete_node_id is view.recovery_branch_id is None
        assert len(after["branches"]) == len(before["branches"])
        assert cm.active_head.id == view.end_node_id
