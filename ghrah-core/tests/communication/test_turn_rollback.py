# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""从 Core 端口验证轮次回滚的 SQLite 和 Runner 边界。"""

import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock

import pytest
from ghrah.protocol.enums import CommandType
from ghrah.protocol.problem import ProblemCode
from ghrah.protocol.views.core import TurnAbortView, TurnRollbackView

from ghrah.chat.message import ChatMessage
from ghrah.communication.sqlite_message_store import SqliteDeliverySettlement
from ghrah.communication.supervisor import SupervisorActor
from ghrah.context.manager import ContextManager
from ghrah.core.api.errors import CoreApiError
from ghrah.core.unit import CoreUnit, CoreUnitConfig
from ghrah.core.units.context import UnitContext
from ghrah.core.units.registry import RuntimeHandle
from ghrah.core.wiring import build_core_port

pytest_plugins = ["test_turn_lifecycle"]


@pytest.fixture
async def rollback_runtime(runtime):
    agent, backend, mm = runtime
    await mm.close()
    await mm.open(start_dispatcher=False)
    await mm.bind_atomic(agent)
    supervisor = SupervisorActor(message_manager=mm)
    supervisor._registry.register("display", agent.config, agent)
    agent._supervisor = supervisor
    unit = CoreUnit(CoreUnitConfig(project_id="p", message_manager=mm))
    unit._supervisor = supervisor
    assert CommandType.TURN_ROLLBACK.value in unit.meta.routes.commands

    port = build_core_port(scope_id="p", db_path=str(backend.db_path))
    registry = port._registry
    registry._handle = RuntimeHandle("p", "test", unit, UnitContext(request_id="p"))
    try:
        yield agent, backend, mm, port
    finally:
        registry._handle = None
        await port.aclose()


async def send(mm, key):
    return await mm.send_external(
        sender="user", recipient_ids=("agent",), content=key, idempotency_key=key
    )


def snapshot(path):
    tables = (
        "context_schema",
        "mm_meta",
        "nodes",
        "turns",
        "chain_meta",
        "sessions",
        "branches",
        "mm_deliveries",
        "mm_delivery_results",
        "mm_messages",
        "mm_room_projections",
        "hitl_requests",
    )
    with closing(sqlite3.connect(path)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in tables
        }


@pytest.fixture
async def closed_turn(rollback_runtime):
    agent, backend, mm, _ = rollback_runtime
    request = await send(mm, "opener")
    await mm.dispatch_pending()
    (opener,) = await mm.get_deliveries(request.message_id)
    row = await backend.load_turn("agent", opener.turn_id)
    assert row["status"] == "closed"
    assert (await mm.get_replies(request.message_id))[0].content == "done"
    return row


async def test_public_turn_commands_return_typed_idempotent_receipts(rollback_runtime, closed_turn):
    _, _, _, port = rollback_runtime
    registry = port._registry
    payload = {
        "agent_id": "agent",
        "agent_name": "display",
        "turn_id": closed_turn["turn_id"],
    }

    invalid = await registry.handle_command(command="turn_abort", payload={**payload, "reason": 1})
    assert invalid["error"] == "invalid_abort_request"
    invalid = await registry.handle_command(command="turn_rollback", payload=payload)
    assert invalid["error"] == "invalid_rollback_target"
    wrong_agent = await registry.handle_command(
        command="turn_abort", payload={**payload, "agent_id": "other"}
    )
    assert wrong_agent["error"] == "agent_identity_mismatch"

    abort = await registry.handle_command(command="turn_abort", payload=payload)
    assert abort["success"]
    assert TurnAbortView.model_validate(abort["data"]).already_terminal

    rollback_payload = {**payload, "target": "base", "dry_run": False}
    first = await registry.handle_command(command="turn_rollback", payload=rollback_payload)
    replay = await registry.handle_command(command="turn_rollback", payload=rollback_payload)
    assert first["success"] and replay["success"]
    first_view = TurnRollbackView.model_validate(first["data"])
    assert not first_view.already_applied
    assert TurnRollbackView.model_validate(replay["data"]) == first_view.model_copy(
        update={"already_applied": True}
    )


async def seed_residual_inputs(backend, mm, row):
    ids = {}
    for key in ("released-first", "released-second", "consumed", "failed", "result", "queued"):
        request = await send(mm, key)
        ids[key] = request.deliveries[0].delivery_id
    async with backend.transaction() as db:
        await db.execute(
            "INSERT OR IGNORE INTO mm_room_projections (message_id,completed,attempts) "
            "SELECT message_id,1,1 FROM mm_deliveries WHERE delivery_id=?",
            (row["opener_delivery_id"],),
        )
        # 模拟轮次已关闭、原请求尚未完成独立收尾，另有待归一的历史绑定。
        await db.execute(
            "UPDATE mm_deliveries SET state='processing' WHERE delivery_id=?",
            (row["opener_delivery_id"],),
        )
        await db.executemany(
            "UPDATE mm_deliveries SET turn_id=?, delivery_policy='inject', error='stale' "
            "WHERE delivery_id=?",
            [(row["turn_id"], delivery_id) for key, delivery_id in ids.items() if key != "queued"],
        )
        await db.execute(
            "UPDATE mm_deliveries SET consumed_node_id=?, state='completed' WHERE delivery_id=?",
            (row["start_node_id"], ids["consumed"]),
        )
        await db.execute(
            "UPDATE mm_deliveries SET state='failed' WHERE delivery_id=?", (ids["failed"],)
        )
        await db.execute(
            "INSERT INTO mm_delivery_results (delivery_id,content,recorded_at) VALUES (?, 'ok', 1)",
            (ids["result"],),
        )
    return ids


async def test_base_rollback_preserves_history_and_releases_inputs(
    rollback_runtime, closed_turn, monkeypatch
):
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    row = closed_turn
    ids = await seed_residual_inputs(backend, mm, row)
    expected_ids = [ids["released-first"], ids["released-second"]]
    async with backend.transaction() as db:
        await db.execute("UPDATE turns SET metadata=json_set(metadata,'$.existing','kept')")
    before = snapshot(backend.db_path)
    old_messages = cm.get_messages()
    event_sink = AsyncMock()
    monkeypatch.setattr(agent._event_publisher, "publish", event_sink)
    kwargs = {"agent_id": "agent", "turn_id": row["turn_id"]}

    preview = await port.turns.rollback_preview(**kwargs)
    assert preview.target_node_id == row["base_node_id"]
    assert preview.truncated_node_count == len(before["nodes"]) - 1
    assert preview.released_delivery_ids == expected_ids
    assert not preview.head_unchanged
    assert not preview.crosses_compact
    end_preview = await port.turns.rollback_preview(
        **kwargs, target="node", node_id=row["end_node_id"]
    )
    assert end_preview.head_unchanged and end_preview.truncated_node_count == 0
    assert snapshot(backend.db_path) == before
    assert cm.active_head.id == row["end_node_id"]

    result = await port.turns.rollback(**kwargs)
    assert result.target_node_id == preview.target_node_id
    assert result.released_delivery_ids == expected_ids
    assert not result.already_applied
    assert cm.active_head.id == row["base_node_id"]
    assert cm.active_session_id == row["session_id"]
    assert cm.get_messages() != old_messages
    assert cm.message_store.current_messages == cm.get_messages(branch_id=result.branch_id)
    assert cm.get_current_state() == cm.active_head.agent_state
    assert not cm._pending_messages

    after = snapshot(backend.db_path)
    for table in (
        "context_schema",
        "mm_meta",
        "nodes",
        "chain_meta",
        "mm_delivery_results",
        "mm_messages",
        "mm_room_projections",
        "hitl_requests",
    ):
        assert after[table] == before[table], table
    assert len(after["branches"]) == len(before["branches"]) + 1
    assert after["branches"][:-1] == before["branches"]
    with closing(sqlite3.connect(backend.db_path)) as db:
        assert db.execute(
            "SELECT active_branch_id FROM sessions WHERE session_id=?", (row["session_id"],)
        ).fetchone() == (result.branch_id,)
        branch = db.execute(
            "SELECT session_id,head_node_id,parent_branch_id,fork_point_node_id,metadata "
            "FROM branches WHERE branch_id=?",
            (result.branch_id,),
        ).fetchone()
        assert branch[:4] == (
            row["session_id"],
            preview.target_node_id,
            preview.target_branch_id,
            preview.target_node_id,
        )
        assert json.loads(branch[4]) == {
            "is_rollback": True,
            "rollback_from_branch_id": row["end_branch_id"],
            "rollback_from_node_id": row["end_node_id"],
            "rollback_to_node_id": preview.target_node_id,
            "turn_id": row["turn_id"],
        }
        assert db.execute(
            "SELECT delivery_id,turn_id,delivery_policy,state,error FROM mm_deliveries "
            "WHERE delivery_id IN (?,?) ORDER BY seq",
            expected_ids,
        ).fetchall() == [
            (delivery_id, None, "queue", "pending", None) for delivery_id in expected_ids
        ]
    assert [d for d in after["mm_deliveries"] if d[1] not in expected_ids] == [
        d for d in before["mm_deliveries"] if d[1] not in expected_ids
    ]

    view = await port.turns.get(**kwargs)
    assert view.status == view.closing_kind == "rolled_back"
    for key in ("base_node_id", "start_node_id", "end_node_id", "end_branch_id", "closed_at"):
        assert getattr(view, key) == row[key]
    current = await backend.load_turn("agent", row["turn_id"])
    metadata = json.loads(current["metadata"])
    assert metadata["rollback_from_end_node_id"] == row["end_node_id"]
    assert metadata["rolled_back_at"] == result.rolled_back_at
    assert metadata["existing"] == "kept"
    assert (await port.turns.list(agent_id="agent", status="rolled_back")).items == [view]

    replay = await port.turns.rollback(**kwargs)
    assert replay == result.model_copy(update={"already_applied": True})
    with pytest.raises(CoreApiError) as conflict:
        await port.turns.rollback(**kwargs, target="node", node_id=row["start_node_id"])
    assert conflict.value.problem.code == ProblemCode.CONFLICT
    assert snapshot(backend.db_path) == after
    events = [call.args[0] for call in event_sink.await_args_list]
    assert [event.event_type.value for event in events] == [
        "branch_created",
        "branch_activated",
        "turn_closed",
    ]
    assert events[-1].end_node_id == row["end_node_id"]

    # 收尾完成后，真实调度器按 seq 领取回队输入，并从回滚点继续。
    agent._llm._replies.extend(["first", "second", "last"])
    await mm.dispatch_pending()
    with closing(sqlite3.connect(backend.db_path)) as db:
        first, second = db.execute(
            "SELECT turn_id, state FROM mm_deliveries WHERE delivery_id IN (?,?) ORDER BY seq",
            expected_ids,
        ).fetchall()
        assert (first[1], second[1]) == ("completed", "completed")
        assert first[0] != second[0]
        first_turn = await backend.load_turn("agent", first[0])
        second_turn = await backend.load_turn("agent", second[0])
        assert db.execute(
            "SELECT parent_id FROM nodes WHERE id=?", (first_turn["start_node_id"],)
        ).fetchone() == (preview.target_node_id,)
    assert first_turn["base_node_id"] == preview.target_node_id
    assert second_turn["base_node_id"] == first_turn["end_node_id"]


def append_node(cm, text):
    cm.begin_iteration()
    cm.add_messages([ChatMessage.user(text)])
    return cm.commit_iteration()


async def test_rollback_to_node_on_alternate_branch_in_inactive_session(rollback_runtime):
    agent, backend, _, port = rollback_runtime
    cm = agent._context_manager
    session = cm.active_session_id
    agent._supervisor.message_manager = None
    turn_id = await cm.open_turn()
    first = append_node(cm, "first")
    retry = cm.create_branch(session_id=session, name="retry", from_node_id=first.id)
    target = append_node(cm, "alternate")
    cm.activate_branch(session, retry.branch_id)
    end = append_node(cm, "end")
    await cm.persist()
    await cm.close_turn(status="failed", closing_kind="error", end_node_id=end.id)
    other = cm.create_session(name="other")
    cm.activate_session(other.session_id)
    await cm.persist()
    kwargs = {"agent_id": "agent", "turn_id": turn_id, "target": "node", "node_id": target.id}
    preview = await port.turns.rollback_preview(**kwargs)
    assert preview.truncated_node_count == 1
    assert preview.target_branch_id == target.created_on_branch_id
    assert preview.end_node_id == end.id
    assert preview.released_delivery_ids == []
    result = await port.turns.rollback(**kwargs)
    assert result.target_node_id == target.id
    assert result.parent_branch_id == target.created_on_branch_id
    assert result.released_delivery_ids == []
    assert cm.active_session_id == other.session_id
    assert cm.get_session(session).active_branch_id == result.branch_id
    checkpoint = await backend.load_checkpoint("agent")
    assert checkpoint.active_session_id == other.session_id
    assert (
        next(s for s in checkpoint.sessions if s.session_id == session).active_branch_id
        == result.branch_id
    )
    assert (await backend.load_turn("agent", turn_id))["end_node_id"] == end.id


@pytest.mark.parametrize(
    ("change", "reason", "code"),
    [
        ("missing", "turn_not_found", ProblemCode.NOT_FOUND),
        ("foreign_agent", "turn_not_found", ProblemCode.NOT_FOUND),
        ("open", "turn_active", ProblemCode.BUSY),
        ("waiting_hitl", "turn_active", ProblemCode.BUSY),
        ("live_iteration", "turn_busy", ProblemCode.BUSY),
        ("approval", "turn_has_pending_hitl", ProblemCode.FAILED_PRECONDITION),
        ("deleted_session", "turn_session_missing", ProblemCode.FAILED_PRECONDITION),
        ("zero", "turn_has_no_nodes", ProblemCode.FAILED_PRECONDITION),
        ("rootless", "turn_base_missing", ProblemCode.FAILED_PRECONDITION),
        ("other_turn", "rollback_target_not_found", ProblemCode.NOT_FOUND),
        ("other_session", "rollback_target_not_found", ProblemCode.NOT_FOUND),
        ("incomplete", "rollback_target_not_usable", ProblemCode.FAILED_PRECONDITION),
        ("deleted_branch", "target_branch_deleted", ProblemCode.FAILED_PRECONDITION),
    ],
)
async def test_rejected_rollback_and_preview_leave_database_unchanged(
    rollback_runtime, closed_turn, change, reason, code
):
    agent, backend, _, port = rollback_runtime
    cm = agent._context_manager
    row = closed_turn
    if change == "incomplete":
        turn_id = await cm.open_turn()
        append_node(cm, "usable target")
        end = append_node(cm, "non-selectable target")
        await cm.persist()
        await cm.close_turn(status="interrupted", closing_kind="cancelled", end_node_id=end.id)
        row = await backend.load_turn("agent", turn_id)

    kwargs = {"agent_id": "agent", "turn_id": row["turn_id"]}
    async with backend.transaction() as db:
        if change in ("open", "waiting_hitl"):
            await db.execute("UPDATE turns SET status=?", (change,))
        elif change == "approval":
            await db.execute(
                "INSERT INTO hitl_requests (request_id,promise_id,agent_name,session_id,turn_id,"
                "ability_name,tool_call_id,status,created_at) "
                "VALUES ('req','promise','agent',?,?,'tool','call','pending','now')",
                (row["session_id"], row["turn_id"]),
            )
        elif change == "deleted_session":
            await db.execute("UPDATE sessions SET lifecycle='deleted'")
        elif change == "zero":
            await db.execute("UPDATE turns SET start_node_id=NULL")
        elif change == "rootless":
            await db.execute("UPDATE turns SET base_node_id=NULL")
        elif change == "incomplete":
            await db.execute(
                "UPDATE nodes SET metadata=json_set(metadata,'$.node_kind',?) WHERE id=?",
                (change, row["end_node_id"]),
            )
            kwargs.update(target="node", node_id=row["end_node_id"])
        elif change == "deleted_branch":
            await db.execute("UPDATE branches SET lifecycle='deleted'")
        elif change == "other_session":
            await db.execute(
                "UPDATE nodes SET session_id='other' WHERE id=?", (row["start_node_id"],)
            )
            kwargs.update(target="node", node_id=row["start_node_id"])
        elif change == "other_turn":
            kwargs.update(target="node", node_id=row["base_node_id"])
        elif change == "missing":
            kwargs.update(turn_id="unknown")
        elif change == "foreign_agent":
            await db.execute("UPDATE turns SET agent_name='foreign'")
    if change == "live_iteration":
        cm.begin_iteration()
    before = snapshot(backend.db_path)
    for operation in (port.turns.rollback_preview, port.turns.rollback):
        with pytest.raises(CoreApiError) as rejected:
            await operation(**kwargs)
        assert rejected.value.problem.code == code
        assert rejected.value.problem.detail == reason
        if change == "incomplete":
            assert rejected.value.problem.details["suggested_node_ids"] == [row["start_node_id"]]
    assert snapshot(backend.db_path) == before


@pytest.mark.parametrize("cas_conflict", [False, True])
async def test_rollback_transaction_failure_is_atomic(
    rollback_runtime, closed_turn, monkeypatch, cas_conflict
):
    agent, backend, mm, port = rollback_runtime
    row = closed_turn
    await seed_residual_inputs(backend, mm, row)
    if cas_conflict:
        async with backend.transaction() as db:
            await db.execute(
                "CREATE TRIGGER prevent_rollback BEFORE UPDATE OF status ON turns "
                "WHEN NEW.status='rolled_back' BEGIN SELECT RAISE(IGNORE); END"
            )
    else:
        settlement = SqliteDeliverySettlement
        release = settlement.release_inputs_in_transaction

        async def fail_after_release(db, **kwargs):
            assert len(await release(db, **kwargs)) == 2
            raise RuntimeError("release failed")

        monkeypatch.setattr(
            settlement, "release_inputs_in_transaction", staticmethod(fail_after_release)
        )

    before = snapshot(backend.db_path)
    with pytest.raises(CoreApiError) as rejected:
        await port.turns.rollback(agent_id="agent", turn_id=row["turn_id"])
    assert rejected.value.problem.detail == (
        "rollback_conflict" if cas_conflict else "release failed"
    )
    assert snapshot(backend.db_path) == before
    assert agent._context_manager.active_head.id == row["end_node_id"]


async def test_live_turn_rejects_rollback_without_waiting_in_runner(rollback_runtime, monkeypatch):
    agent, backend, mm, port = rollback_runtime
    entered, resume = asyncio.Event(), asyncio.Event()
    ensure_llm = agent._ensure_llm

    async def pause():
        entered.set()
        await resume.wait()
        return await ensure_llm()

    monkeypatch.setattr(agent, "_ensure_llm", pause)
    await send(mm, "live turn")
    dispatch = asyncio.create_task(mm.dispatch_pending())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        kwargs = {"agent_id": "agent", "turn_id": agent._context_manager.active_turn_id}
        before = snapshot(backend.db_path)
        for operation in (port.turns.rollback_preview, port.turns.rollback):
            with pytest.raises(CoreApiError) as rejected:
                await asyncio.wait_for(operation(**kwargs), 2)
            assert rejected.value.problem.code == ProblemCode.BUSY
            assert rejected.value.problem.detail == "turn_active"
        assert snapshot(backend.db_path) == before
    finally:
        resume.set()
        await dispatch


async def test_mm_claim_blocks_rollback_before_agent_runner(
    rollback_runtime, closed_turn, monkeypatch
):
    agent, backend, mm, port = rollback_runtime
    claimed, resume = asyncio.Event(), asyncio.Event()
    receive = agent.receive_atomic

    async def pause(*args):
        claimed.set()
        await resume.wait()
        return await receive(*args)

    agent._llm._replies.append("next")
    monkeypatch.setattr(agent, "receive_atomic", pause)
    await send(mm, "claimed")
    dispatch = asyncio.create_task(mm.dispatch_pending())
    try:
        await asyncio.wait_for(claimed.wait(), 2)
        assert agent._context_manager.active_turn is None
        before = snapshot(backend.db_path)
        with pytest.raises(CoreApiError) as rejected:
            await port.turns.rollback(agent_id="agent", turn_id=closed_turn["turn_id"])
        assert rejected.value.problem.code == ProblemCode.BUSY
        assert rejected.value.problem.detail == "session_has_active_turn"
        assert snapshot(backend.db_path) == before
    finally:
        resume.set()
        await dispatch


async def test_cancelled_rollback_waits_for_committed_memory(
    rollback_runtime, closed_turn, monkeypatch
):
    agent, backend, mm, port = rollback_runtime
    entered, finish = asyncio.Event(), asyncio.Event()
    settlement = SqliteDeliverySettlement
    release = settlement.release_inputs_in_transaction

    async def pause(db, **kwargs):
        entered.set()
        await finish.wait()
        return await release(db, **kwargs)

    monkeypatch.setattr(settlement, "release_inputs_in_transaction", staticmethod(pause))
    kwargs = {"agent_id": "agent", "turn_id": closed_turn["turn_id"]}
    operation = asyncio.create_task(port.turns.rollback(**kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        operation.cancel()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    replay = await port.turns.rollback(**kwargs)
    assert replay.already_applied
    assert agent._context_manager.active_head.id == replay.target_node_id
    checkpoint = await backend.load_checkpoint("agent")
    (session,) = checkpoint.sessions
    assert session.active_branch_id == replay.branch_id


async def test_concurrent_rollbacks_create_one_branch(rollback_runtime, closed_turn, monkeypatch):
    agent, backend, _, port = rollback_runtime
    event_sink = AsyncMock()
    monkeypatch.setattr(agent._event_publisher, "publish", event_sink)
    before = snapshot(backend.db_path)
    kwargs = {"agent_id": "agent", "turn_id": closed_turn["turn_id"]}
    results = await asyncio.gather(port.turns.rollback(**kwargs), port.turns.rollback(**kwargs))
    assert results[0].branch_id == results[1].branch_id
    assert {result.already_applied for result in results} == {False, True}
    assert len(snapshot(backend.db_path)["branches"]) == len(before["branches"]) + 1
    assert event_sink.await_count == 3


async def test_rollback_recovers_when_execution_stops_before_events(
    rollback_runtime, closed_turn, monkeypatch
):
    agent, backend, mm, port = rollback_runtime
    row = closed_turn
    ids = await seed_residual_inputs(backend, mm, row)
    event_sink = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(agent._event_publisher, "publish", event_sink)
    kwargs = {"agent_id": "agent", "turn_id": row["turn_id"]}
    with pytest.raises(asyncio.CancelledError):
        await port.turns.rollback(**kwargs)
    async with mm.context_backend(None) as reopened:
        cm = ContextManager("agent", persistence=reopened)
        await cm.restore("agent")
        persisted = await reopened.load_turn("agent", row["turn_id"])
        assert persisted["status"] == "rolled_back"
        branch = cm.get_active_session_branch()
        assert branch.branch_id == json.loads(persisted["metadata"])["rollback_branch_id"]
        assert cm.active_head.id == row["base_node_id"]
        replay = await cm.rollback_turn(turn_id=row["turn_id"], target="base")
        assert replay.as_payload()["released_delivery_ids"] == [
            ids["released-first"],
            ids["released-second"],
        ]
        assert replay.as_payload()["already_applied"]


@pytest.mark.parametrize("target", ["base", "node"])
async def test_rollback_accepts_compact_as_member_or_base(rollback_runtime, target):
    agent, backend, _, port = rollback_runtime
    cm = agent._context_manager
    base = cm.active_head
    if target == "base":
        base = cm.commit_compact_node([])
    turn_id = await cm.open_turn()
    point = cm.commit_compact_node([]) if target == "node" else base
    await cm.persist()
    view = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert view.base_node_id == (base.id if target == "node" else None)
    end = point if target == "node" else append_node(cm, "input")
    await cm.persist()
    await cm.close_turn(status="interrupted", closing_kind="cancelled", end_node_id=end.id)
    view = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert view.base_node_id == base.id
    assert view.start_node_id == view.end_node_id == end.id

    kwargs = {"agent_id": "agent", "turn_id": turn_id, "target": target}
    if target == "node":
        kwargs["node_id"] = point.id
        base_preview = await port.turns.rollback_preview(agent_id="agent", turn_id=turn_id)
        assert base_preview.crosses_compact
    preview = await port.turns.rollback_preview(**kwargs)
    assert preview.target_node_id == point.id
    assert preview.target_is_compact
    assert not preview.crosses_compact
    result = await port.turns.rollback(**kwargs)
    assert result.target_node_id == cm.active_head.id == point.id
    assert (await backend.load_turn("agent", turn_id))["end_node_id"] == end.id
