# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受理时固定 Session、迟到回复和关闭会话隔离测试。"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.ability.builtin.cluster.operations.send_message import SendMessageAbility
from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.communication import DeliveryState, MessageKind, MessageManager
from ghrah.communication.supervisor import SupervisorActor
from ghrah.context.manager import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.context.turn import TurnRevokedError
from ghrah.core.config import AgentConfig
from ghrah.types.config_types import ContextConfig
from ghrah.types.results import ActionOutcome, ActionResult


async def agent_for(backend, agent_id, replies, *, restore=False):
    llm = ScriptedLLM(replies)
    agent = AgentBuilder.from_config(
        AgentConfig(name=agent_id, agent_id=agent_id, context=ContextConfig(auto_persist=False)),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
        persistence_factory=lambda config: backend,
    )
    if restore:
        await agent._context_manager.restore(agent_id)
    else:
        await agent._context_manager.persist()
    return agent, llm


def manager_for(path):
    return MessageManager(path, project_id="p", agent_ids=("a", "b"), session_routing=True)


@pytest.mark.parametrize("abort_mode", ["local", "other_context", "turn_only"])
async def test_abort_wakes_inline_wait_and_releases_worker(tmp_path, monkeypatch, abort_mode):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        sender, _ = await agent_for(backend, "a", ["next reply"])
        await agent_for(backend, "b", [])  # 固定目标会话，接收方保持离线。
        mm = manager_for(path)
        await mm.open(start_dispatcher=False)
        dispatch = None
        try:
            sender._supervisor = SupervisorActor(message_manager=mm)
            action = sender._action
            polled = asyncio.Event()
            draft = None
            get_deliveries = mm.get_deliveries

            async def observe_poll(message_id):
                deliveries = await get_deliveries(message_id)
                if draft is not None and message_id == draft.message_id:
                    polled.set()
                return deliveries

            async def checkpoint_then_wait(data):
                nonlocal draft
                if draft is not None:
                    with pytest.raises(TurnRevokedError) as revoked:
                        await mm.wait_inline(draft.message_id, timeout=-1)
                    raise revoked.value
                draft = await mm.prepare_send(
                    recipient_ids=("b",),
                    content="inline request",
                    kind=MessageKind.REQUEST,
                    idempotency_key="inline",
                    metadata={"inline_reply": True},
                )
                return {"results": []}

            async def wait_state():
                async with (
                    backend.transaction() as db,
                    db.execute(
                        "SELECT state FROM mm_inline_waits WHERE message_id=?", (draft.message_id,)
                    ) as cursor,
                ):
                    row = await cursor.fetchone()
                assert row is not None
                return row[0]

            monkeypatch.setattr(mm, "get_deliveries", observe_poll)
            monkeypatch.setattr(sender, "_action", checkpoint_then_wait)
            monkeypatch.setattr(sender.config, "max_iterations", 2)
            await mm.bind_atomic(sender)
            opener = await mm.send_external(
                sender="user", recipient_ids=("a",), content="opener", idempotency_key="opener"
            )
            await mm._scan()
            await asyncio.wait_for(polled.wait(), 3)
            worker = mm._workers["a"]
            (parent,) = await mm.get_deliveries(opener.message_id)
            assert parent.turn_id is not None
            assert await wait_state() == "waiting"
            assert draft.message_id in mm._reply_changes
            later = await mm.send_external(
                sender="user", recipient_ids=("a",), content="later", idempotency_key="later"
            )
            monkeypatch.setattr(sender, "_action", action)
            dispatch = asyncio.create_task(mm.dispatch_pending())

            if abort_mode == "local":
                await sender.abort_turn(turn_id=parent.turn_id)
            else:
                async with SqliteBackend(path) as other_backend:
                    other = ContextManager("a", persistence=other_backend)
                    await other.abort_turn(
                        turn_id=parent.turn_id,
                        settlement=None if abort_mode == "turn_only" else mm.delivery_settlement,
                    )
            if abort_mode != "turn_only":
                assert (await mm.get_deliveries(opener.message_id))[0].state == (
                    DeliveryState.INTERRUPTED
                )
                assert await wait_state() == "released"

            await asyncio.wait_for(asyncio.shield(worker), 1)
            assert mm._workers.get("a") is not worker
            assert not mm._reply_waiters
            assert not mm._reply_changes
            assert not mm._wait_edges
            await asyncio.wait_for(asyncio.shield(dispatch), 3)
            (stopped,) = await mm.get_deliveries(opener.message_id)
            assert stopped.state == DeliveryState.INTERRUPTED
            assert await wait_state() == "released"
            assert not await mm.get_replies(opener.message_id)
            assert (await mm.get_replies(later.message_id))[0].content == "next reply"
        finally:
            await mm.close()
            if dispatch is not None:
                await asyncio.gather(dispatch, return_exceptions=True)


@pytest.mark.parametrize("after_stop", ["mailbox", "scan", "reopen", "resume", "claimed"])
async def test_stopped_inline_wait_releases_queue_or_reserves_on_resume(
    tmp_path, monkeypatch, after_stop
):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        sender, llm = await agent_for(backend, "a", ["received", "next"])
        peer, _ = await agent_for(backend, "b", ["peer reply"])
        mm = manager_for(path)
        await mm.open(start_dispatcher=False)
        try:
            sender._supervisor = SupervisorActor(message_manager=mm)
            monkeypatch.setitem(sender._abilities, "send_message", SendMessageAbility())
            action = sender._action
            entered = asyncio.Event()
            draft = None

            async def checkpoint_then_wait(data):
                nonlocal draft
                if draft is not None:
                    entered.set()
                    await asyncio.Event().wait()
                draft = await mm.prepare_send(
                    recipient_ids=("b",),
                    content="inline request",
                    kind=MessageKind.REQUEST,
                    idempotency_key="inline",
                    metadata={"inline_reply": True},
                )
                return {
                    "results": [
                        {
                            "ability_name": "send_message",
                            "action_result": ActionResult(
                                outcome=ActionOutcome.SUCCESS,
                                data={
                                    "await_reply": {
                                        "message_id": draft.message_id,
                                        "timeout": 1,
                                        "mode": "sync",
                                    }
                                },
                            ),
                        }
                    ],
                    "llm_metadata": {
                        "pending_tool_replies": True,
                        "pending_tool_call_ids": ["inline-call"],
                    },
                }

            async def wait_state():
                async with (
                    backend.transaction() as db,
                    db.execute(
                        "SELECT state FROM mm_inline_waits WHERE message_id=?", (draft.message_id,)
                    ) as cursor,
                ):
                    row = await cursor.fetchone()
                assert row is not None
                return row[0]

            monkeypatch.setattr(sender, "_action", checkpoint_then_wait)
            monkeypatch.setattr(sender.config, "max_iterations", 2)
            await mm.bind_atomic(sender)
            request = await mm.send_external(
                sender="user", recipient_ids=("a",), content="opener", idempotency_key="opener"
            )
            await mm._scan()
            await asyncio.wait_for(entered.wait(), 3)
            assert await wait_state() == "waiting"
            await mm.unbind("a")
            (parent,) = await mm.get_deliveries(request.message_id)
            assert parent.state == DeliveryState.INTERRUPTED
            assert await wait_state() == "released"
            monkeypatch.setattr(sender, "_action", action)

            if after_stop in ("scan", "reopen"):
                # 注入旧版本留下的无主等待，扫描和启动都应收敛。
                async with backend.transaction() as db:
                    await db.execute("UPDATE mm_inline_waits SET state='waiting'")
                if after_stop == "reopen":
                    await mm.close()
                    await mm.open(start_dispatcher=False)
                else:
                    await mm.dispatch_pending()
                assert await wait_state() == "released"

            await mm.bind_atomic(peer)
            await mm.dispatch_pending()
            (reply,) = await mm.get_replies(draft.message_id)
            (reply_delivery,) = await mm.get_deliveries(reply.message_id)
            assert reply_delivery.state == DeliveryState.PENDING
            resume_options = {
                "expected_last_node_id": parent.last_node_id,
                "acknowledge_side_effect_risk": True,
            }
            if after_stop == "claimed":
                # 回复已由普通收件箱执行，但尚未提交任何节点移动原检查点。
                async with backend.transaction() as db:
                    await db.execute(
                        "UPDATE mm_deliveries SET state='failed' WHERE delivery_id=?",
                        (reply_delivery.delivery_id,),
                    )
                assert (await mm.get_progress(parent.delivery_id)).resume_block_reason == (
                    "inline_reply_unavailable"
                )
                with pytest.raises(ValueError, match="inline_reply_unavailable"):
                    await mm.resume_interrupted(parent.delivery_id, **resume_options)
                assert await wait_state() == "released"
                return
            if after_stop == "resume":
                await mm.resume_interrupted(parent.delivery_id, **resume_options)
                await mm._scan()
                assert await wait_state() == "waiting"

            await mm.bind_atomic(sender)
            later = await mm.send_external(
                sender="user", recipient_ids=("a",), content="later", idempotency_key="later"
            )
            await asyncio.wait_for(mm.dispatch_pending(), 3)
            (reply_delivery,) = await mm.get_deliveries(reply.message_id)
            assert reply_delivery.state == DeliveryState.COMPLETED
            assert (await mm.get_replies(later.message_id))[0].content == "next"
            if after_stop == "resume":
                assert reply_delivery.attempts == 0
                assert reply_delivery.consumed_node_id is not None
                assert await wait_state() == "consumed"
                (parent,) = await mm.get_deliveries(request.message_id)
                assert parent.state == DeliveryState.COMPLETED
                node = await backend.load_node("a", reply_delivery.consumed_node_id)
                assert node.action_results[0]["action_result"].data["response"] == "peer reply"
            else:
                assert reply_delivery.attempts == 1
                assert await wait_state() == "released"
                assert sum(message.text == "peer reply" for message in llm.calls[0]) == 1
        finally:
            await mm.close()


async def test_acceptance_freezes_sessions_and_late_reply_preserves_active_selection(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        a, a_llm = await agent_for(backend, "a", ["received"])
        b, b_llm = await agent_for(backend, "b", ["done"])
        a1 = a._context_manager.active_session_id
        b1 = b._context_manager.active_session_id
        async with manager_for(path) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="old task",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            assert request.sender_session_id == a1
            assert (await mm.get_deliveries(request.message_id))[0].target_session_id == b1
            a2 = await a.create_session(name="new task")
            b2 = await b.create_session(name="new task")
            await a.activate_session(a2.session_id)
            await b.activate_session(b2.session_id)
            await a._context_manager.persist()
            await b._context_manager.persist()
            # 默认会话已经变了，重复受理仍返回原消息，不重新解析默认值。
            assert (
                await mm.send(
                    sender_id="a",
                    recipient_ids=("b",),
                    content="old task",
                    kind=MessageKind.REQUEST,
                    idempotency_key="request",
                )
                == request
            )
            await mm.bind_atomic(b)
            await mm.bind_atomic(a)
            await mm.dispatch_pending()
            assert a._context_manager.active_session_id == a2.session_id
            assert b._context_manager.active_session_id == b2.session_id
            (reply,) = await mm.get_replies(request.message_id)
            assert reply.sender_session_id == b1
            assert (await mm.get_deliveries(reply.message_id))[0].target_session_id == a1
            assert a_llm.calls[0][-1].metadata["target_session_id"] == a1
            assert b_llm.calls[0][-1].metadata["target_session_id"] == b1
            assert len(a._context_manager.get_history(session_id=a2.session_id)) == 1
            assert len(b._context_manager.get_history(session_id=b2.session_id)) == 1
            async with aiosqlite.connect(path) as db:
                pointers = dict(
                    await (
                        await db.execute("SELECT agent_name, active_session_id FROM chain_meta")
                    ).fetchall()
                )
            assert pointers == {"a": a2.session_id, "b": b2.session_id}
            with pytest.raises(ValueError, match="conflicts"):
                await mm.send(
                    sender_id="a",
                    recipient_ids=("b",),
                    content="old task",
                    kind=MessageKind.REQUEST,
                    idempotency_key="request",
                    sender_session_id=a2.session_id,
                )


async def test_offline_delivery_keeps_session_after_reopen(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        await agent_for(backend, "a", [])
        b, _ = await agent_for(backend, "b", [])
        b1 = b._context_manager.active_session_id
        async with manager_for(path) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="offline",
                kind=MessageKind.NOTIFICATION,
                idempotency_key="offline",
            )
            b2 = await b.create_session(name="another")
            await b.activate_session(b2.session_id)
            await b._context_manager.persist()
    async with SqliteBackend(path) as backend:
        b, llm = await agent_for(backend, "b", ["done"], restore=True)
        async with manager_for(path) as mm:
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.COMPLETED
            assert delivery.target_session_id == delivery.consumed_session_id == b1
            assert b._context_manager.active_session_id == b2.session_id
            assert llm.calls[0][-1].text == "offline"


async def test_new_session_request_independent_of_old_session_queue(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        await agent_for(backend, "a", [])
        b, llm = await agent_for(backend, "b", ["new task done"])
        b1 = b._context_manager.active_session_id
        async with manager_for(path) as mm:
            first = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="closed task",
                kind=MessageKind.NOTIFICATION,
                idempotency_key="first",
            )
            queued = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="queued behind closed task",
                kind=MessageKind.REQUEST,
                idempotency_key="queued",
            )
            b2 = await b.create_session(name="open task")
            await b.activate_session(b2.session_id)
            await b.archive_session(b1)
            await b._context_manager.persist()
            second = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="open task",
                kind=MessageKind.REQUEST,
                idempotency_key="second",
            )
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            (blocked,) = await mm.get_deliveries(first.message_id)
            assert blocked.state == DeliveryState.BLOCKED
            assert blocked.blocked_reason == "target_session_unavailable"
            assert blocked.target_session_id == b1
            assert blocked.attempts == 0
            (old_pending,) = await mm.get_deliveries(queued.message_id)
            assert (old_pending.state, old_pending.attempts) == (DeliveryState.PENDING, 0)
            assert await mm.get_queue_blocker(old_pending.delivery_id) == blocked
            (fresh,) = await mm.get_deliveries(second.message_id)
            assert fresh.state == DeliveryState.COMPLETED
            assert fresh.target_session_id == b2.session_id
            (reply,) = await mm.get_replies(second.message_id)
            assert reply.content == "new task done"
            assert len(llm.calls) == 1
            assert llm.calls[0][-1].text == "open task"
            with pytest.raises(ValueError, match="not open"):
                await mm.resume_delivery(blocked.delivery_id)
            with pytest.raises(ValueError, match="not open"):
                await mm.send(
                    sender_id="a",
                    recipient_ids=("b",),
                    content="wrong owner",
                    kind=MessageKind.NOTIFICATION,
                    idempotency_key="wrong",
                    target_sessions={"b": second.sender_session_id},
                )


async def test_reply_to_closed_request_session_is_saved_and_blocked(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        a, llm = await agent_for(backend, "a", [])
        b, _ = await agent_for(backend, "b", ["done"])
        a1 = a._context_manager.active_session_id
        async with manager_for(path) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="old task",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            a2 = await a.create_session(name="new task")
            await a.activate_session(a2.session_id)
            await a.archive_session(a1)
            await a._context_manager.persist()
            await mm.bind_atomic(a)
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            assert (await mm.get_deliveries(request.message_id))[0].state == DeliveryState.COMPLETED
            (reply,) = await mm.get_replies(request.message_id)
            (delivery,) = await mm.get_deliveries(reply.message_id)
            assert delivery.target_session_id == a1
            assert delivery.state == DeliveryState.BLOCKED
            assert llm.calls == []
            assert a._context_manager.active_session_id == a2.session_id


async def test_prepared_outbound_records_actual_execution_session(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        a, _ = await agent_for(backend, "a", [])
        b, llm = await agent_for(backend, "b", ["done"])
        a1 = a._context_manager.active_session_id
        b1 = b._context_manager.active_session_id
        async with manager_for(path) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="input",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            b2 = await b.create_session(name="other")
            await b.activate_session(b2.session_id)
            await b._context_manager.persist()
            generate = llm.generate
            drafts = []

            async def prepare(messages, tools=None):
                drafts.append(
                    await mm.prepare_send(
                        recipient_ids=("a",),
                        content="outbound",
                        kind=MessageKind.NOTIFICATION,
                        idempotency_key="outbound",
                        target_sessions={"a": a1},
                    )
                )
                return await generate(messages, tools)

            llm.generate = prepare
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            outgoing = await mm.get_message(drafts[0].message_id)
            assert (
                outgoing.origin_node_id
                == (await mm.get_deliveries(request.message_id))[0].consumed_node_id
            )
            assert outgoing.sender_session_id == b1
            (delivery,) = await mm.get_deliveries(outgoing.message_id)
            assert delivery.target_session_id == a1
            assert b._context_manager.active_session_id == b2.session_id


async def test_legacy_unscoped_message_is_not_guessed_in_strict_mode(tmp_path):
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        await agent_for(backend, "a", [])
        b, llm = await agent_for(backend, "b", [])
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="legacy",
                kind=MessageKind.REQUEST,
                idempotency_key="legacy",
            )
        async with manager_for(path) as mm:
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.BLOCKED
            assert delivery.blocked_reason == "legacy_session_unassigned"
            assert delivery.target_session_id is None
            assert llm.calls == []
            assert await mm.get_replies(request.message_id) == ()


async def test_failed_input_releases_old_session_queue(tmp_path):
    """旧会话的前序失败后，原先排队的输入仍在该会话执行。"""
    path = tmp_path / "core.sqlite"
    async with SqliteBackend(path) as backend:
        b, llm = await agent_for(backend, "b", ["old-session reply"])
        old_session = b._context_manager.active_session_id
        async with manager_for(path) as mm:
            failed = await mm.send_external(
                sender="user",
                recipient_ids=("b",),
                content="old command",
                idempotency_key="old-command",
                metadata={
                    "execute_ability": {
                        "name": "send_message",
                        "args": {},
                        "call_id": "missing-tool",
                    }
                },
            )
            queued = await mm.send_external(
                sender="user",
                recipient_ids=("b",),
                content="old queued input",
                idempotency_key="old-queued",
            )
            session = await b.create_session(name="new test session")
            await b.activate_session(session.session_id)
            await b._context_manager.persist()
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            (failed_delivery,) = await mm.get_deliveries(failed.message_id)
            assert failed_delivery.state == DeliveryState.FAILED
            assert failed_delivery.target_session_id == old_session
            (completed,) = await mm.get_deliveries(queued.message_id)
            assert (completed.state, completed.attempts) == (DeliveryState.COMPLETED, 1)
            assert completed.target_session_id == completed.consumed_session_id == old_session
            assert await mm.get_queue_blocker(completed.delivery_id) is None
            (reply,) = await mm.wait_replies(queued.message_id, timeout=1)
            assert reply.content == "old-session reply"
            assert len(llm.calls) == 1
            assert b._context_manager.active_session_id == session.session_id
