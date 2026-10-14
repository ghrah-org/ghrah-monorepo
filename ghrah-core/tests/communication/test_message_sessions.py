# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受理时固定 Session、迟到回复和关闭会话隔离测试。"""

from __future__ import annotations

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.communication import DeliveryState, MessageKind, MessageManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.types.config_types import ContextConfig


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


async def test_closed_session_blocks_only_its_own_queue(tmp_path):
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
            b2 = await b.create_session(name="open task")
            await b.activate_session(b2.session_id)
            await b.archive_session(b1)
            await b._context_manager.persist()
            second = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="open task",
                kind=MessageKind.NOTIFICATION,
                idempotency_key="second",
            )
            await mm.bind_atomic(b)
            await mm.dispatch_pending()
            (blocked,) = await mm.get_deliveries(first.message_id)
            assert blocked.state == DeliveryState.BLOCKED
            assert blocked.blocked_reason == "target_session_unavailable"
            assert blocked.target_session_id == b1
            assert blocked.attempts == 0
            assert (await mm.get_deliveries(second.message_id))[0].state == DeliveryState.COMPLETED
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
