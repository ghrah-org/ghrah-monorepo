# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""消息受理、迭代边界注入与排队的持久契约。"""

import asyncio
import sqlite3

import pytest
from ghrah.protocol.payloads.entity import AgentIdentity
from ghrah.protocol.views.observer_room import (
    RoomFactsQuery,
    RoomMessageReference,
    RoomNodeReference,
)

from ghrah.communication import DeliveryState
from ghrah.communication.mailbox_reader import MailboxReader
from ghrah.core.read.mailbox import MailboxReadStore
from ghrah.core.wiring import build_core_port

pytest_plugins = ["test_turn_lifecycle"]


async def accept(mm, key, *, policy="inject", await_reply=False, room_id=None):
    return await mm.send_external(
        sender="user",
        recipient_ids=("agent",),
        content=key,
        idempotency_key=key,
        delivery_policy=policy,
        await_reply=await_reply,
        metadata={"room_id": room_id} if room_id else None,
    )


async def test_acceptance_binds_only_when_no_independent_reply(runtime):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    turn_id = await agent._context_manager.open_turn()
    request = await accept(mm, "inject")
    (delivery,) = request.deliveries
    assert delivery.turn_id == turn_id
    assert delivery.display_state == "injected"
    assert (await accept(mm, "inject")).deliveries == request.deliveries
    queued = await accept(mm, "independent", await_reply=True)
    assert queued.deliveries[0].turn_id is None
    with pytest.raises(ValueError, match="no independent reply"):
        await mm.wait_replies(request.message_id)
    with pytest.raises(ValueError, match="conflicts"):
        await accept(mm, "inject", policy="queue")
    assert len(await backend.list_turns("agent", None, None, 50)) == 1


async def test_queued_cancel_is_visible_and_retryable(runtime):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    request = await accept(mm, "fallback")
    delivery = request.deliveries[0]
    assert delivery.turn_id is None
    reader = MailboxReadStore(backend.db_path, scope_id="p")
    queue = await reader.queue(
        agent_id="agent", session_id=agent._context_manager.active_session_id
    )
    assert queue.entries[0].cancelable
    await mm.cancel(delivery.delivery_id)
    cancelled = await reader.get_delivery(agent_id="agent", delivery_id=delivery.delivery_id)
    assert cancelled.display_state == "cancelled"
    assert not cancelled.cancelable
    assert (await reader.queue(agent_id="agent", session_id=queue.session_id)).entries == []
    await mm.retry(delivery.delivery_id)
    assert (await mm.get_deliveries(request.message_id))[0].state == DeliveryState.PENDING


async def test_queue_and_injection_reads_batch_and_idle_scan_does_not_write(runtime):
    agent, backend, mm = runtime
    await mm.close()
    await mm.open(start_dispatcher=False)
    session_id = agent._context_manager.active_session_id
    for index in range(16):
        await accept(mm, f"queued-{index}", policy="queue")

    statements = []
    identity = AgentIdentity(project_id="p", agent_id="agent")
    async with MailboxReader(backend.db_path, identity) as reader:
        await reader._db.set_trace_callback(statements.append)
        entries = (await reader.queue(session_id))["entries"]
        assert [entry["position"] for entry in entries] == list(range(1, 17))
        assert {entry["blocked_by_delivery_id"] for entry in entries[1:]} == {
            entries[0]["delivery_id"]
        }
        assert sum(sql.startswith("SELECT") for sql in statements) <= 4

    statements.clear()
    db = mm._sqlite.require_connection()
    await db.set_trace_callback(statements.append)
    try:
        await mm._scan()
        assert not any(
            sql.startswith(("BEGIN IMMEDIATE", "INSERT", "UPDATE", "DELETE")) for sql in statements
        )
        (probe,) = (sql for sql in statements if sql.startswith("SELECT EXISTS (SELECT 1 FROM"))
        plan = await db.execute_fetchall("EXPLAIN QUERY PLAN " + probe)
        details = [row[3] for row in plan]
        assert sum("mm_bound_inflight" in detail for detail in details) == 2, details
        assert any("mm_unbound_processing" in detail for detail in details), details

        turn_id = await agent._context_manager.open_turn()
        for index in range(16):
            await accept(mm, f"injected-{index}")
        statements.clear()
        inputs = await mm.load_turn_inputs(turn_id)
        assert [item.text for item in inputs] == [f"injected-{index}" for index in range(16)]
        assert sum(sql.startswith("SELECT") for sql in statements) == 1
    finally:
        await db.set_trace_callback(None)


async def test_two_injections_commit_with_opener_in_one_node(runtime, monkeypatch):
    agent, backend, mm = runtime
    entered, release = asyncio.Event(), asyncio.Event()
    original = agent._ensure_llm

    async def pause():
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(agent, "_ensure_llm", pause)
    first = await accept(mm, "opener", policy="queue", await_reply=True, room_id="room")
    await asyncio.wait_for(entered.wait(), 1)
    second = await accept(mm, "one", room_id="room")
    third = await accept(mm, "two", room_id="room")
    turn_id = second.deliveries[0].turn_id
    assert turn_id == third.deliveries[0].turn_id
    port = build_core_port(scope_id="p", db_path=str(backend.db_path))
    try:
        active = RoomFactsQuery(room_id="room", active_agent_ids=["agent"], include_turns=True)
        assert [card.turn_id for card in (await port.transcript.facts(active)).turns] == [turn_id]
        foreign = active.model_copy(update={"room_id": "different-room"})
        assert (await port.transcript.facts(foreign)).turns == []
    finally:
        release.set()
        await port.aclose()
    await mm.dispatch_pending()
    deliveries = [
        (await mm.get_deliveries(request.message_id))[0] for request in (first, second, third)
    ]
    assert {d.state for d in deliveries} == {DeliveryState.COMPLETED}
    assert len({d.consumed_node_id for d in deliveries}) == 1
    assert {d.turn_id for d in deliveries} == {turn_id}
    node = await backend.load_node("agent", deliveries[0].consumed_node_id)
    assert [m.text for m in node.messages_delta if m.role == "user"] == ["opener", "one", "two"]
    assert len(await backend.list_turns("agent", None, None, 50)) == 1
    assert await mm.get_replies(second.message_id) == ()
    assert await mm.get_replies(third.message_id) == ()

    (reply,) = await mm.get_replies(first.message_id)
    query = RoomFactsQuery(
        room_id="room",
        messages=[
            RoomMessageReference(message_id=request.message_id, author="user")
            for request in (first, second, third)
        ]
        + [RoomMessageReference(message_id=reply.message_id, author="agent")],
        nodes=[RoomNodeReference(node_id=node.id, agent_id="agent")],
        include_turns=True,
    )
    port = build_core_port(scope_id="p", db_path=str(backend.db_path))
    try:
        facts = await port.transcript.facts(query)
        (card,) = facts.turns
        assert card.turn_id == turn_id
        assert card.opener_message_id == first.message_id
        assert card.stats == (await port.turns.detail(agent_id="agent", turn_id=turn_id)).turn.stats
        assert {d.message_id: d.injection_step for d in facts.deliveries} == {
            first.message_id: None,
            second.message_id: 1,
            third.message_id: 1,
            reply.message_id: None,
        }
        assert {m.message_id: m.turn_id for m in facts.messages} == {
            first.message_id: None,
            second.message_id: None,
            third.message_id: None,
            reply.message_id: turn_id,
        }
        assert (node.id, "agent", turn_id) in {
            (fact.node_id, fact.agent_id, fact.turn_id) for fact in facts.node_turns
        }
        legacy = await port.transcript.facts(query.model_copy(update={"messages": []}))
        assert [(fact.node_id, fact.turn_id) for fact in legacy.node_turns] == [(node.id, turn_id)]
        foreign = query.model_copy(update={"room_id": "different-room", "messages": []})
        assert (await port.transcript.facts(foreign)).node_turns == []

    finally:
        await port.aclose()


@pytest.mark.parametrize("terminal", ["closed", "failed", "interrupted"])
async def test_terminal_turn_releases_unconsumed_and_settles_consumed(runtime, terminal):
    from datetime import UTC, datetime

    from ghrah.communication.delivery_queue import settle_terminal_turns
    from ghrah.context import turn

    agent, backend, mm = runtime
    await mm.unbind("agent")
    cm = agent._context_manager
    active = await cm.open_turn()
    consumed = await accept(mm, "consumed")
    queued = await accept(mm, "unconsumed")
    async with mm._sqlite.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET consumed_node_id=?, consumed_session_id=? "
            "WHERE delivery_id=?",
            (cm.active_head.id, cm.active_session_id, consumed.deliveries[0].delivery_id),
        )
        await turn.close_turn(
            db,
            turn_id=active,
            status=terminal,
            closing_kind="completed" if terminal == "closed" else "cancelled",
            end_node_id=cm.active_head.id,
            closed_at=datetime.now(UTC).isoformat(),
        )
        await settle_terminal_turns(db)
        await settle_terminal_turns(db)
    first = (await mm.get_deliveries(consumed.message_id))[0]
    later = (await mm.get_deliveries(queued.message_id))[0]
    assert first.state.value == ("completed" if terminal == "closed" else terminal)
    assert first.consumed_node_id == cm.active_head.id
    assert first.turn_id == active
    assert (later.state, later.turn_id, later.delivery_policy) == (
        DeliveryState.PENDING,
        None,
        "queue",
    )


async def test_completion_failure_preserves_result_and_blocks_successor(runtime, monkeypatch):
    agent, backend, mm = runtime
    finish = mm._finish_result

    async def fail_finish(delivery_id):
        raise RuntimeError("cannot finalize")

    monkeypatch.setattr(mm, "_finish_result", fail_finish)
    first = await accept(mm, "first", policy="queue", await_reply=True)
    await mm.dispatch_pending()
    prior = (await mm.get_deliveries(first.message_id))[0]
    assert prior.result_ready and prior.state == DeliveryState.PROCESSING
    later = await accept(mm, "later", policy="queue", await_reply=True)
    await mm.dispatch_pending()
    pending = (await mm.get_deliveries(later.message_id))[0]
    assert (pending.state, pending.attempts) == (DeliveryState.PENDING, 0)
    blocker = await mm.get_queue_blocker(pending.delivery_id)
    assert blocker.delivery_id == prior.delivery_id
    assert blocker.queue_block_reason == "completion_pending"
    reader = MailboxReadStore(backend.db_path, "p")
    assert (
        await reader.get_delivery(agent_id="agent", delivery_id=prior.delivery_id)
    ).display_state == ("finalizing")
    with pytest.raises(RuntimeError, match="completion_pending"):
        await mm.wait_replies(later.message_id)
    await mm.cancel(pending.delivery_id)
    assert await mm.recover_completions() == 0
    with pytest.raises(TimeoutError):
        await mm.wait_replies(first.message_id, timeout=0.02)
    # 升级前可能已经将有结果的投递误记为失败，等待者仍应等待收尾。
    async with mm._sqlite.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='failed' WHERE delivery_id=?", (prior.delivery_id,)
        )
    waiter = asyncio.create_task(mm.wait_replies(first.message_id, tolerate_errors=True))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    monkeypatch.setattr(mm, "_finish_result", finish)
    assert await mm.recover_completions() == 1
    assert len(await asyncio.wait_for(waiter, timeout=1)) == 1
    assert await mm.recover_completions() == 0
    assert (await mm.get_deliveries(first.message_id))[0].state == DeliveryState.COMPLETED
    assert len(await mm.get_replies(first.message_id)) == 1


async def test_injection_can_bypass_queued_opener_but_queue_order_stays(runtime):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    active = await agent._context_manager.open_turn()
    first = await accept(mm, "queue-1", policy="queue")
    injected = await accept(mm, "inject")
    last = await accept(mm, "queue-2", policy="queue")
    assert injected.deliveries[0].turn_id == active
    assert first.deliveries[0].turn_id is None
    reader = MailboxReadStore(backend.db_path, "p")
    queue = await reader.queue(
        agent_id="agent", session_id=agent._context_manager.active_session_id
    )
    assert [entry.message_id for entry in queue.entries] == [
        first.message_id,
        injected.message_id,
        last.message_id,
    ]
    assert [entry.position for entry in queue.entries] == [1, 0, 2]
    with pytest.raises(ValueError, match="delivery_not_cancelable"):
        await mm.cancel(injected.deliveries[0].delivery_id)
    await mm.cancel(first.deliveries[0].delivery_id)
    assert (await reader.queue(agent_id="agent", session_id=queue.session_id)).entries[
        -1
    ].position == 1


async def test_waiting_hitl_injection_consumes_only_after_approval(runtime, monkeypatch):
    from types import SimpleNamespace

    from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, ExecutionServices
    from ghrah.ability.execution.executor import LocalAbilityExecutor
    from ghrah.ability.execution.hooks import HookResult
    from ghrah.ability.execution.invocation import MemberReference, OperationCall
    from ghrah.core.events import HITLRequestEvent

    agent, backend, mm = runtime
    cm = agent._context_manager
    entered = asyncio.Event()
    executor = LocalAbilityExecutor("display", hitl_timeout=2)
    calls = 0

    async def publish(event):
        if isinstance(event, HITLRequestEvent):
            entered.set()

    executor.update_event_publisher(SimpleNamespace(publish=publish))

    async def action(data):
        nonlocal calls
        calls += 1
        if calls == 1:
            await executor.handle_hitl_hook_result(
                HookResult(should_continue=False, requires_hitl=True),
                OperationCall(member=MemberReference(member="tool"), tool_call_id="call"),
                ExecutionServices({CONTEXT_MANAGER: cm}),
            )
        return {"results": []}

    monkeypatch.setattr(agent, "_action", action)
    monkeypatch.setattr(agent.config, "max_iterations", 2)
    opener = await accept(mm, "opener", policy="queue")
    await asyncio.wait_for(entered.wait(), 1)
    incoming = await accept(mm, "hitl input")
    incoming_id = incoming.deliveries[0].delivery_id
    assert incoming.deliveries[0].turn_id == cm.active_turn_id
    assert (await backend.load_turn("agent", cm.active_turn_id))["status"] == "waiting_hitl"
    assert (await mm.get_deliveries(incoming.message_id))[0].consumed_node_id is None
    pending = (await backend.list_pending_hitl("agent", cm.active_session_id))[0]
    decision = await cm.resolve_hitl_request(
        pending["promise_id"], approved=True, resolved_by=None, resolution=None
    )
    assert decision.changed
    assert executor.receive_hitl_response("tool", "call", True)
    await mm.dispatch_pending()
    first = (await mm.get_deliveries(opener.message_id))[0]
    second = (await mm.get_deliveries(incoming.message_id))[0]
    assert calls == 2
    assert first.consumed_node_id != second.consumed_node_id
    assert first.turn_id == second.turn_id
    assert second.state == DeliveryState.COMPLETED
    node = await backend.load_node("agent", second.consumed_node_id)
    assert any(m.metadata.get("delivery_id") == incoming_id for m in node.messages_delta)


@pytest.mark.parametrize(
    "fault",
    [
        "closed",
        "head_changed",
        "input_changed",
        "missing_opener",
        "new_attempt",
        "chain_write",
        "outbound_write",
        "inline_reservation",
        "outbound_replay",
    ],
)
async def test_atomic_iteration_commits_or_rolls_back_all_facts(runtime, fault):
    from ghrah.chat.message import ChatMessage
    from ghrah.communication.message_manager import _AtomicDelivery
    from ghrah.communication.storage import DeliveryExecution, MessageKind, MessageSubmission
    from ghrah.context.persistence import ContextChanges

    agent, backend, mm = runtime
    await mm.close()
    await mm.open(start_dispatcher=False)
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    opener = await accept(mm, "opener", policy="queue")
    incoming = await accept(mm, "incoming")
    opener_id = opener.deliveries[0].delivery_id
    input_id = incoming.deliveries[0].delivery_id
    async with mm._sqlite.transaction() as db:
        await db.execute(
            "UPDATE turns SET opener_delivery_id=? WHERE turn_id=?", (opener_id, turn_id)
        )
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, opener_id),
        )
    cm.begin_iteration()
    messages = list(await mm.load_turn_inputs(turn_id))
    if fault != "missing_opener":
        messages.insert(0, ChatMessage.user("opener", metadata={"delivery_id": opener_id}))
    cm.add_messages(messages)
    node = cm.commit_iteration()
    scope = _AtomicDelivery(
        mm, DeliveryExecution(opener_id, "agent", 0, turn_id, mm._sqlite._execution_token), [], cm
    )
    scope.consumed_deliveries.add(input_id)
    outbound = MessageSubmission(
        sender_id="agent",
        recipient_ids=("agent",),
        content="follow-up",
        kind=MessageKind.REQUEST,
        idempotency_key="outbound",
        session_routing=True,
        sender_session_id=cm.active_session_id,
        metadata={"inline_reply": True},
    )
    draft = (await mm._delivery_store.prepare_outbound(outbound)).draft
    if fault in {"outbound_write", "inline_reservation", "outbound_replay"}:
        scope.drafts.append(draft)
    if fault == "inline_reservation":
        scope.inline_updates["missing-reservation"] = True
    if fault == "outbound_replay":
        accepted = await mm._delivery_store.accept_message(outbound)
        assert accepted.message_id == draft.message_id
    async with mm._sqlite.transaction() as db:
        if fault == "closed":
            await db.execute("UPDATE turns SET status='closed' WHERE turn_id=?", (turn_id,))
        elif fault == "head_changed":
            await db.execute(
                "UPDATE branches SET head_node_id='other-head' WHERE branch_id=?",
                (cm.get_active_session_branch().branch_id,),
            )
        elif fault == "input_changed":
            await db.execute(
                "UPDATE mm_deliveries SET turn_id=NULL WHERE delivery_id=?", (input_id,)
            )
        elif fault == "new_attempt":
            await db.execute(
                "UPDATE mm_deliveries SET attempts=attempts+1 WHERE delivery_id=?", (opener_id,)
            )
        elif fault == "chain_write":
            await db.execute(
                "CREATE TRIGGER reject_node BEFORE INSERT ON nodes "
                "BEGIN SELECT RAISE(ABORT, 'reject node'); END"
            )
        elif fault == "outbound_write":
            await db.execute(
                "CREATE TRIGGER reject_message BEFORE INSERT ON mm_messages "
                "BEGIN SELECT RAISE(ABORT, 'reject outbound'); END"
            )
    changes = ContextChanges(
        agent_name="agent",
        nodes=(node,),
        sessions=(cm.get_active_session(),),
        branches=(cm.get_active_session_branch(),),
    )
    if fault == "outbound_replay":
        await mm._apply_atomic(scope, changes)
        assert (await mm.get_message(draft.message_id)).origin_node_id == node.id
        assert await mm._query.inline_wait_is_active(draft.message_id, opener_id)
        return

    branch = cm.get_active_session_branch()
    head_before = await backend.load_branch_head("agent", cm.active_session_id, branch.branch_id)
    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        await mm._apply_atomic(scope, changes)
    assert (
        await backend.load_branch_head("agent", cm.active_session_id, branch.branch_id)
        == head_before
    )
    with pytest.raises(KeyError):
        await mm.get_message(draft.message_id)
    assert not await mm._query.inline_wait_is_active(draft.message_id, opener_id)
    assert await backend.load_node("agent", node.id) is None
    assert (await mm.get_deliveries(opener.message_id))[0].consumed_node_id is None
    assert (await mm.get_deliveries(incoming.message_id))[0].consumed_node_id is None


async def test_compact_commits_without_loading_or_consuming_injections(runtime):
    from ghrah.communication.message_manager import _AtomicDelivery
    from ghrah.communication.storage import DeliveryExecution
    from ghrah.context.persistence import ContextChanges

    agent, backend, mm = runtime
    await mm.unbind("agent")
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    opener = await accept(mm, "opener", policy="queue")
    incoming = await accept(mm, "incoming")
    opener_id = opener.deliveries[0].delivery_id
    async with mm._sqlite.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='processing', turn_id=? WHERE delivery_id=?",
            (turn_id, opener_id),
        )
    compact = cm.commit_compact_node([])
    scope = _AtomicDelivery(
        mm, DeliveryExecution(opener_id, "agent", 0, turn_id, mm._sqlite._execution_token), [], cm
    )
    scope.consumed_deliveries.add(incoming.deliveries[0].delivery_id)
    changes = ContextChanges(
        agent_name="agent",
        nodes=(compact,),
        sessions=(cm.get_active_session(),),
        branches=(cm.get_active_session_branch(),),
    )
    with pytest.raises(ValueError, match="Compact"):
        await mm._apply_atomic(scope, changes)
    scope.consumed_deliveries.clear()
    await mm._apply_atomic(scope, changes)
    assert (await backend.load_turn("agent", turn_id))["start_node_id"] == compact.id
    assert (await mm.get_deliveries(opener.message_id))[0].last_node_id == compact.id
    assert (await mm.get_deliveries(incoming.message_id))[0].consumed_node_id is None
    assert len(await mm.load_turn_inputs(turn_id)) == 1
    assert not scope.consumed


async def test_public_queue_cancel_and_send_acceptance(runtime, monkeypatch):
    from unittest.mock import AsyncMock

    from ghrah.communication.supervisor import SupervisorActor
    from ghrah.core.api.errors import CoreApiError
    from ghrah.core.unit import CoreUnit, CoreUnitConfig
    from ghrah.core.wiring import build_core_port

    agent, backend, mm = runtime
    await mm.unbind("agent")
    port = build_core_port(scope_id="p", db_path=str(backend.db_path))
    registry = port._registry
    registry._manager = mm
    monkeypatch.setattr(registry, "ensure", AsyncMock())
    supervisor = SupervisorActor(message_manager=mm)
    supervisor._registry.register("display", agent.config, agent)
    unit = CoreUnit(CoreUnitConfig(project_id="p", message_manager=mm))
    unit._supervisor = supervisor

    async def command(*, command, payload):
        return await unit._handle_send_message({**payload, "project_id": "p"}, None)

    monkeypatch.setattr(registry, "handle_command", command)
    try:
        receipt = await port.messages.append(
            agent_id="agent",
            target="display",
            content="queued",
            delivery_policy="inject",
        )
        assert receipt.message_id
        assert not receipt.deliveries[0].policy_applied
        assert receipt.deliveries[0].queue_position == 1
        queue = await port.deliveries.queue(
            agent_id="agent", session_id=agent._context_manager.active_session_id
        )
        assert queue.entries[0].delivery_id == receipt.deliveries[0].delivery_id
        with pytest.raises(CoreApiError):
            await port.deliveries.cancel(
                agent_id="foreign", delivery_id=queue.entries[0].delivery_id
            )
        await port.deliveries.cancel(agent_id="agent", delivery_id=queue.entries[0].delivery_id)
        assert (
            await port.deliveries.get(
                agent_id="agent",
                delivery_id=queue.entries[0].delivery_id,
            )
        ).display_state == "cancelled"
        with pytest.raises(CoreApiError) as not_cancelable:
            await port.deliveries.cancel(agent_id="agent", delivery_id=queue.entries[0].delivery_id)
        assert not_cancelable.value.problem.details["reason"] == "delivery_not_cancelable"
        # 缺省 queue 仍经同步发送等待独立回复。
        await mm.bind_atomic(agent)
        reply = await port.messages.append(agent_id="agent", target="display", content="normal")
        assert reply.content == "done"
    finally:
        registry._manager = None
        await port.aclose()


async def test_reply_expected_inject_fallback_can_wait_after_claim(runtime):
    agent, backend, mm = runtime
    request = await accept(mm, "requires reply", await_reply=True)
    await mm.dispatch_pending()
    (reply,) = await mm.wait_replies(request.message_id)
    assert reply.content == "done"
    (record,) = (await accept(mm, "requires reply", await_reply=True)).deliveries
    assert not record.policy_applied


@pytest.mark.parametrize("missing", [False, True])
async def test_special_turn_blockers_match_scheduler_and_recovery(runtime, missing):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    await mm.close()
    await mm.open(start_dispatcher=False)
    active = await agent._context_manager.open_turn()
    injected = await accept(mm, "injected")
    later = await accept(mm, "later", policy="queue")
    async with mm._sqlite.transaction() as db:
        if missing:
            await db.execute(
                "UPDATE mm_deliveries SET turn_id='missing' WHERE delivery_id=?",
                (injected.deliveries[0].delivery_id,),
            )
        else:
            await db.execute("UPDATE turns SET status='closed' WHERE turn_id=?", (active,))
    blocker = await mm.get_queue_blocker(later.deliveries[0].delivery_id)
    assert blocker.queue_block_reason == (
        "turn_missing" if missing else "turn_terminal_pending_input"
    )
    async with mm._sqlite.lock:
        db = mm._sqlite.require_connection()
        candidate = await (await db.execute(mm._sqlite._next_delivery_sql(), ("agent",))).fetchone()
        assert candidate is None
    await mm.dispatch_pending()
    (released,) = await mm.get_deliveries(injected.message_id)
    if missing:
        assert (released.state, released.error) == (DeliveryState.INTERRUPTED, "turn_missing")
        assert released.turn_id == "missing"
        assert await mm.get_queue_blocker(later.deliveries[0].delivery_id) is None
    else:
        assert (released.turn_id, released.delivery_policy) == (None, "queue")


async def test_reopen_releases_injections_idempotently(runtime):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    incoming = await accept(mm, "incoming")
    await mm.close()
    await mm.open(start_dispatcher=False)
    assert (await backend.load_turn("agent", turn_id))["status"] == "interrupted"
    released = (await mm.get_deliveries(incoming.message_id))[0]
    assert (released.turn_id, released.delivery_policy, released.consumed_node_id) == (
        None,
        "queue",
        None,
    )
    await mm.close()
    await mm.open(start_dispatcher=False)
    assert (await mm.get_deliveries(incoming.message_id))[0] == released


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_execution_settles_consumed_injection(
    runtime, monkeypatch, cancel
):
    agent, backend, mm = runtime
    entered, run, next_iteration = asyncio.Event(), asyncio.Event(), asyncio.Event()
    fail = asyncio.Event()
    ensure = agent._ensure_llm
    count = 0

    async def pause():
        entered.set()
        await run.wait()
        return await ensure()

    async def action(data):
        nonlocal count
        count += 1
        if count == 2:
            next_iteration.set()
            await fail.wait()
            raise RuntimeError("execution failed")
        return {"results": []}

    monkeypatch.setattr(agent, "_ensure_llm", pause)
    monkeypatch.setattr(agent, "_action", action)
    monkeypatch.setattr(agent.config, "max_iterations", 3)
    await accept(mm, "opener", policy="queue")
    await asyncio.wait_for(entered.wait(), 1)
    incoming = await accept(mm, "consume me")
    run.set()
    await asyncio.wait_for(next_iteration.wait(), 1)
    before = (await mm.get_deliveries(incoming.message_id))[0]
    assert before.consumed_node_id is not None
    if cancel:
        await mm.unbind("agent")
    else:
        fail.set()
        await mm.dispatch_pending()
    after = (await mm.get_deliveries(incoming.message_id))[0]
    assert after.state == (DeliveryState.INTERRUPTED if cancel else DeliveryState.FAILED)
    assert after.consumed_node_id == before.consumed_node_id
    assert after.turn_id == before.turn_id


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"state": "pending"}, "queued"),
        ({"state": "pending", "turn_id": "t"}, "injected"),
        ({"state": "pending", "turn_id": "t", "consumed_node_id": "n"}, "consumed"),
        ({"state": "failed", "result_ready": True}, "finalizing"),
        ({"state": "processing", "result_ready": True}, "finalizing"),
        ({"state": "interrupted", "error": "cancelled_by_user"}, "cancelled"),
        ({"state": "pending", "blocked_reason": "session"}, "blocked"),
        ({"state": "failed", "parked": True}, "parked"),
    ],
)
def test_display_state_uses_durable_evidence(item, expected):
    from ghrah.communication.delivery_queue import display_state

    assert display_state(item) == expected


async def test_send_does_not_bind_other_session_or_notification(runtime):
    from ghrah.communication import MessageKind

    agent, backend, mm = runtime
    await mm.unbind("agent")
    cm = agent._context_manager
    turn_id = await cm.open_turn()
    other = cm.create_session()
    await cm.persist()
    request = await mm.send(
        sender_id="agent",
        recipient_ids=("agent",),
        content="other session",
        kind=MessageKind.REQUEST,
        idempotency_key="other-session",
        target_sessions={"agent": other.session_id},
        delivery_policy="inject",
        await_reply=False,
    )
    assert request.deliveries[0].turn_id is None
    notification = await mm.send(
        sender_id="agent",
        recipient_ids=("agent",),
        content="notification",
        kind=MessageKind.NOTIFICATION,
        idempotency_key="notification",
        delivery_policy="inject",
        await_reply=False,
    )
    assert notification.deliveries[0].turn_id is None
    assert (await backend.load_turn("agent", turn_id))["status"] == "open"


async def test_normal_close_requeues_unconsumed_injection_without_second_reply(
    runtime, monkeypatch
):
    agent, backend, mm = runtime
    pause, finish = asyncio.Event(), asyncio.Event()
    llm = await agent._ensure_llm()
    generate = llm.generate

    async def hold(messages, tools=None):
        pause.set()
        await finish.wait()
        return await generate(messages, tools)

    monkeypatch.setattr(llm, "generate", hold)
    opener = await accept(mm, "opener", policy="queue")
    await asyncio.wait_for(pause.wait(), 1)
    incoming = await accept(mm, "late injection")
    # 暂停新 opener 领取，只让已经运行的一轮完成。
    mm._paused_agents.add("agent")
    finish.set()
    await mm.dispatch_pending()
    first = (await mm.get_deliveries(opener.message_id))[0]
    pending = (await mm.get_deliveries(incoming.message_id))[0]
    assert first.state == DeliveryState.COMPLETED
    assert (pending.state, pending.turn_id, pending.delivery_policy) == (
        DeliveryState.PENDING,
        None,
        "queue",
    )
    assert await mm.get_replies(incoming.message_id) == ()
    assert len(await backend.list_turns("agent", None, None, 50)) == 1


async def test_inject_to_owned_unavailable_session_is_retained_and_blocked(runtime):
    from ghrah.communication import MessageKind

    agent, backend, mm = runtime
    await mm.unbind("agent")
    cm = agent._context_manager
    closed = cm.create_session()
    cm.archive_session(closed.session_id)
    await cm.persist()
    request = await mm.send(
        sender_id="agent",
        recipient_ids=("agent",),
        content="retain input",
        kind=MessageKind.REQUEST,
        idempotency_key="unavailable",
        target_sessions={"agent": closed.session_id},
        delivery_policy="inject",
        await_reply=False,
    )
    (delivery,) = request.deliveries
    assert (delivery.turn_id, delivery.state, delivery.blocked_reason) == (
        None,
        DeliveryState.BLOCKED,
        "target_session_unavailable",
    )
    assert not delivery.policy_applied
    await mm.dispatch_pending()
    assert (await mm.get_deliveries(request.message_id))[0].attempts == 0


@pytest.mark.parametrize("message_type", ["tool_result", "result", "error", "broadcast"])
async def test_reply_and_broadcast_inputs_keep_their_own_queue(runtime, message_type):
    agent, backend, mm = runtime
    await mm.unbind("agent")
    await agent._context_manager.open_turn()
    request = await mm.send_external(
        sender="user",
        recipient_ids=("agent",),
        content="reply",
        idempotency_key="reply",
        metadata={"message_type": message_type},
        delivery_policy="inject",
        await_reply=False,
    )
    assert request.deliveries[0].turn_id is None
    assert not request.deliveries[0].policy_applied
