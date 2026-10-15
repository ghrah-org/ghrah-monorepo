# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""持久消息内压缩不消费输入、故障回滚、降级和紧急重试验证。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.agents.builder import AgentBuilder
from ghrah.chat.message import ChatMessage
from ghrah.communication import DeliveryState, MessageKind, MessageManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.core.events import ActionChainUpdatedEvent
from ghrah.types.config_types import ContextConfig, WindowConfig


class CompactLLM(ScriptedLLM):
    """可在摘要或首次正常推理阶段注入失败。"""

    def __init__(self, *, summary_fails=False, limit_once=False):
        super().__init__(["summary", "done"])
        self.summary_fails = summary_fails
        self.limit_once = limit_once
        self.normal_calls = 0

    async def generate(self, messages, tools=None):
        from ghrah.context.compact import COMPACT_SUMMARY_PROMPT

        summary = messages[0].text == COMPACT_SUMMARY_PROMPT
        if summary and self.summary_fails:
            self._replies.pop(0)
            raise RuntimeError("summary unavailable")
        if not summary:
            self.normal_calls += 1
            if self.limit_once:
                self.limit_once = False
                raise RuntimeError("context_length_exceeded")
        return await super().generate(messages, tools)


async def seeded_agent(backend, llm):
    agent = AgentBuilder.from_config(
        AgentConfig(
            name="b",
            agent_id="b",
            context=ContextConfig(auto_persist=False),
            window=WindowConfig(max_tokens=2000, compact_threshold=0.5, compact_keep_recent=2),
        ),
        abilities=[ConversationAbility()],
        llm_factory=lambda config: llm,
        persistence_factory=lambda config: backend,
    )
    cm = agent._context_manager
    for index in range(3):
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(f"history {index}")])
        cm.commit_iteration(ability_names=["seed"])
    await cm.persist()
    return agent


@pytest.mark.parametrize("trigger", ["manual", "threshold", "emergency", "degraded"])
async def test_atomic_compaction_preserves_pending_input_and_then_completes(tmp_path, trigger):
    path = tmp_path / "core.sqlite"
    llm = CompactLLM(summary_fails=trigger == "degraded", limit_once=trigger == "emergency")
    async with SqliteBackend(path) as backend:
        agent = await seeded_agent(backend, llm)
        cm = agent._context_manager
        compact_events = []

        class Publisher:
            async def publish(self, event):
                if (
                    isinstance(event, ActionChainUpdatedEvent)
                    and event.node["metadata"].get("node_kind") == "compact"
                ):
                    (delivery,) = await mm.get_deliveries(request.message_id)
                    assert delivery.consumed_node_id is None
                    assert delivery.state == DeliveryState.PROCESSING
                    assert agent._message_queue.qsize() == 1
                    compact_events.append(event.node)

        agent._event_publisher = Publisher()
        if trigger == "threshold":
            cm.record_window_occupied(1500)
        elif trigger != "emergency":
            cm.request_compact()
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="input",
                kind=MessageKind.REQUEST,
                idempotency_key="input",
            )
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.COMPLETED
            assert len(compact_events) == 1
            compact = compact_events[0]
            assert compact["messages_delta"] == []
            assert compact["metadata"]["degraded"] == (trigger == "degraded")
            assert compact["metadata"]["trigger_source"] == (
                "manual" if trigger == "degraded" else trigger
            )
            assert delivery.consumed_node_id != compact["id"]
            node = cm.active_head
            assert [m.text for m in node.messages_delta if m.source == "agent:a"] == ["input"]
            assert agent._iteration_state.iteration == 0
            assert agent._message_queue.empty()
            checkpoint = await backend.load_checkpoint("b")
            assert checkpoint is not None
            assert compact["id"] in {node.id for node in checkpoint.nodes}
            restored = type(cm)(agent_name="b", persistence=backend)
            await restored.restore("b")
            assert restored.active_head.id == cm.active_head.id
            assert len(await mm.get_replies(request.message_id)) == 1


async def test_compact_write_failure_keeps_old_head_and_retry_has_one_input(tmp_path):
    path = tmp_path / "core.sqlite"
    llm = CompactLLM()
    llm._replies = ["first summary", "retry summary", "done"]
    async with SqliteBackend(path) as backend:
        agent = await seeded_agent(backend, llm)
        cm = agent._context_manager
        old_head = cm.active_head.id
        cm.request_compact()
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "CREATE TRIGGER reject_compact BEFORE INSERT ON nodes "
                    "WHEN json_extract(NEW.metadata, '$.node_kind')='compact' "
                    "BEGIN SELECT RAISE(ABORT, 'compact write failed'); END"
                )
                await db.commit()
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="input",
                kind=MessageKind.REQUEST,
                idempotency_key="input",
            )
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.FAILED
            assert delivery.consumed_node_id is None
            assert cm.active_head.id == old_head
            assert cm.compact_requested
            assert agent._message_queue.empty()
            async with aiosqlite.connect(path) as db:
                assert (
                    await (
                        await db.execute(
                            "SELECT count(*) FROM nodes "
                            "WHERE json_extract(metadata, '$.node_kind')='compact'"
                        )
                    ).fetchone()
                )[0] == 0
                await db.execute("DROP TRIGGER reject_compact")
                await db.commit()
            await mm.retry(delivery.delivery_id)
            await mm.dispatch_pending()
            assert (await mm.get_deliveries(request.message_id))[0].state == DeliveryState.COMPLETED
            assert [m.text for m in cm.active_head.messages_delta if m.source == "agent:a"] == [
                "input"
            ]


async def test_process_exit_after_compact_keeps_input_unconsumed(tmp_path):
    """压缩提交后强杀，恢复快照且原消息仍可显式重试。"""
    path = tmp_path / "core.sqlite"
    script = """
import asyncio, os, sys, runpy
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2]).parent.parent))
ns = runpy.run_path(sys.argv[2])
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.communication import MessageManager, MessageKind
from ghrah.core.events import ActionChainUpdatedEvent
class Publisher:
    async def publish(self, event):
        if (isinstance(event, ActionChainUpdatedEvent)
            and event.node['metadata'].get('node_kind') == 'compact'):
            os._exit(23)
async def main():
    async with SqliteBackend(sys.argv[1]) as backend:
        agent = await ns['seeded_agent'](backend, ns['CompactLLM']())
        agent._event_publisher = Publisher()
        agent._context_manager.request_compact()
        async with MessageManager(sys.argv[1], project_id='p', agent_ids=('a','b')) as mm:
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
        [sys.executable, "-c", script, str(path), str(Path(__file__).resolve())],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 23, result.stderr.decode()
    llm = ScriptedLLM(["done"])
    async with SqliteBackend(path) as backend:
        agent = AgentBuilder.from_config(
            AgentConfig(name="b", context=ContextConfig(auto_persist=False)),
            abilities=[ConversationAbility()],
            llm_factory=lambda config: llm,
            persistence_factory=lambda config: backend,
        )
        cm = agent._context_manager
        await cm.restore("b")
        assert cm.active_head.metadata["node_kind"] == "compact"
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            request = await mm.send(
                sender_id="a",
                recipient_ids=("b",),
                content="input",
                kind=MessageKind.REQUEST,
                idempotency_key="input",
            )
            (delivery,) = await mm.get_deliveries(request.message_id)
            assert delivery.state == DeliveryState.INTERRUPTED
            assert delivery.consumed_node_id is None
            await mm.bind_atomic(agent)
            await mm.retry(delivery.delivery_id)
            await mm.dispatch_pending()
            assert len(llm.calls) == 1
            assert any("summary" in message.text for message in llm.calls[0])
            assert (await mm.get_deliveries(request.message_id))[0].state == DeliveryState.COMPLETED
