# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""压缩的归属、领取竞态、提交资格与续跑。"""

import asyncio

import pytest
from test_atomic_compaction import CompactLLM, seeded_agent
from test_turn_rollback import append_node, send, snapshot

from ghrah.communication import MessageManager
from ghrah.communication.supervisor import SupervisorActor
from ghrah.context import ContextManager, turn
from ghrah.context.compact import COMPACT_SUMMARY_PROMPT
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.message import AgentMessage

pytest_plugins = ["test_turn_rollback"]


class PausedSummary(CompactLLM):
    """只暂停第一次摘要调用，测试可在该边界提交中止或领取。"""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def generate(self, messages, tools=None):
        if messages[0].text == COMPACT_SUMMARY_PROMPT and not self.entered.is_set():
            self.entered.set()
            await self.resume.wait()
        return await super().generate(messages, tools)


async def send_to_b(mm, key):
    return await mm.send_external(
        sender="user", recipient_ids=("b",), content=key, idempotency_key=key
    )


@pytest.mark.parametrize("when", ["before_attach", "during_idle_summary"])
async def test_claim_moves_idle_compaction_into_claimed_turn(tmp_path, when, monkeypatch):
    path = tmp_path / "core.sqlite"
    llm = PausedSummary()
    async with SqliteBackend(path) as backend:
        agent = await seeded_agent(backend, llm)
        cm = agent._context_manager
        await cm.persist()
        mm = MessageManager(path, project_id="p", agent_ids=("b",), session_routing=True)
        await mm.open(start_dispatcher=False)
        agent._supervisor = SupervisorActor(message_manager=mm)
        await mm.bind_atomic(agent)
        claimed, attach = asyncio.Event(), asyncio.Event()
        original_receive = agent.receive_atomic

        async def receive(*args):
            claimed.set()
            await attach.wait()
            return await original_receive(*args)

        monkeypatch.setattr(agent, "receive_atomic", receive)
        base = cm.active_head.id
        operation = None
        try:
            if when == "during_idle_summary":
                llm._replies.insert(0, "discarded idle summary")
                operation = asyncio.create_task(agent.request_compact())
                await asyncio.wait_for(llm.entered.wait(), 3)

            request = await send_to_b(mm, "input")
            dispatch = asyncio.create_task(mm.dispatch_pending())
            await asyncio.wait_for(claimed.wait(), 3)
            assert cm.active_turn is None
            assert await mm.has_active_turn("b")
            if operation is None:
                receipt = await agent.request_compact()
            else:
                llm.resume.set()
                receipt = await asyncio.wait_for(operation, 3)
            assert receipt["scheduled"]
            assert cm.active_head.id == base
            assert cm.compact_requested
            llm.resume.set()
            attach.set()
            await asyncio.wait_for(dispatch, 3)
        finally:
            llm.resume.set()
            attach.set()
            if operation is not None:
                await asyncio.gather(operation, return_exceptions=True)
            await mm.close()

        (row,) = await backend.list_turns("b", None, None, 10)
        cp = await backend.load_node("b", row["start_node_id"])
        assert cp.metadata["node_kind"] == "compact" and cp.turn_id == row["turn_id"]
        assert row["base_node_id"] == base and row["end_node_id"] != cp.id
        end = await backend.load_node("b", row["end_node_id"])
        assert end.parent_id == cp.id
        assert any(m.metadata.get("message_id") == request.message_id for m in end.messages_delta)
        assert not cm.compact_requested


@pytest.mark.parametrize("with_mm", [False, True])
async def test_aborting_summary_prevents_compact_commit(tmp_path, with_mm):
    path = tmp_path / "core.sqlite"
    llm = PausedSummary()
    async with SqliteBackend(path) as backend:
        agent = await seeded_agent(backend, llm)
        cm = agent._context_manager
        base = cm.active_head
        cm.request_compact()
        mm = None
        if with_mm:
            mm = MessageManager(path, project_id="p", agent_ids=("b",), session_routing=True)
            await mm.open(start_dispatcher=False)
            agent._supervisor = SupervisorActor(message_manager=mm)
            await mm.bind_atomic(agent)
            opener = await send_to_b(mm, "aborted")
            drive = asyncio.create_task(mm.dispatch_pending())
        else:
            drive = asyncio.create_task(
                agent.receive(AgentMessage(sender="user", recipient="b", content="aborted"))
            )
        try:
            await asyncio.wait_for(llm.entered.wait(), 3)
            turn_id = cm.active_turn_id
            receipt = await agent.abort_turn(turn_id=turn_id)
            assert receipt["aborted"] and receipt["incomplete_node_id"] is None
            before = snapshot(path) if mm is not None else None
            with pytest.raises(turn.TurnRevokedError):
                cm.commit_compact_node([])
            llm.resume.set()
            if mm is None:
                with pytest.raises(turn.TurnRevokedError):
                    await asyncio.wait_for(drive, 3)
            else:
                await asyncio.wait_for(drive, 3)
                (delivery,) = await mm.get_deliveries(opener.message_id)
                assert delivery.state.value == "interrupted" and not delivery.consumed_node_id
                assert snapshot(path) == before
            assert cm.active_head == base
            assert not cm.compact_requested
            assert agent._message_queue.empty()
            row = await backend.load_turn("b", turn_id)
            assert row["status"] == "aborted" and row["start_node_id"] is None
            if mm is not None:
                await send_to_b(mm, "next")
                await mm.dispatch_pending()
                assert cm.active_head.metadata.get("node_kind") != "compact"
        finally:
            llm.resume.set()
            await asyncio.gather(drive, return_exceptions=True)
            if mm is not None:
                await mm.close()


async def test_idle_compact_is_durable_and_is_only_base_of_empty_turn(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        agent = await seeded_agent(backend, CompactLLM())
        cm = agent._context_manager
        receipt = await agent.request_compact()
        cp = await backend.load_node("b", receipt["node_id"])
        assert cp.id == cm.active_head.id and cp.turn_id == ""
        assert cp.metadata["node_kind"] == "compact"
        assert not await backend.list_turns("b", None, None, 10)
        turn_id = await cm.open_turn()
        await cm.close_turn(status="failed", closing_kind="error")
        row = await backend.load_turn("b", turn_id)
        assert row["base_node_id"] == cp.id
        assert row["start_node_id"] is row["end_node_id"] is None


async def test_resume_interrupted_from_compact_checkpoint(rollback_runtime):
    agent, backend, mm, _ = rollback_runtime
    cm = agent._context_manager
    opener = await send(mm, "original payload")
    delivery_id = opener.deliveries[0].delivery_id
    turn_id = await cm.open_turn(opener_delivery_id=delivery_id)
    action = append_node(cm, "already-consumed history")
    compact = cm.commit_compact_node([])
    await cm.persist()
    await cm.close_turn(status="interrupted", closing_kind="cancelled", end_node_id=compact.id)
    async with backend.transaction() as db:
        await db.execute(
            "UPDATE mm_deliveries SET state='interrupted',turn_id=?,consumed_node_id=?,"
            "last_node_id=?,consumed_session_id=? WHERE delivery_id=?",
            (turn_id, action.id, compact.id, compact.session_id, delivery_id),
        )
    await mm.resume_interrupted(
        delivery_id, expected_last_node_id=compact.id, acknowledge_side_effect_risk=True
    )
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(opener.message_id)
    assert delivery.state.value == "completed"
    assert delivery.consumed_node_id == action.id
    assert cm.active_head.parent_id == compact.id
    assert cm.active_head.metadata["delivery_context"] == {
        "agent_id": "agent",
        "message_manager": True,
    }
    assert not any(message.text == "original payload" for message in cm.active_head.messages_delta)


async def test_crash_after_only_compact_uses_member_as_end(rollback_runtime):
    agent, backend, mm, _ = rollback_runtime
    cm = agent._context_manager
    branches = cm.list_branches(cm.active_session_id)
    turn_id = await cm.open_turn()
    compact = cm.commit_compact_node([])
    await cm.persist()
    await mm.close()
    await mm.open(start_dispatcher=False)
    restored = ContextManager("agent", persistence=backend)
    await restored.restore("agent")
    row = await backend.load_turn("agent", turn_id)
    assert row["status"] == "interrupted"
    assert row["start_node_id"] == row["end_node_id"] == compact.id
    assert restored.active_head.id == compact.id
    assert len(restored.list_branches(restored.active_session_id)) == len(branches)


@pytest.mark.parametrize("entry", ["receive", "resume"])
async def test_atomic_entry_early_failure_clears_compact_request(
    rollback_runtime, monkeypatch, entry
):
    """未进入驱动循环的原子接收/续跑失败也要丢弃压缩请求，避免泄漏到下一轮。"""
    agent, _, _, _ = rollback_runtime
    cm = agent._context_manager
    message = AgentMessage(sender="user", recipient="agent", content="go", metadata={})

    async def committer(changes):
        raise AssertionError("must not commit")

    def rollback():
        pass

    async def finish(result):
        return None

    cm.request_compact()
    if entry == "receive":
        with pytest.raises(ValueError):
            await agent.receive_atomic(message, committer, target_session_id="missing-session")
    else:
        monkeypatch.setattr(agent.config, "max_iterations", 0)
        with pytest.raises(RuntimeError):
            await agent.resume_atomic(
                message,
                committer,
                rollback,
                finish,
                session_id=cm.active_session_id,
                branch_id=cm.get_active_session_branch().branch_id,
                last_node_id=cm.active_head.id,
            )
    assert not cm.compact_requested


async def test_compact_only_turn_failure_then_next_turn_continues(rollback_runtime):
    """首节点即 compact 的轮次失败后，用户直接发下一条：新轮从 compact 继续，无诊断/无恢复分支。"""
    agent, backend, mm, port = rollback_runtime
    cm = agent._context_manager
    branch_count = len(cm.list_branches(cm.active_session_id))
    turn_id = await cm.open_turn()
    compact = cm.commit_compact_node([])
    await cm.persist()
    await cm.close_turn(status="failed", closing_kind="error")
    failed = await port.turns.get(agent_id="agent", turn_id=turn_id)
    assert failed.status == "failed"
    assert failed.start_node_id == failed.end_node_id == compact.id
    assert failed.incomplete_node_id is None
    assert len(cm.list_branches(cm.active_session_id)) == branch_count

    request = await send(mm, "next-after-compact")
    await mm.dispatch_pending()
    (delivery,) = await mm.get_deliveries(request.message_id)
    assert delivery.state.value == "completed"
    later = await backend.load_turn("agent", delivery.turn_id)
    assert later["base_node_id"] == compact.id
    assert later["start_node_id"] is not None
