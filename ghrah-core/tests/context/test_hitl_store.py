# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""审批持久事实与实际 Future 回程。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, ExecutionServices
from ghrah.ability.execution.executor import LocalAbilityExecutor
from ghrah.ability.execution.hooks import HookResult
from ghrah.ability.execution.invocation import MemberReference, OperationCall
from ghrah.ability.permissions.hitl import HITLApprovalHook
from ghrah.communication import MessageManager
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.events import HITLRequestEvent, HITLResolvedEvent, TurnWaitingHitlEvent
from ghrah.core.unit import CoreUnit, CoreUnitConfig
from ghrah.core.wiring import build_core_port
from ghrah.types.results import ActionOutcome, ActionResult


def approval(executor, cm, call_id):
    return executor.handle_hitl_hook_result(
        HookResult(should_continue=False, requires_hitl=True, message="approval required"),
        OperationCall(member=MemberReference(member="tool"), tool_call_id=call_id),
        ExecutionServices({CONTEXT_MANAGER: cm}),
    )


async def test_pending_visible_before_events_and_resolution_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        async with MessageManager(path, project_id="p", agent_ids=("agent",)) as mm:
            turn_id = await cm.open_turn()
            port = build_core_port(scope_id="p", db_path=str(path))
            unit = CoreUnit(CoreUnitConfig(message_manager=mm))
            events = []
            decisions = []
            requests = asyncio.Queue()
            monkeypatch.setattr(unit, "_emit_event", lambda _, payload: decisions.append(payload))

            async def publish(event):
                events.append(event)
                if isinstance(event, HITLRequestEvent):
                    view = await port.hitl.get(agent_id="agent", promise_id=event.promise_id)
                    assert view.status == "pending"
                    assert view.deadline_at == event.deadline_at
                    assert event.turn_id == view.turn_id == turn_id
                    assert (await port.turns.get(agent_id="agent", turn_id=turn_id)).status == (
                        "waiting_hitl"
                    )
                    requests.put_nowait(event)

            executor = LocalAbilityExecutor(
                "display",
                event_publisher=SimpleNamespace(publish=publish),
                hitl_promise_registry=unit._hitl_promises,
                hitl_timeout=2,
            )
            unit._supervisor = SimpleNamespace(
                get_agent_handle=AsyncMock(
                    return_value=SimpleNamespace(_context_manager=cm, _ability_executor=executor)
                )
            )
            try:
                async with asyncio.TaskGroup() as group:
                    first = group.create_task(approval(executor, cm, "one"))
                    second = group.create_task(approval(executor, cm, "two"))
                    received = [await asyncio.wait_for(requests.get(), 1) for _ in range(2)]
                    by_call = {event.context["tool_call_id"]: event for event in received}
                    pending = await port.hitl.list_pending(
                        agent_id="agent", session_id=cm.active_session_id
                    )
                    assert {item.promise_id for item in pending.items} == {
                        event.promise_id for event in received
                    }
                    assert sum(isinstance(e, TurnWaitingHitlEvent) for e in events) == 1

                    payload = {"promise_id": by_call["one"].promise_id, "approved": True}
                    outcome = await unit._handle_hitl_response(payload, None)
                    assert outcome["data"] == {"resolved": True}
                    duplicate = await unit._handle_hitl_response(payload, None)
                    assert duplicate["data"] == {"resolved": False, "status": "already_resolved"}
                    assert (await port.turns.get(agent_id="agent", turn_id=turn_id)).status == (
                        "waiting_hitl"
                    )
                    payload = {
                        "promise_id": by_call["two"].promise_id,
                        "approved": False,
                        "reason": "denied",
                    }
                    assert (await unit._handle_hitl_response(payload, None))["data"]["resolved"]
                assert first.result().outcome == "approved"
                assert second.result().outcome == "rejected"
                assert second.result().reason == {"reason": "denied"}
                assert (await port.hitl.list_pending(agent_id="agent")).items == []
                assert (await port.turns.get(agent_id="agent", turn_id=turn_id)).status == "open"
                assert [event["status"] for event in decisions] == ["approved", "rejected"]
                assert not any(isinstance(e, HITLResolvedEvent) for e in events)
            finally:
                await port.aclose()


async def test_executor_timeout_expires_before_resolved_event(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        turn_id = await cm.open_turn()
        resolved = []

        async def publish(event):
            if isinstance(event, HITLResolvedEvent):
                assert event.status == "timeout"
                assert event.turn_id == turn_id
                assert (await backend.load_turn("agent", turn_id))["status"] == "open"
                row = await backend.load_hitl_request("agent", event.promise_id)
                assert row["status"] == "expired"
                assert row["resolved_at"]
                resolved.append(event.promise_id)

        executor = LocalAbilityExecutor(
            "display", event_publisher=SimpleNamespace(publish=publish), hitl_timeout=0.01
        )
        outcome = await approval(executor, cm, "call")
        assert outcome.outcome == "timeout"
        assert resolved == [outcome.promise_id]


async def test_turn_close_cancels_pending_and_startup_orphans_other_requests(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()

        async def request(promise, deadline):
            result = await cm.open_hitl_request(
                promise_id=promise,
                ability_name="tool",
                tool_call_id=promise,
                tool_call={},
                reason="",
                options={},
                deadline_at=deadline,
            )
            assert result.persisted

        await cm.open_turn()
        await request("cancel", "2199-01-01T00:00:00+00:00")
        await cm.close_turn(status="closed", closing_kind="completed")

        active_turn = await cm.open_turn()
        await request("orphan", "2199-01-01T00:00:00+00:00")
        await request("late", "2000-01-01T00:00:00+00:00")
        async with MessageManager(path, project_id="p", agent_ids=("agent",)) as mm:
            assert (await backend.load_turn("agent", active_turn))["status"] == "interrupted"
            expected = {"cancel": "cancelled", "orphan": "orphaned", "late": "expired"}
            for promise, status in expected.items():
                row = await backend.load_hitl_request("agent", promise)
                assert row["status"] == status
            result = await cm.resolve_hitl_request(
                "orphan", approved=True, resolved_by=None, resolution=None
            )
            assert result.persisted and not result.changed and result.status == "orphaned"
            unit = CoreUnit(CoreUnitConfig(message_manager=mm))
            unit._supervisor = SimpleNamespace()
            ack = await unit._handle_hitl_response({"promise_id": "orphan", "approved": True}, None)
            assert ack["data"] == {"resolved": False, "status": "already_resolved"}


async def test_approval_committed_before_local_timeout_takes_precedence(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        await cm.open_turn()

        async def publish(event):
            if isinstance(event, HITLRequestEvent):
                decided = await cm.resolve_hitl_request(
                    event.promise_id, approved=True, resolved_by="operator", resolution={"ok": True}
                )
                assert decided.changed

        # 模拟已经提交审批，但回包尚未唤醒内存 Future。
        executor = LocalAbilityExecutor(
            "display", event_publisher=SimpleNamespace(publish=publish), hitl_timeout=0.01
        )
        result = await approval(executor, cm, "call")
        assert result.outcome == "approved"
        assert result.reason == {"ok": True}
        assert (await backend.load_hitl_request("agent", result.promise_id))["status"] == "approved"


@pytest.mark.parametrize("fault", ["no_active_turn", "insert"])
async def test_unconfirmed_approval_registration_fails_before_publishing(tmp_path, fault):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        if fault == "insert":
            turn_id = await cm.open_turn()
            async with backend.transaction() as db:
                await db.execute(
                    "CREATE TRIGGER fail_hitl_register BEFORE INSERT ON hitl_requests "
                    "BEGIN SELECT RAISE(ABORT, 'injected'); END"
                )
        publish = AsyncMock()
        executor = LocalAbilityExecutor("display", event_publisher=SimpleNamespace(publish=publish))
        result = await approval(executor, cm, "call")
        assert result.outcome == "storage_error"
        publish.assert_not_awaited()
        assert await backend.load_hitl_request("agent", result.promise_id) is None
        if fault == "insert":
            assert (await backend.load_turn("agent", turn_id))["status"] == "open"


async def test_failed_approval_write_does_not_wake_or_execute_tool(tmp_path, monkeypatch):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        turn_id = await cm.open_turn()
        # 让审批 UPDATE 成功，再在同一事务的轮次转换处失败，验证回滚与回执。
        async with backend.transaction() as db:
            await db.execute(
                "CREATE TRIGGER fail_hitl_finish BEFORE UPDATE OF status ON turns "
                "WHEN NEW.status='open' BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )

        unit = CoreUnit(CoreUnitConfig())
        emit = Mock()
        monkeypatch.setattr(unit, "_emit_event", emit)
        events = asyncio.Queue()

        async def publish(event):
            if isinstance(event, HITLRequestEvent):
                events.put_nowait(event)

        executor = LocalAbilityExecutor(
            "display",
            hooks=[HITLApprovalHook("tool", "approval required")],
            event_publisher=SimpleNamespace(publish=publish),
            hitl_promise_registry=unit._hitl_promises,
            hitl_timeout=10,
        )
        unit._supervisor = SimpleNamespace(
            get_agent_handle=AsyncMock(
                return_value=SimpleNamespace(_context_manager=cm, _ability_executor=executor)
            )
        )
        effect = AsyncMock(return_value=ActionResult(outcome=ActionOutcome.SUCCESS))
        tool = SimpleNamespace(name="tool", execute=effect)
        call = OperationCall(member=MemberReference(member="tool"), tool_call_id="one")

        async with asyncio.TaskGroup() as group:
            execution = group.create_task(
                executor.execute_ability(tool, call, ExecutionServices({CONTEXT_MANAGER: cm}))
            )
            event = await asyncio.wait_for(events.get(), 2)
            payload = {"promise_id": event.promise_id, "approved": True}
            ack = await unit.handle_command("hitl_response", payload, None)
            assert ack == {"success": False, "error": "Could not confirm persistent HITL decision"}
            row = await backend.load_hitl_request("agent", event.promise_id)
            assert row["status"] == "pending"
            assert (await backend.load_turn("agent", turn_id))["status"] == "waiting_hitl"
            assert not execution.done()
            effect.assert_not_awaited()
            emit.assert_not_called()

            async with backend.transaction() as db:
                await db.execute("DROP TRIGGER fail_hitl_finish")
            ack = await unit.handle_command("hitl_response", payload, None)
            assert ack["data"] == {"resolved": True}
            result = await asyncio.wait_for(execution, 2)
            assert result.outcome == ActionOutcome.SUCCESS
            row = await backend.load_hitl_request("agent", event.promise_id)
            assert row["status"] == "approved"
            effect.assert_awaited_once()
            emit.assert_called_once()


async def test_memory_wake_cannot_approve_a_persistent_request(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        await cm.open_turn()

        async def publish(event):
            if isinstance(event, HITLRequestEvent):
                assert executor.receive_hitl_response("tool", "call", True)

        executor = LocalAbilityExecutor("display", event_publisher=SimpleNamespace(publish=publish))
        result = await approval(executor, cm, "call")
        assert result.outcome == "storage_error"
        assert (await backend.load_hitl_request("agent", result.promise_id))["status"] == "pending"


async def test_unconfirmed_expiry_does_not_count_as_timeout(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        await cm.open_turn()
        async with backend.transaction() as db:
            await db.execute(
                "CREATE TRIGGER fail_hitl_expiry BEFORE UPDATE OF status ON hitl_requests "
                "WHEN NEW.status='expired' BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
        publish = AsyncMock()
        executor = LocalAbilityExecutor(
            "display", event_publisher=SimpleNamespace(publish=publish), hitl_timeout=0
        )
        result = await approval(executor, cm, "call")
        assert result.outcome == "storage_error"
        assert executor._consecutive_hitl_timeouts == 0
        assert (await backend.load_hitl_request("agent", result.promise_id))["status"] == "pending"
        assert not any(
            isinstance(args.args[0], HITLResolvedEvent) for args in publish.await_args_list
        )
