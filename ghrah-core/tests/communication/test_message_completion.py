# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""最终结果收尾故障、重启恢复与幂等测试。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.communication import DeliveryState, MessageKind, MessageManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.core.events import AgentResponseEvent
from ghrah.types.config_types import ContextConfig


@pytest.mark.parametrize(
    "point", ["before_intent", "after_intent", "during_finish", "after_finish"]
)
async def test_process_exit_around_completion_recovers_without_reexecuting(tmp_path, point):
    """结果落库后任意退出都能补齐一次回复；落库前不猜测执行已完成。"""
    path = tmp_path / "core.sqlite"
    script = """
import asyncio, os, sys
from ghrah.agents.builder import AgentBuilder
from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.communication import MessageManager, MessageKind
from ghrah.core.config import AgentConfig
from ghrah.types.config_types import ContextConfig
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
class LLM:
    model = 'completion-test'
    def configure_tools(self, tools): pass
    def apply_model_overrides(self, overrides): pass
    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text='durable final reply')])
class CrashManager(MessageManager):
    async def _complete_delivery(self, *args, **kwargs):
        if sys.argv[2] == 'before_intent': os._exit(23)
        await super()._complete_delivery(*args, **kwargs)
    async def _finish_result(self, delivery_id):
        if sys.argv[2] == 'after_intent': os._exit(23)
        await super()._finish_result(delivery_id)
        if sys.argv[2] == 'after_finish': os._exit(23)
    async def _insert_message(self, *args, **kwargs):
        message_id = await super()._insert_message(*args, **kwargs)
        if args[4] == MessageKind.RESPONSE and sys.argv[2] == 'during_finish':
            os._exit(23)
        return message_id
async def main():
    async with SqliteBackend(sys.argv[1]) as backend:
        agent = AgentBuilder.from_config(
            AgentConfig(name='b', agent_id='b', context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()], llm_factory=lambda config: LLM(),
            persistence_factory=lambda config: backend)
        await agent._context_manager.persist()
        async with CrashManager(sys.argv[1], project_id='p', agent_ids=('a','b')) as mm:
            await mm.send(sender_id='a', recipient_ids=('b',), content='request',
                kind=MessageKind.REQUEST, idempotency_key='request')
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
asyncio.run(main())
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(path), point],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 23, result.stderr.decode()
    # 重开两次，证明恢复不依赖在线 agent，也不会生成第二条回复。
    for _ in range(2):
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="request",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.consumed_node_id is not None
            replies = await mm.get_replies(request.message_id)
            if point == "before_intent":
                assert delivery.state == DeliveryState.INTERRUPTED
                assert not delivery.result_ready
                assert replies == ()
            else:
                assert delivery.state == DeliveryState.COMPLETED
                assert delivery.result_ready
                assert delivery.result_node_id == delivery.consumed_node_id
                (reply,) = replies
                assert reply.content == "durable final reply"
                assert reply.origin_node_id == delivery.result_node_id
                assert (await mm.get_deliveries(reply.message_id))[0].state == DeliveryState.PENDING
            assert await mm.recover_completions() == 0
            with pytest.raises(ValueError, match="not retryable"):
                await mm.retry(delivery.delivery_id)


async def test_atomic_event_failure_does_not_lose_completed_reply(tmp_path):
    """观察事件失败不回滚已完成请求，也不丢持久回复。"""

    class FailingPublisher:
        async def publish(self, event):
            if isinstance(event, AgentResponseEvent):
                raise RuntimeError("observer event failed")

    path = tmp_path / "core.sqlite"
    llm = ScriptedLLM(["done"])
    async with SqliteBackend(path) as backend:
        agent = AgentBuilder.from_config(
            AgentConfig(name="b", agent_id="b", context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()],
            llm_factory=lambda config: llm,
            persistence_factory=lambda config: backend,
            event_publisher=FailingPublisher(),
        )
        await agent._context_manager.persist()
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="request",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.COMPLETED
            assert delivery.result_ready
            (reply,) = await mm.get_replies(request.message_id)
            assert reply.content == "done"
            assert len(llm.calls) == 1


async def test_completion_failure_recovers_after_restart_without_model_calls(tmp_path):
    path = tmp_path / "messages.sqlite"
    llm = ScriptedLLM(["done"])
    agent = AgentBuilder.from_config(
        AgentConfig(name="b"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
    )
    async with MessageManager(path, project_id="p", agent_ids=("a", "b"), poll_interval=10) as mm:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TRIGGER reject_reply BEFORE INSERT ON mm_messages "
                "WHEN NEW.kind='response' BEGIN SELECT RAISE(ABORT, 'reply write failed'); END"
            )
            await db.commit()
        request = await mm.send(
            sender_id="a",
            recipient_ids=("b",),
            content="request",
            kind=MessageKind.REQUEST,
            idempotency_key="request",
        )
        mm.bind("b", agent.receive)
        await mm.dispatch_pending()
        (delivery,) = await mm.get_deliveries(request.message_id)
        assert delivery.state == DeliveryState.FAILED
        assert delivery.result_ready
        assert await mm.get_replies(request.message_id) == ()
        with pytest.raises(ValueError, match="not retryable"):
            await mm.retry(delivery.delivery_id)
    async with aiosqlite.connect(path) as db:
        await db.execute("DROP TRIGGER reject_reply")
        await db.commit()
    async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
        (delivery,) = await mm.get_deliveries(request.message_id)
        assert delivery.state == DeliveryState.COMPLETED
        (reply,) = await mm.get_replies(request.message_id)
        assert reply.content == "done"
        assert len(llm.calls) == 1


async def test_atomic_receive_without_consumption_cannot_complete(tmp_path):
    """未执行任何迭代时，不允许用空结果伪造请求已完成。"""
    path = tmp_path / "core.sqlite"
    llm = ScriptedLLM([])
    async with SqliteBackend(path) as backend:
        agent = AgentBuilder.from_config(
            AgentConfig(name="b", max_iterations=0, context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()],
            llm_factory=lambda config: llm,
            persistence_factory=lambda config: backend,
        )
        await agent._context_manager.persist()
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="request",
                kind=MessageKind.REQUEST,
                idempotency_key="request",
            )
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.FAILED
            assert delivery.consumed_node_id is None
            assert not delivery.result_ready
            assert await mm.get_replies(request.message_id) == ()
            assert llm.calls == []
