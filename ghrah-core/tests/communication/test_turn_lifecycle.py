# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实 SQLite 绑定下的轮次终态与投递保序。"""

import asyncio
import sqlite3

import pytest
from _helpers import ScriptedLLM

from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.chat.message import ChatMessage
from ghrah.communication import DeliveryState, MessageManager
from ghrah.context.manager import DeliverySessionUnavailableError
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.core.message import AgentMessage
from ghrah.types.config_types import ContextConfig


@pytest.fixture
async def runtime(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        agent = AgentBuilder.from_config(
            AgentConfig(
                name="display", agent_id="agent", context=ContextConfig(auto_persist=False)
            ),
            abilities=[ConversationAbility()],
            llm_factory=lambda _: ScriptedLLM(["done"]),
            persistence_factory=lambda _: backend,
        )
        await agent._context_manager.persist()
        async with MessageManager(
            path, project_id="p", agent_ids=("agent",), session_routing=True
        ) as mm:
            await mm.bind_atomic(agent)
            yield agent, backend, mm


async def send(mm, *, key="key", metadata=None):
    return await mm.send_external(
        sender="user",
        recipient_ids=("agent",),
        content="input",
        idempotency_key=key,
        metadata=metadata,
    )


async def test_mm_turn_closes_with_opener_and_nodes(runtime):
    agent, backend, mm = runtime
    cm = agent._context_manager
    base = cm.active_head.id
    request = await send(mm)
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state == DeliveryState.COMPLETED
    assert delivery.turn_id is not None
    (row,) = await backend.list_turns("agent", cm.active_session_id, None, 50)
    assert row["status"] == "closed"
    assert row["closing_kind"] == "completed"
    assert row["turn_id"] == delivery.turn_id
    assert row["opener_delivery_id"] == delivery.delivery_id
    assert row["base_node_id"] == base
    assert row["start_node_id"] == delivery.consumed_node_id
    assert row["end_node_id"] == delivery.last_node_id == cm.active_head.id
    assert row["branch_id"] == row["end_branch_id"] == cm.get_active_session_branch().branch_id
    checkpoint = await backend.load_checkpoint("agent")
    assert all(node.turn_id == delivery.turn_id for node in checkpoint.nodes if node.id != base)
    assert cm.active_turn_id is None


async def test_claim_failure_rolls_back_turn_before_agent_runs(runtime):
    agent, backend, mm = runtime
    session_id = agent._context_manager.active_session_id
    async with mm._transaction() as db:
        await db.execute(
            "CREATE TRIGGER reject_claim BEFORE UPDATE OF state ON mm_deliveries "
            "WHEN NEW.state='processing' BEGIN SELECT RAISE(ABORT, 'claim failed'); END"
        )
    request = await send(mm)
    await mm.dispatch_pending()
    (failed,) = await mm.get_deliveries(request.message_id)
    assert failed.state == DeliveryState.FAILED and failed.error == "claim failed"
    assert (failed.attempts, failed.turn_id, failed.consumed_node_id) == (0, None, None)
    assert await backend.list_turns("agent", session_id, None, 50) == []
    assert agent._context_manager.active_turn_id is None
    assert await mm.get_replies(request.message_id) == ()

    async with mm._transaction() as db:
        await db.execute("DROP TRIGGER reject_claim")
    await mm.retry(failed.delivery_id)
    await mm.dispatch_pending()
    (completed,) = await mm.get_deliveries(request.message_id)
    assert completed.state == DeliveryState.COMPLETED and completed.attempts == 1


async def test_failed_zero_node_turn_releases_queue(runtime):
    agent, backend, mm = runtime
    head = agent._context_manager.active_head.id
    broken = await send(
        mm,
        metadata={"execute_ability": {"name": "missing", "args": {}, "call_id": "broken"}},
    )
    later = await send(mm, key="later")
    await mm.dispatch_pending()
    (failed,) = await mm.get_deliveries(broken.message_id)
    (completed,) = await mm.get_deliveries(later.message_id)
    assert (failed.state, completed.state) == (DeliveryState.FAILED, DeliveryState.COMPLETED)
    row = await backend.load_turn("agent", failed.turn_id)
    assert row["status"] == "failed"
    assert row["start_node_id"] is None
    assert row["base_node_id"] == row["end_node_id"] == head
    assert failed.turn_id != completed.turn_id
    assert (await mm.get_replies(later.message_id))[0].content == "done"


@pytest.mark.parametrize("unavailable", [False, True])
async def test_turn_terminal_on_cancellation_or_session_unavailable(
    runtime, monkeypatch, unavailable
):
    agent, backend, mm = runtime
    entered = asyncio.Event()

    async def receive_atomic(*args):
        entered.set()
        if unavailable:
            raise DeliverySessionUnavailableError("Session became unavailable")
        await asyncio.Event().wait()

    monkeypatch.setattr(agent, "receive_atomic", receive_atomic)
    request = await send(mm)
    await asyncio.wait_for(entered.wait(), 1)
    if unavailable:
        await mm.dispatch_pending()
    else:
        await mm.unbind("agent")
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state == (DeliveryState.BLOCKED if unavailable else DeliveryState.INTERRUPTED)
    (row,) = await backend.list_turns("agent", None, None, 50)
    assert row["opener_delivery_id"] == delivery.delivery_id
    assert row["status"] == "interrupted"
    assert row["closing_kind"] == "cancelled"
    assert row["start_node_id"] is None
    assert row["base_node_id"] == row["end_node_id"] == agent._context_manager.active_head.id


async def test_direct_receive_failure_closes_standalone_turn(runtime, monkeypatch):
    agent, backend, _ = runtime

    async def fail(message):
        raise RuntimeError("failure before model")

    monkeypatch.setattr(agent, "_execute_received_message", fail)
    with pytest.raises(RuntimeError, match="failure before model"):
        await agent.receive(AgentMessage(sender="user", recipient="display", content="input"))
    (row,) = await backend.list_turns("agent", None, None, 50)
    assert row["status"] == "failed"
    assert row["start_node_id"] is None
    assert agent._context_manager.active_turn_id is None


async def test_message_ability_command_uses_a_turn_and_emits_scoped_events(runtime):
    from ghrah.core.unit import UnitEventPublisher

    agent, backend, mm = runtime
    emitted = []
    agent.set_event_publisher(
        UnitEventPublisher(
            lambda name, frame: emitted.append((name, frame)),
            agent_id_resolver=lambda name: {"display": "agent"}[name],
        )
    )
    command = await send(
        mm,
        metadata={"execute_ability": {"name": "conversation", "args": {}, "call_id": "call"}},
    )
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(command.message_id)
    assert delivery.state == DeliveryState.COMPLETED
    row = await backend.load_turn("agent", delivery.turn_id)
    assert row["status"] == "closed"
    assert row["start_node_id"] == row["end_node_id"] == delivery.consumed_node_id
    payloads = {name: frame for name, frame in emitted if name.startswith("core:turn_")}
    assert set(payloads) == {"core:turn_opened", "core:turn_closed"}
    for payload in payloads.values():
        assert (payload["agent_id"], payload["turn_id"]) == ("agent", delivery.turn_id)


async def test_claim_failure_terminates_delivery_without_hot_loop(runtime, monkeypatch):
    """领取事务异常后投递必须终结，避免调度器反复领取同一行。"""
    agent, backend, mm = runtime
    from ghrah.context import turn as turn_module

    async def fail_normalize(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(turn_module, "normalize_open_turns", fail_normalize)
    request = await send(mm)
    await asyncio.wait_for(mm.dispatch_pending(), 3)
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state == DeliveryState.FAILED
    assert "database is locked" in (delivery.error or "")
    # 再扫一次不应重新领取该行。
    attempts_before = delivery.attempts
    await asyncio.wait_for(mm.dispatch_pending(), 3)
    assert (await mm.get_deliveries(request.message_id))[0].attempts == attempts_before


async def test_atomic_claim_requires_turn_schema(runtime):
    """缺少轮次表时保留待收投递，迁移完成后按正常轮次领取。"""
    agent, backend, mm = runtime
    await mm.close()
    await mm.open(start_dispatcher=False)
    await mm.bind_atomic(agent)
    with sqlite3.connect(backend.db_path) as db:
        db.executescript(
            "DROP TABLE IF EXISTS hitl_requests; DROP TABLE IF EXISTS turns; "
            "DROP INDEX IF EXISTS idx_nodes_turn;"
        )
    request = await send(mm)
    with pytest.raises(RuntimeError, match="Durable turn schema is required"):
        await mm.dispatch_pending()
    (pending,) = await mm.get_deliveries(request.message_id)
    assert (pending.state, pending.attempts, pending.turn_id) == (DeliveryState.PENDING, 0, None)
    async with SqliteBackend(backend.db_path) as migrated:
        await migrated.initialize()
    await asyncio.wait_for(mm.dispatch_pending(), 3)
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state == DeliveryState.COMPLETED
    assert delivery.turn_id is not None


async def test_consumed_session_loss_closes_turn_interrupted(runtime, monkeypatch):
    """已消费输入在会话丢失时：投递按既有语义 failed，轮次收敛为 interrupted。"""
    agent, backend, mm = runtime
    cm = agent._context_manager

    async def consume_then_lose_session(message):
        cm.begin_iteration()
        cm.add_messages(
            [
                ChatMessage.user(
                    "input",
                    source="user",
                    metadata={
                        "delivery_id": message.metadata.get("delivery_id"),
                        "message_id": message.metadata.get("message_id"),
                    },
                )
            ]
        )
        await cm.commit_iteration_async()
        raise DeliverySessionUnavailableError("Session became unavailable")

    monkeypatch.setattr(agent, "_execute_received_message", consume_then_lose_session)
    request = await send(mm)
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state == DeliveryState.FAILED
    assert delivery.consumed_node_id is not None
    row = await backend.load_turn("agent", delivery.turn_id)
    assert row["status"] == "interrupted"
    assert row["closing_kind"] == "cancelled"
    assert row["start_node_id"] == delivery.consumed_node_id
