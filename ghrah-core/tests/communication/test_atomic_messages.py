# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""同一 SQLite 事务的消费确认、上下文节点和出站消息故障验证。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.agents.builder import AgentBuilder
from ghrah.chat.format import LLMResponse
from ghrah.chat.message import ChatMessage
from ghrah.communication.message_manager import DeliveryState, MessageKind, MessageManager
from ghrah.context.iteration_commit import durable_iteration_scope
from ghrah.context.manager import ContextManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.types.config_types import ContextConfig


class OutboundLLM(ScriptedLLM):
    """在本轮执行阶段准备出站，验证准备与持久受理的差别。"""

    def __init__(self) -> None:
        super().__init__(["done", "retry done"])
        self.manager: MessageManager | None = None
        self.draft = None
        self.prepared = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        assert self.manager is not None
        self.draft = await self.manager.prepare_send(
            recipient_ids=("c",),
            content="outbound",
            kind=MessageKind.NOTIFICATION,
            idempotency_key="from-b",
        )
        self.prepared.set()
        await self.release.wait()
        return await super().generate(messages, tools)


async def make_atomic_agent(backend: SqliteBackend, llm: ScriptedLLM):
    """先初始化 Core schema 和根节点，再打开同文件消息服务。"""
    agent = AgentBuilder.from_config(
        AgentConfig(name="display-b", agent_id="b"),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
        persistence_factory=lambda config: backend,
    )
    await agent._context_manager.persist()
    return agent


def manager_for(path: Path) -> MessageManager:
    """声明同项目的三个稳定 agent 身份。"""
    return MessageManager(path, project_id="project", agent_ids=("a", "b", "c"))


async def submit(manager: MessageManager):
    """发送单个请求，目标离线时保持 pending。"""
    return await manager.send(
        sender_id="a",
        recipient_ids=("b",),
        content="input",
        kind=MessageKind.REQUEST,
        idempotency_key="input",
    )


async def test_execution_rollback_discards_staged_reply_acknowledgements(tmp_path):
    from ghrah.communication.message_manager import _ATOMIC

    scopes = []

    class FailingLLM(ScriptedLLM):
        async def generate(self, messages, tools=None):
            scope = _ATOMIC.get()
            assert scope is not None
            scopes.append(scope)
            # 模拟等待回复后、工具结果节点提交前已准备的消费确认。
            scope.inline_updates["uncommitted-reply"] = True
            raise RuntimeError("tool result iteration failed")

    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        agent = await make_atomic_agent(backend, FailingLLM([]))
        async with manager_for(backend.db_path) as manager:
            request = await submit(manager)
            await manager.bind_atomic(agent)
            await manager.dispatch_pending()
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.FAILED
            assert scopes
            assert all(not scope.inline_updates and not scope.drafts for scope in scopes)


async def test_node_consumption_and_outbound_are_atomic_and_queryable_after_restart(tmp_path):
    path = tmp_path / "core.sqlite"
    llm = OutboundLLM()
    async with SqliteBackend(path) as backend:
        agent = await make_atomic_agent(backend, llm)
        original_head = agent._context_manager.active_head.id
        async with manager_for(path) as manager:
            llm.manager = manager
            request = await submit(manager)
            await manager.bind_atomic(agent)
            await asyncio.wait_for(llm.prepared.wait(), 1)
            assert llm.draft is not None
            with pytest.raises(KeyError):
                await manager.get_message(llm.draft.message_id)
            assert agent._context_manager.active_head.id == original_head
            llm.release.set()
            await manager.dispatch_pending()
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.COMPLETED
            assert delivery.consumed_node_id == agent._context_manager.active_head.id
            outbound = await manager.get_message(llm.draft.message_id)
            assert outbound.origin_node_id == delivery.consumed_node_id
            assert outbound.sender_id == "b"
            checkpoint = await backend.load_checkpoint("b")
            assert checkpoint is not None
            assert delivery.consumed_node_id in {node.id for node in checkpoint.nodes}
            node = agent._context_manager.active_head
            assert [m.text for m in node.messages_delta if m.source == "agent:a"] == ["input"]
    async with manager_for(path) as manager:
        (delivery,) = await manager.get_deliveries(request.message_id)
        assert delivery.state == DeliveryState.COMPLETED
        assert (
            await manager.get_message(outbound.message_id)
        ).origin_node_id == delivery.consumed_node_id
        (reply,) = await manager.get_replies(request.message_id)
        assert reply.content == "done"


@pytest.mark.parametrize("failure", ["node", "consumption", "outbound"])
async def test_any_write_failure_rolls_back_all_three_and_memory_then_retry(tmp_path, failure):
    path = tmp_path / "core.sqlite"
    llm = OutboundLLM()
    triggers = {
        "node": "BEFORE INSERT ON nodes WHEN NEW.parent_id IS NOT NULL",
        "consumption": "BEFORE UPDATE ON mm_deliveries WHEN NEW.consumed_node_id IS NOT NULL",
        "outbound": "BEFORE INSERT ON mm_messages WHEN NEW.sender_id='b'",
    }
    async with SqliteBackend(path) as backend:
        agent = await make_atomic_agent(backend, llm)
        old_head = agent._context_manager.active_head.id
        async with manager_for(path) as manager:
            llm.manager = manager
            request = await submit(manager)
            await manager.bind_atomic(agent)
            # 回调扫描可能立即运行；注册触发器前阻止首轮 LLM 继续。
            # 此用例以提交事务中的触发器作为持久失败点。
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    f"CREATE TRIGGER injected {triggers[failure]} "
                    "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
                )
                await db.commit()
            llm.release.set()
            await manager.dispatch_pending()
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.FAILED
            assert delivery.consumed_node_id is None
            assert agent._context_manager.active_head.id == old_head
            assert llm.draft is not None
            with pytest.raises(KeyError):
                await manager.get_message(llm.draft.message_id)
            assert await manager.get_replies(request.message_id) == ()
            checkpoint = await backend.load_checkpoint("b")
            assert checkpoint is not None
            assert all(
                not any(
                    m.metadata.get("message_id") == request.message_id for m in n.messages_delta
                )
                for n in checkpoint.nodes
            )
            async with aiosqlite.connect(path) as db:
                await db.execute("DROP TRIGGER injected")
                await db.commit()
            await manager.retry(delivery.delivery_id)
            await manager.dispatch_pending()
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.COMPLETED
            assert [
                m.text
                for m in agent._context_manager.active_head.messages_delta
                if m.source == "agent:a"
            ] == ["input"]


async def test_cancel_during_commit_waits_for_result_and_finalizes_memory(tmp_path):
    """取消不会留下“数据库已提交、内存却回滚”的假象。"""
    entered, release = asyncio.Event(), asyncio.Event()
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager(agent_name="b", persistence=backend)
        await cm.persist()
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user("input")])
        old_head = cm.active_head.id

        async def commit(changes):
            entered.set()
            await release.wait()
            await backend.apply_changes(changes)

        with durable_iteration_scope(cm, commit):
            task = asyncio.create_task(cm.commit_iteration_async(ability_names=["conversation"]))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert cm.active_head.id == old_head
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cm.active_head.id != old_head
        assert not cm.in_iteration
        restored = ContextManager(agent_name="b", persistence=backend)
        await restored.restore("b")
        assert restored.active_head.id == cm.active_head.id


async def test_foreign_database_and_nontransaction_writes_are_rejected(tmp_path):
    async with SqliteBackend(tmp_path / "core.sqlite") as backend:
        cm = ContextManager(agent_name="b", persistence=backend)
        await cm.persist()
        from ghrah.context.persistence.changes import ContextChanges

        async with aiosqlite.connect(tmp_path / "other.sqlite") as db:
            with pytest.raises(RuntimeError, match="transaction"):
                await backend.apply_changes_in_transaction(db, ContextChanges(agent_name="b"))
            await db.execute("BEGIN IMMEDIATE")
            with pytest.raises(ValueError, match="does not match"):
                await backend.apply_changes_in_transaction(db, ContextChanges(agent_name="b"))
            await db.rollback()


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
async def test_process_crash_before_and_after_atomic_commit(tmp_path, point):
    """真实进程退出：提交前可以重试，提交后禁止重复注入，出站仍可恢复。"""
    path = tmp_path / "core.sqlite"
    script = """
import asyncio, os, sys
from ghrah.agents.builder import AgentBuilder
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.communication.message_manager import MessageManager, MessageKind
from ghrah.core.config import AgentConfig
from ghrah.types.config_types import ContextConfig
from ghrah.core.events import ActionChainUpdatedEvent
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
class LLM:
    model = 'crash-model'
    def configure_tools(self, tools): pass
    def apply_model_overrides(self, overrides): pass
    async def generate(self, messages, tools=None):
        await self.mm.prepare_send(recipient_ids=('c',), content='outbound',
            kind=MessageKind.NOTIFICATION, idempotency_key='crash-out')
        if sys.argv[2] == 'before_commit': os._exit(23)
        return LLMResponse(content_blocks=[TextBlock(text='done')])
class Publisher:
    async def publish(self, event):
        if isinstance(event, ActionChainUpdatedEvent): os._exit(23)
async def main():
    async with SqliteBackend(sys.argv[1]) as backend:
        llm = LLM()
        agent = AgentBuilder.from_config(
            AgentConfig(name='b', agent_id='b', context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()], llm_factory=lambda config: llm,
            persistence_factory=lambda config: backend, event_publisher=Publisher())
        await agent._context_manager.persist()
        async with MessageManager(sys.argv[1], project_id='project', agent_ids=('a','b','c')) as mm:
            llm.mm = mm
            await mm.send(sender_id='a', recipient_ids=('b',), content='input',
                kind=MessageKind.REQUEST, idempotency_key='input')
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
    async with SqliteBackend(path) as backend:
        llm = ScriptedLLM(["recovered"])
        agent = AgentBuilder.from_config(
            AgentConfig(name="b", agent_id="b", context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()],
            llm_factory=lambda config: llm,
            persistence_factory=lambda config: backend,
        )
        await agent._context_manager.restore("b")
        async with manager_for(path) as manager:
            request = await submit(manager)
            (delivery,) = await manager.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.INTERRUPTED
            await manager.bind_atomic(agent)
            await manager.dispatch_pending()
            assert llm.calls == []
            async with aiosqlite.connect(path) as db:
                row = await (
                    await db.execute(
                        "SELECT origin_node_id FROM mm_messages WHERE idempotency_key='crash-out'"
                    )
                ).fetchone()
            if point == "before_commit":
                assert delivery.consumed_node_id is None
                assert row is None
                await manager.retry(delivery.delivery_id)
                await manager.dispatch_pending()
                assert len(llm.calls) == 1
                assert len(await manager.get_replies(request.message_id)) == 1
            else:
                assert delivery.consumed_node_id is not None
                assert row is not None and row[0] == delivery.consumed_node_id
                assert agent._context_manager.active_head.id == delivery.consumed_node_id
                with pytest.raises(ValueError, match="not retryable"):
                    await manager.retry(delivery.delivery_id)
