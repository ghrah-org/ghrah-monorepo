# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""唯一执行入口的并发、取消、循环等待和真实 agent 回归。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from _helpers import ScriptedLLM

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.agents.runner import AgentRunner, detached_runner_context
from ghrah.chat.format import LLMResponse
from ghrah.communication.message_manager import MessageKind, MessageManager
from ghrah.core.config import AgentConfig
from ghrah.core.message import AgentMessage


class BlockingLLM(ScriptedLLM):
    """首轮暂停，验证其他入口不能并发进入同一实例。"""

    def __init__(self) -> None:
        super().__init__(["first reply", "second reply", "third reply"])
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum = 0

    async def generate(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            if not self.calls:
                self.entered.set()
                await self.release.wait()
            return await super().generate(messages, tools)
        finally:
            self.active -= 1


async def test_direct_and_persistent_receives_share_runner_and_keep_reply_ownership(tmp_path):
    """直接 receive、chat 与持久回调同时到达时共享串行入口。"""
    llm = BlockingLLM()
    agent = AgentBuilder.from_config(
        AgentConfig(name="worker", agent_id="b"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
    )
    first = AgentMessage(sender="user", recipient="b", content="first")
    task = asyncio.create_task(agent.receive(first))
    await asyncio.wait_for(llm.entered.wait(), 1)
    second_task = asyncio.create_task(agent.chat("second"))
    # 让 chat 进入 Runner 队列，首轮 LLM 仍暂停。
    await asyncio.sleep(0)
    async with MessageManager(
        tmp_path / "messages.sqlite", project_id="project", agent_ids=("a", "b")
    ) as manager:
        manager.bind("b", agent.receive)
        request = await manager.send(
            sender_id="a",
            recipient_ids=("b",),
            content="third",
            kind=MessageKind.REQUEST,
            idempotency_key="third",
        )
        with pytest.raises(RuntimeError, match="Cannot reset"):
            await agent.reset()
        llm.release.set()
        reply = await asyncio.wait_for(task, 1)
        assert reply.reply_to == first.id
        assert reply.content == "first reply"
        assert await asyncio.wait_for(second_task, 1) == "second reply"
        await manager.dispatch_pending()
        (response,) = await manager.get_replies(request.message_id)
        assert response.content == "third reply"
        assert llm.maximum == 1
        for index, content in enumerate(("first", "second", "third")):
            assert llm.calls[index][-1].text == content
        # 空闲后再次接收会启动 Runner；专用测试 LLM 扩充一条回应。
        llm._replies.append("idle reply")
        assert await agent.chat("idle") == "idle reply"


async def test_cancel_queued_operation_does_not_execute_it() -> None:
    runner = AgentRunner("worker")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def first() -> str:
        entered.set()
        await release.wait()
        return "first"

    async def queued() -> str:
        calls.append("queued")
        return "queued"

    first_task = asyncio.create_task(runner.run(first))
    await entered.wait()
    queued_task = asyncio.create_task(runner.run(queued))
    await asyncio.sleep(0)
    queued_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued_task
    release.set()
    assert await first_task == "first"
    assert calls == []
    assert await runner.run(queued) == "queued"


async def test_cancel_active_operation_cleans_up_before_next_operation() -> None:
    runner = AgentRunner("worker")
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def first() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def next_operation() -> str:
        assert cleaned.is_set()
        return "next"

    first_task = asyncio.create_task(runner.run(first))
    await entered.wait()
    next_task = asyncio.create_task(runner.run(next_operation))
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    assert await asyncio.wait_for(next_task, 1) == "next"


async def test_independent_roots_wait_cycle_is_rejected_without_hanging() -> None:
    a, b = AgentRunner("a"), AgentRunner("b")
    a_entered, b_entered = asyncio.Event(), asyncio.Event()

    async def result() -> str:
        return "ok"

    async def a_operation() -> str:
        a_entered.set()
        await b_entered.wait()
        return await b.run(result)

    async def b_operation() -> str:
        b_entered.set()
        await a_entered.wait()
        return await a.run(result)

    results = await asyncio.wait_for(
        asyncio.gather(a.run(a_operation), b.run(b_operation), return_exceptions=True), 1
    )
    assert any(
        isinstance(result, RuntimeError) and "wait cycle" in str(result) for result in results
    )
    assert "ok" in results
    assert await a.run(result) == "ok"
    assert await b.run(result) == "ok"


async def test_background_submission_can_queue_back_to_sender() -> None:
    a = AgentRunner("a")
    background = []

    async def reply() -> str:
        return "reply"

    async def send() -> str:
        background.append(asyncio.create_task(a.run(reply), context=detached_runner_context()))
        return "sent"

    assert await a.run(send) == "sent"
    assert await asyncio.wait_for(background[0], 1) == "reply"


async def test_close_cancels_active_and_queued_and_rejects_new_requests() -> None:
    runner = AgentRunner("worker")
    entered = asyncio.Event()

    async def operation() -> None:
        entered.set()
        await asyncio.Event().wait()

    first = asyncio.create_task(runner.run(operation))
    await entered.wait()
    second = asyncio.create_task(runner.run(operation))
    await asyncio.sleep(0)
    await runner.close()
    assert all(
        isinstance(result, asyncio.CancelledError)
        for result in await asyncio.gather(first, second, return_exceptions=True)
    )
    with pytest.raises(RuntimeError, match="closed"):
        await runner.run(operation)
