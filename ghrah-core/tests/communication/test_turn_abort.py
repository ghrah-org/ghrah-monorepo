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
from _helpers import ScriptedLLM
from ghrah.protocol.problem import ProblemCode
from test_turn_rollback import append_node, send, snapshot

from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, ExecutionServices
from ghrah.ability.execution.executor import LocalAbilityExecutor
from ghrah.ability.execution.hooks import HookResult
from ghrah.ability.execution.invocation import MemberReference, OperationCall
from ghrah.communication.message_manager import _ATOMIC, _AtomicDelivery
from ghrah.communication.storage import DeliveryExecution
from ghrah.context import ContextManager, turn
from ghrah.context.persistence import ContextChanges
from ghrah.context.persistence.sqlite_backend import SqliteBackend
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
        # 连续两次 open 幂等：标记不重复写，链不重复建。
        await mm.close()
        await mm.open(start_dispatcher=False)
        assert snapshot(backend.db_path) == marker_snapshot
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
    scope = _AtomicDelivery(mm, DeliveryExecution(opener_id, "agent", 0, turn_id), [], cm)
    result = await port.turns.abort(agent_id="agent", turn_id=turn_id)
    assert result.aborted and result.incomplete_node_id is None
    before = snapshot(backend.db_path)
    with pytest.raises(turn.TurnRevokedError):
        await mm._apply_atomic(scope, changes)
    with pytest.raises(turn.TurnRevokedError):
        await mm._complete_delivery(
            scope.execution, AgentMessage(sender="agent", recipient="user", content="late")
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
    async with SqliteBackend(backend.db_path) as other_backend:
        other = ContextManager("agent", persistence=other_backend)
        outcome = await other.abort_turn(turn_id=turn_id)
        assert outcome.result_pending and not outcome.changed
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
    observed_closures = []

    async def action(data):
        nonlocal completed_actions
        completed_actions += 1
        if completed_actions > 1:
            raise RuntimeError("later action failed")
        metadata = {"pending_tool_call_ids": ["unfinished"]} if mode == "pending_tools" else {}
        return {"results": [], "llm_metadata": metadata}

    async def publish(event):
        if event.event_type.value == "turn_closed":
            row = await backend.load_turn("agent", event.turn_id)
            observed_closures.append((event.turn_id, event.status, row["status"]))

    monkeypatch.setattr(agent._event_publisher, "publish", publish)
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
    assert observed_closures == [(opener.turn_id, "failed", "failed")]
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


async def test_routed_ability_revocation_leaves_no_retry_branch(rollback_runtime, monkeypatch):
    """中止是控制流：路由 ability 被吊销时不得创建 retry-* 分支或写链。"""
    agent, backend, _, _ = rollback_runtime
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    session_id = cm.active_session_id
    branches_before = len(cm.list_branches(session_id))
    db_before = snapshot(backend.db_path)["branches"]

    async def revoked_ability(*args, **kwargs):
        cm.request_turn_abort(turn_id)
        raise turn.TurnRevokedError("revoked during routed ability")

    monkeypatch.setitem(agent._abilities, "end_task", SimpleNamespace(name="end_task"))
    monkeypatch.setattr(agent, "_build_ability_call", lambda *args, **kwargs: (object(), object()))
    monkeypatch.setattr(agent._ability_executor, "execute_ability", revoked_ability)

    with pytest.raises(turn.TurnRevokedError):
        await agent._execute_routed_ability({}, "end_task")

    assert len(cm.list_branches(session_id)) == branches_before
    assert snapshot(backend.db_path)["branches"] == db_before


async def test_cross_process_abort_revokes_atomic_commit(rollback_runtime):
    """进程内吊销集看不到的跨进程中止，由 DB 权威层在提交事务中拦下。"""
    agent, backend, mm, _ = rollback_runtime
    cm = agent._context_manager
    opener = await send(mm, "cross-process")
    opener_id = opener.deliveries[0].delivery_id
    turn_id = await cm.open_turn(opener_delivery_id=opener_id)
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, opener_id),
        )
        await db.execute(
            "UPDATE turns SET status='aborted', closing_kind='external_abort', "
            "closed_at='2026-11-18T00:00:00Z' WHERE turn_id=?",
            (turn_id,),
        )
    # 模拟另一进程中止：内存吊销集并不知道该轮已中止。
    cm.check_turn_revoked()
    candidate = cm._commit_candidate()
    candidate.begin_iteration()
    node = candidate.commit_iteration()
    changes = ContextChanges(
        agent_name="agent",
        nodes=(node,),
        sessions=(candidate.get_active_session(),),
        branches=(candidate.get_active_session_branch(),),
    )
    scope = _AtomicDelivery(mm, DeliveryExecution(opener_id, "agent", 0, turn_id), [], cm)
    before = snapshot(backend.db_path)
    with pytest.raises(turn.TurnRevokedError):
        await mm._apply_atomic(scope, changes)
    assert snapshot(backend.db_path) == before


async def test_abort_without_local_settler_releases_next_delivery(rollback_runtime, monkeypatch):
    agent, backend, mm, _ = rollback_runtime
    entered = asyncio.Event()
    proceed = asyncio.Event()
    original = agent._ensure_llm
    late = ScriptedLLM(["late"])
    first_turn = None

    async def pause_first_model_call():
        nonlocal first_turn
        active_turn = agent._context_manager.active_turn_id
        if entered.is_set() and active_turn != first_turn:
            return await original()
        first_turn = active_turn
        entered.set()
        await proceed.wait()
        return late

    monkeypatch.setattr(agent, "_ensure_llm", pause_first_model_call)
    opener = await send(mm, "opener")
    await mm._scan()
    await asyncio.wait_for(entered.wait(), 3)
    later = await send(mm, "later")
    (claimed,) = await mm.get_deliveries(opener.message_id)
    async with SqliteBackend(backend.db_path) as other_backend:
        other = ContextManager("agent", persistence=other_backend)
        assert (await other.abort_turn(turn_id=claimed.turn_id)).changed
    worker = mm._workers["agent"]
    scan = mm._scan

    async def finish_revoked_worker_during_scan():
        saw_worker = await scan()
        # 旧 worker 在扫描跳过该 Agent 后结束，同步调度必须再检查一次。
        proceed.set()
        await worker
        return saw_worker

    monkeypatch.setattr(mm, "_scan", finish_revoked_worker_during_scan)
    await asyncio.wait_for(mm.dispatch_pending(), 3)

    (original_delivery,) = await mm.get_deliveries(opener.message_id)
    assert (original_delivery.state.value, original_delivery.error) == (
        "interrupted",
        "aborted_by_user",
    )
    assert (original_delivery.turn_id, original_delivery.attempts) == (claimed.turn_id, 1)
    assert await mm.get_replies(opener.message_id) == ()
    assert (await mm.get_replies(later.message_id))[0].content == "done"


@pytest.mark.parametrize("has_result", [False, True])
async def test_missing_turn_releases_queue_and_preserves_result(rollback_runtime, has_result):
    _, backend, mm, _ = rollback_runtime
    first = await send(mm, "lost turn")
    later = await send(mm, "later")
    delivery_id = first.deliveries[0].delivery_id
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', attempts=1, turn_id='missing' "
            "WHERE delivery_id=?",
            (delivery_id,),
        )
        if has_result:
            await db.execute(
                "INSERT INTO mm_delivery_results (delivery_id,content,recorded_at) VALUES (?,?,0)",
                (delivery_id, "saved result"),
            )

    await asyncio.wait_for(mm.dispatch_pending(), 3)
    (settled,) = await mm.get_deliveries(first.message_id)
    assert settled.attempts == 1
    assert settled.turn_id == "missing"
    if has_result:
        assert settled.state.value == "completed"
        (reply,) = await mm.get_replies(first.message_id)
        assert reply.content == "saved result"
    else:
        assert (settled.state.value, settled.error) == ("interrupted", "turn_missing")
        assert await mm.get_replies(first.message_id) == ()
    assert (await mm.get_replies(later.message_id))[0].content == "done"
    before = snapshot(backend.db_path)
    await mm.dispatch_pending()
    assert snapshot(backend.db_path) == before


async def test_worker_failure_write_rollback_is_recovered_in_place(rollback_runtime, monkeypatch):
    agent, backend, mm, _ = rollback_runtime
    entered, fail = asyncio.Event(), asyncio.Event()
    original = agent._action
    checkpointed = False

    async def fail_after_checkpoint(data):
        nonlocal checkpointed
        if not checkpointed:
            checkpointed = True
            return {"results": []}
        entered.set()
        await fail.wait()
        raise RuntimeError("model failed")

    monkeypatch.setattr(agent, "_action", fail_after_checkpoint)
    monkeypatch.setattr(agent.config, "max_iterations", 2)
    opener = await send(mm, "opener")
    await mm._scan()
    await asyncio.wait_for(entered.wait(), 3)
    (claimed,) = await mm.get_deliveries(opener.message_id)
    assert claimed.consumed_node_id is not None
    assert claimed.last_node_id is not None
    later = await send(mm, "later")
    assert (await new_approval(agent._context_manager)).persisted
    async with backend.transaction() as db:
        await db.execute(
            "INSERT INTO mm_inline_waits VALUES (?,?,'waiting')",
            (later.message_id, claimed.delivery_id),
        )
        await db.execute(
            "CREATE TRIGGER fail_delivery_failure BEFORE UPDATE OF state ON mm_deliveries "
            "WHEN OLD.state='processing' AND NEW.state='failed' "
            "BEGIN SELECT RAISE(ABORT, 'cannot record failure'); END"
        )

    # 执行仍持有 worker 时，扫描不得改动活动轮次或其等待状态。
    await mm._scan()
    assert (await mm.get_deliveries(opener.message_id))[0] == claimed
    assert (await backend.load_turn("agent", claimed.turn_id))["status"] == "waiting_hitl"
    worker = mm._workers["agent"]
    fail.set()
    await asyncio.wait_for(asyncio.shield(worker), 3)
    monkeypatch.setattr(agent, "_action", original)
    assert (await mm.get_deliveries(opener.message_id))[0] == claimed
    async with backend.transaction() as db:
        await db.execute("DROP TRIGGER fail_delivery_failure")

    await asyncio.wait_for(mm.dispatch_pending(), 3)
    (interrupted,) = await mm.get_deliveries(opener.message_id)
    assert (interrupted.state.value, interrupted.error) == ("interrupted", "worker_lost")
    assert (interrupted.turn_id, interrupted.attempts) == (claimed.turn_id, 1)
    assert interrupted.consumed_node_id == claimed.consumed_node_id
    assert interrupted.last_node_id == claimed.last_node_id
    closed = await backend.load_turn("agent", claimed.turn_id)
    assert (closed["status"], closed["end_node_id"]) == ("interrupted", claimed.last_node_id)
    assert (await backend.load_hitl_request("agent", "promise"))["status"] == "cancelled"
    async with backend.transaction() as db:
        assert (await db.execute_fetchall("SELECT state FROM mm_inline_waits"))[0][0] == "released"
    assert await mm.get_replies(opener.message_id) == ()
    assert (await mm.get_replies(later.message_id))[0].content == "done"
    await mm.close()
    await mm.open(start_dispatcher=False)
    assert (await mm.get_deliveries(opener.message_id))[0] == interrupted


async def test_missing_inflight_index_allows_terminal_repair(rollback_runtime):
    agent, backend, mm, _ = rollback_runtime
    opener = await send(mm, "interrupted")
    later = await send(mm, "later")
    delivery_id = opener.deliveries[0].delivery_id
    turn_id = await agent._context_manager.open_turn(opener_delivery_id=delivery_id)
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, delivery_id),
        )
        await db.execute("UPDATE turns SET status='aborted' WHERE turn_id=?", (turn_id,))
        await db.execute("DROP INDEX mm_bound_inflight")

    await asyncio.wait_for(mm.dispatch_pending(), 3)
    (settled,) = await mm.get_deliveries(opener.message_id)
    assert (settled.state.value, settled.error) == ("interrupted", "aborted_by_user")
    assert (await mm.get_replies(later.message_id))[0].content == "done"


async def test_stranded_repair_leaves_a_direct_turn_without_matching_opener(rollback_runtime):
    agent, backend, mm, _ = rollback_runtime
    request = await send(mm, "foreign opener")
    direct_turn = await agent._context_manager.open_turn()
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (direct_turn, request.deliveries[0].delivery_id),
        )

    await mm.dispatch_pending()
    row = await backend.load_turn("agent", direct_turn)
    assert (row["status"], row["opener_delivery_id"]) == ("open", None)
    (unchanged,) = await mm.get_deliveries(request.message_id)
    assert unchanged.state.value == "processing"


async def test_consecutive_failures_do_not_pick_previous_diagnosis(rollback_runtime, monkeypatch):
    """连续两次异常：第二次诊断挂在恢复分支上，最近正确节点不得是上一次的 incomplete。"""
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    calls = 0

    async def action(data):
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            return {"results": [], "llm_metadata": {"pending_tool_call_ids": ["unfinished"]}}
        raise RuntimeError("action failed")

    monkeypatch.setattr(agent, "_action", action)
    monkeypatch.setattr(agent.config, "max_iterations", 2)
    branches_before = len(cm.list_branches(cm.active_session_id))
    first = await send(mm, "first")
    await mm.dispatch_pending()
    (first_delivery,) = await mm.get_deliveries(first.message_id)
    first_view = await port.turns.get(agent_id="agent", turn_id=first_delivery.turn_id)
    first_diagnosis = await backend.load_node("agent", first_view.incomplete_node_id)
    assert first_diagnosis.parent_id == first_view.end_node_id

    second = await send(mm, "second")
    await mm.dispatch_pending()
    (second_delivery,) = await mm.get_deliveries(second.message_id)
    second_view = await port.turns.get(agent_id="agent", turn_id=second_delivery.turn_id)
    second_diagnosis = await backend.load_node("agent", second_view.incomplete_node_id)
    assert second_diagnosis.parent_id == second_view.end_node_id
    assert second_diagnosis.id != first_diagnosis.id
    assert second_diagnosis.parent_id != first_diagnosis.id
    assert second_view.start_node_id != first_diagnosis.id
    assert second_diagnosis.metadata["last_correct_node_id"] == second_view.end_node_id
    assert len(cm.list_branches(cm.active_session_id)) == branches_before + 2


async def test_crash_after_abort_commit_recovers_recovery_branch(rollback_runtime, monkeypatch):
    """中止事务提交后、内存安装前崩溃：重启后终态与恢复分支都来自持久事实。"""
    agent, backend, _, _ = rollback_runtime
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    node = append_node(cm, "committed")
    await cm.persist()
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE nodes SET metadata=json_set(metadata,'$.pending_tool_call_ids',json(?)) "
            "WHERE id=?",
            (json.dumps(["tool-1"]), node.id),
        )
    monkeypatch.setattr(cm, "_install_convergence", lambda changes: None)
    outcome = await cm.abort_turn(
        turn_id=turn_id, reason="crash-after-commit", settle_deliveries=None
    )
    assert outcome.changed and outcome.row["status"] == "aborted"
    row = await backend.load_turn("agent", turn_id)
    metadata = json.loads(row["metadata"])
    assert metadata["recovery_branch_id"] and metadata["incomplete_node_id"]
    assert row["end_node_id"] == node.id

    restored = ContextManager("agent", persistence=backend)
    await restored.restore("agent")
    reloaded = json.loads((await backend.load_turn("agent", turn_id))["metadata"])
    assert restored.get_active_session_branch().branch_id == reloaded["recovery_branch_id"]
    assert restored.active_head.id == node.id
    async with backend.transaction() as db:
        session_row = await (
            await db.execute(
                "SELECT active_branch_id FROM sessions WHERE agent_name='agent' AND session_id=?",
                (restored.active_session_id,),
            )
        ).fetchone()
        branch_row = await (
            await db.execute(
                "SELECT head_node_id FROM branches WHERE branch_id=?",
                (reloaded["recovery_branch_id"],),
            )
        ).fetchone()
    assert session_row["active_branch_id"] == reloaded["recovery_branch_id"]
    assert branch_row["head_node_id"] == node.id


async def test_crash_after_failure_convergence_keeps_single_diagnosis(
    rollback_runtime, monkeypatch
):
    """失败收敛提交后、内存安装前崩溃：重启不重复写诊断，活动分支落到恢复分支。"""
    agent, backend, _, _ = rollback_runtime
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    node = append_node(cm, "committed")
    await cm.persist()
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE nodes SET metadata=json_set(metadata,'$.pending_tool_call_ids',json(?)) "
            "WHERE id=?",
            (json.dumps(["tool-1"]), node.id),
        )
    monkeypatch.setattr(cm, "_install_convergence", lambda changes: None)
    outcome = await cm.converge_turn(
        turn_id=turn_id, error=RuntimeError("boom"), settle_deliveries=None
    )
    assert outcome.changed and outcome.row["status"] == "failed"
    row = await backend.load_turn("agent", turn_id)
    metadata = json.loads(row["metadata"])
    assert metadata["incomplete_node_id"] and metadata["recovery_branch_id"]

    restored = ContextManager("agent", persistence=backend)
    await restored.restore("agent")
    checkpoint = await backend.load_checkpoint("agent")
    diagnoses = [
        item for item in checkpoint.nodes if item.metadata.get("node_kind") == "incomplete"
    ]
    assert len(diagnoses) == 1
    assert restored.get_active_session_branch().branch_id == metadata["recovery_branch_id"]
    assert restored.active_head.id == row["end_node_id"] == node.id


async def test_zero_node_failure_records_phase_without_diagnosis(rollback_runtime, monkeypatch):
    """首个提交前失败：只写轮次终态与原因，不建诊断节点/恢复分支。"""
    agent, backend, mm, _ = rollback_runtime

    async def action(data):
        raise RuntimeError("first call failed")

    monkeypatch.setattr(agent, "_action", action)
    branches_before = snapshot(backend.db_path)["branches"]
    request = await send(mm, "zero-node")
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state.value == "failed"
    row = await backend.load_turn("agent", delivery.turn_id)
    metadata = json.loads(row["metadata"])
    assert row["status"] == "failed" and row["start_node_id"] is None
    assert metadata["abort_phase"] == "iteration"
    assert "first call failed" in metadata["abort_reason"]
    assert "incomplete_node_id" not in metadata
    assert snapshot(backend.db_path)["branches"] == branches_before


def test_install_convergence_rejects_active_iteration(rollback_runtime):
    agent, backend, _, _ = rollback_runtime
    cm = agent._context_manager
    before = snapshot(backend.db_path)
    cm.begin_iteration()
    try:
        with pytest.raises(RuntimeError, match="during an iteration"):
            cm._install_convergence(ContextChanges(agent_name=cm.agent_name))
        assert cm.in_iteration
        assert snapshot(backend.db_path) == before
    finally:
        cm.ensure_iteration_closed()


@pytest.mark.parametrize("cancel_before_attach", [False, True])
async def test_abort_before_attach_needs_no_memory_tombstone(
    rollback_runtime, monkeypatch, cancel_before_attach
):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    entered, resume = asyncio.Event(), asyncio.Event()
    receive = agent.receive_atomic
    publish = AsyncMock()
    monkeypatch.setattr(agent._event_publisher, "publish", publish)

    async def wait_before_attach(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await receive(*args, **kwargs)

    monkeypatch.setattr(agent, "receive_atomic", wait_before_attach)
    request = await send(mm, "claimed")
    dispatch = asyncio.create_task(mm.dispatch_pending())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        (delivery,) = await mm.get_deliveries(request.message_id)
        assert delivery.state.value == "processing" and cm.active_turn is None
        assert (await port.turns.abort(agent_id="agent", turn_id=delivery.turn_id)).aborted
        assert cm._revoked_turns == set()
        if cancel_before_attach:
            await mm.unbind("agent")
            await asyncio.gather(dispatch, return_exceptions=True)
        else:
            resume.set()
            await asyncio.wait_for(dispatch, 3)
    finally:
        resume.set()
        if not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)

    assert cm.active_turn is None and not cm._revoked_turns
    assert (await backend.load_turn("agent", delivery.turn_id))["status"] == "aborted"
    assert not await mm.get_replies(request.message_id)
    assert [
        (event.event_type.value, event.status)
        for event in (call.args[0] for call in publish.await_args_list)
    ] == [("turn_closed", "aborted")]


@pytest.mark.parametrize("abort_wins", [True, False])
async def test_close_event_follows_winner_of_result_abort_race(
    rollback_runtime, monkeypatch, abort_wins
):
    agent, backend, mm, port = rollback_runtime
    entered, resume = asyncio.Event(), asyncio.Event()
    complete, finish_result = mm._complete_delivery, mm._finish_result
    publish = AsyncMock()
    monkeypatch.setattr(agent._event_publisher, "publish", publish)

    async def pause_completion(execution, result, result_node_id=None):
        entered.set()
        await resume.wait()
        return await complete(execution, result, result_node_id)

    async def pause_finish_result(delivery_id):
        entered.set()
        await resume.wait()
        return await finish_result(delivery_id)

    if abort_wins:
        # 模拟 Runner 已返回、MM 尚未进入持久结果写事务。
        monkeypatch.setattr(mm, "_finish_atomic", AsyncMock())
        monkeypatch.setattr(mm, "_complete_delivery", pause_completion)
    else:
        monkeypatch.setattr(mm, "_finish_result", pause_finish_result)

    request = await send(mm, "completion race")
    dispatch = asyncio.create_task(mm.dispatch_pending())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        (delivery,) = await mm.get_deliveries(request.message_id)
        row = await backend.load_turn("agent", delivery.turn_id)
        assert row["status"] == ("open" if abort_wins else "closed")
        outcome = await port.turns.abort(agent_id="agent", turn_id=delivery.turn_id)
        assert outcome.aborted == abort_wins
        assert outcome.already_terminal == (not abort_wins)
        resume.set()
        await asyncio.wait_for(dispatch, 3)
    finally:
        resume.set()
        if not dispatch.done():
            dispatch.cancel()
            await asyncio.gather(dispatch, return_exceptions=True)

    row = await backend.load_turn("agent", delivery.turn_id)
    statuses = [
        event.status
        for event in (call.args[0] for call in publish.await_args_list)
        if event.event_type.value == "turn_closed" and event.turn_id == delivery.turn_id
    ]
    expected = "aborted" if abort_wins else "closed"
    assert statuses == [row["status"]] == [expected]
    assert bool(await mm.get_replies(request.message_id)) == (not abort_wins)


async def test_aborted_turn_with_member_explicitly_supports_rollback(rollback_runtime):
    agent, _, _, port = rollback_runtime
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    target = append_node(cm, "member")
    await cm.persist()
    initial = await port.turns.abort(agent_id="agent", turn_id=turn_id, reason="stop")
    assert initial.aborted
    await cm.close_turn(status="interrupted", closing_kind="cancelled")
    args = {"agent_id": "agent", "turn_id": turn_id, "target": "node", "node_id": target.id}
    preview = await port.turns.rollback_preview(**args)
    result = await port.turns.rollback(**args)
    assert result.target_node_id == preview.target_node_id == target.id
    final = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert final.status == "rolled_back" and final.abort_reason == "stop"
