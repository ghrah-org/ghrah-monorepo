# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""审批持久事实与实际 Future 回程。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, ExecutionServices
from ghrah.ability.execution.executor import LocalAbilityExecutor
from ghrah.ability.execution.hooks import HookResult
from ghrah.ability.execution.invocation import MemberReference, OperationCall
from ghrah.communication import MessageManager
from ghrah.context import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.events import HITLRequestEvent, HITLResolvedEvent, TurnWaitingHitlEvent
from ghrah.core.unit import CoreUnit, CoreUnitConfig
from ghrah.core.wiring import build_core_port


def approval(executor, cm, call_id):
    return executor.handle_hitl_hook_result(
        HookResult(should_continue=False, requires_hitl=True, message="approval required"),
        OperationCall(member=MemberReference(member="tool"), tool_call_id=call_id),
        ExecutionServices({CONTEXT_MANAGER: cm}),
    )


async def test_pending_visible_before_events_and_resolution_is_idempotent(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        async with MessageManager(path, project_id="p", agent_ids=("agent",)) as mm:
            turn_id = await cm.open_turn()
            port = build_core_port(scope_id="p", db_path=str(path))
            unit = CoreUnit(CoreUnitConfig(message_manager=mm))
            events = []
            requests = asyncio.Queue()

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
                assert {e.status for e in events if isinstance(e, HITLResolvedEvent)} == {
                    "approved",
                    "rejected",
                }
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


async def test_no_active_turn_keeps_standalone_hitl_in_memory(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager("agent", persistence=backend)
        await cm.persist()
        executor = LocalAbilityExecutor("display", hitl_timeout=0)
        result = await approval(executor, cm, "call")
        assert result.outcome == "timeout"
        assert await backend.load_hitl_request("agent", result.promise_id) is None
