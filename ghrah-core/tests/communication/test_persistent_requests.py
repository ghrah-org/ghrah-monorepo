# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""原请求行为在持久链路上的覆盖：用户输入、同步工具、委托和广播。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.chat.content import TextBlock, ToolCallBlock
from ghrah.chat.format import LLMResponse
from ghrah.communication import DeliveryState, MessageManager, SupervisorActor
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.scheme.ability.cluster.operations.broadcast_message import BroadcastMessageAbility
from ghrah.scheme.ability.cluster.operations.send_message import SendMessageAbility
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility


class RequestLLM(ScriptedLLM):
    """首轮请求、后续收束，既用于同步回复也用于循环等待故障。"""

    def __init__(self, target="b", broadcast=False):
        super().__init__([])
        self.target = target
        self.broadcast = broadcast

    async def generate(self, messages, tools=None):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return LLMResponse(
                content_blocks=[
                    ToolCallBlock(
                        id="request-1",
                        name="broadcast_message" if self.broadcast else "send_message",
                        arguments={"content": "work"}
                        if self.broadcast
                        else {"target": self.target, "content": "work", "fire_and_forget": False},
                    )
                ]
            )
        return LLMResponse(content_blocks=[TextBlock(text="finished")])


async def bootstrap(path):
    async with SqliteBackend(path) as backend:
        await backend.load_checkpoint("bootstrap")


async def spawn(supervisor, name, llm, agent_id=None, timeout=1):
    await supervisor.spawn_agent(
        AgentConfig(name=name, agent_id=agent_id, communication_timeout=timeout),
        abilities=[SendMessageAbility(), BroadcastMessageAbility(), ConversationAbility()],
        llm_factory=lambda config: llm,
    )


@pytest.mark.parametrize("broadcast", [False, True])
async def test_user_chat_can_send_synchronous_requests_and_receive_original_reply(
    tmp_path, broadcast
):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    source_llm = RequestLLM(broadcast=broadcast)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
        try:
            await spawn(supervisor, "a", source_llm, agent_id="id-a")
            await spawn(supervisor, "b", ScriptedLLM(["b reply"]), agent_id="id-b")
            if broadcast:
                await spawn(supervisor, "c", ScriptedLLM(["c reply"]), agent_id="id-c")
            actor = await supervisor.get_agent_handle("a")
            assert await asyncio.wait_for(actor.chat("start", sender="user"), 3) == "finished"
            assert len(source_llm.calls) == 2  # 同步回复不触发额外 mailbox 新回合。
            assert any("b reply" in str(m) for m in source_llm.calls[-1])
            if broadcast:
                assert any("c reply" in str(m) for m in source_llm.calls[-1])
            checkpoint = await actor._context_manager.persistence.load_checkpoint("id-a")
            pending = [
                node for node in checkpoint.nodes if node.metadata.get("pending_tool_replies")
            ]
            assert len(pending) == 1
            assert pending[0].metadata["delivery_context"]["message_manager"] is True
            assert pending[0].metadata["pending_tool_call_ids"] == ["request-1"]
            async with aiosqlite.connect(path) as db:
                waits = await (await db.execute("SELECT state FROM mm_inline_waits")).fetchall()
                requests = await (
                    await db.execute(
                        "SELECT message_id FROM mm_messages "
                        "WHERE sender_id='id-a' AND kind='request'"
                    )
                ).fetchall()
            assert waits == [("consumed",)]
            assert len(requests) == 1
            assert len(await mm.get_deliveries(requests[0][0])) == (2 if broadcast else 1)
        finally:
            await supervisor.shutdown()


async def test_external_delegate_and_broadcast_keep_returned_results(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
        try:
            await spawn(supervisor, "a", ScriptedLLM(["a reply"]), agent_id="id-a")
            await spawn(supervisor, "b", ScriptedLLM(["delegated", "b reply"]), agent_id="id-b")
            assert await supervisor.delegate("a", "b", "work") == "delegated"
            replies = await supervisor.broadcast("hello", sender="user")
            assert {r["responder"]: r["content"] for r in replies} == {
                "a": "a reply",
                "b": "b reply",
            }
            async with aiosqlite.connect(path) as db:
                count = await (
                    await db.execute(
                        "SELECT count(*) FROM mm_messages WHERE idempotency_key LIKE 'broadcast:%'"
                    )
                ).fetchone()
            assert count[0] == 1
        finally:
            await supervisor.shutdown()


async def test_sync_cycle_finishes_with_diagnostic_tool_result(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="cluster", message_manager=mm)
        a_llm, b_llm = RequestLLM("b"), RequestLLM("a")
        try:
            await spawn(supervisor, "a", a_llm)
            await spawn(supervisor, "b", b_llm)
            assert await asyncio.wait_for(supervisor.send("a", "start"), 3) == "finished"
            await asyncio.wait_for(mm.dispatch_pending(), 3)
            assert any("cycle detected" in str(m) for call in b_llm.calls for m in call)
            assert mm._wait_edges == {}
        finally:
            await supervisor.shutdown()


async def test_sync_wait_checkpoint_survives_real_process_exit(tmp_path):
    path = tmp_path / "core.sqlite"
    script = """
import asyncio, os, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2]).parents[1]))
ns = runpy.run_path(sys.argv[2])
class CrashManager(ns['MessageManager']):
    async def _apply_atomic(self, scope, changes):
        await super()._apply_atomic(scope, changes)
        if changes.nodes[0].metadata.get('pending_tool_replies'): os._exit(23)
async def main():
    path = Path(sys.argv[1])
    await ns['bootstrap'](path)
    async with CrashManager(path, project_id='p', agent_ids=(), session_routing=True,
                            allow_persisted_agents=True) as mm:
        supervisor = ns['SupervisorActor'](cluster_id='c', message_manager=mm)
        await ns['spawn'](supervisor, 'a', ns['RequestLLM']())
        await ns['spawn'](supervisor, 'b', ns['ScriptedLLM'](['b reply']))
        await supervisor.send('a', 'start')
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
    source_llm, peer_llm = ScriptedLLM(["after restart"]), ScriptedLLM(["b reply"])
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        try:
            await spawn(supervisor, "a", source_llm)
            await spawn(supervisor, "b", peer_llm)
            async with aiosqlite.connect(path) as db:
                row = await (
                    await db.execute(
                        "SELECT delivery_id FROM mm_deliveries "
                        "WHERE recipient_id='a' AND state='interrupted'"
                    )
                ).fetchone()
            progress = await mm.get_progress(row[0])
            await mm.resume_interrupted(
                row[0],
                expected_last_node_id=progress.delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            await asyncio.wait_for(mm.dispatch_pending(), 3)
            assert (await mm.get_progress(row[0])).delivery.state == DeliveryState.COMPLETED
            assert len(source_llm.calls) == len(peer_llm.calls) == 1
            assert any("b reply" in str(m) for m in source_llm.calls[0])
            assert any(
                block.tool_call_id == "request-1"
                for m in source_llm.calls[0]
                for block in m.tool_results
            )
        finally:
            await supervisor.shutdown()


async def test_sync_timeout_keeps_request_and_delivers_late_reply(tmp_path):
    class SlowLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(["late reply"])
            self.release = asyncio.Event()

        async def generate(self, messages, tools=None):
            await self.release.wait()
            return await super().generate(messages, tools)

    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    source_llm, slow_llm = RequestLLM(), SlowLLM()
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        try:
            await spawn(supervisor, "a", source_llm)
            await spawn(supervisor, "b", slow_llm, timeout=0.03)
            assert await asyncio.wait_for(supervisor.send("a", "start"), 2) == "finished"
            assert any("TimeoutError" in str(m) for m in source_llm.calls[-1])
            slow_llm.release.set()
            await asyncio.wait_for(mm.dispatch_pending(), 2)
            assert any("late reply" in str(m) for call in source_llm.calls for m in call)
            async with aiosqlite.connect(path) as db:
                waits = await (await db.execute("SELECT state FROM mm_inline_waits")).fetchall()
            assert waits == [("released",)]
        finally:
            slow_llm.release.set()
            await supervisor.shutdown()


async def test_broadcast_partial_failure_keeps_successful_responses(tmp_path):
    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    source_llm = RequestLLM(broadcast=True)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        try:
            await spawn(supervisor, "a", source_llm)
            await spawn(supervisor, "b", ScriptedLLM(["successful b"]))
            await spawn(supervisor, "c", ScriptedLLM([]))
            assert await asyncio.wait_for(supervisor.send("a", "start"), 2) == "finished"
            assert any("successful b" in str(m) for m in source_llm.calls[-1])
            assert any("errors" in str(m) for m in source_llm.calls[-1])
        finally:
            await supervisor.shutdown()


async def test_sync_checkpoint_records_model_usage_once(tmp_path):
    from ghrah.types.tokens import TokenUsage

    class UsageLLM(RequestLLM):
        async def generate(self, messages, tools=None):
            response = await super().generate(messages, tools)
            response.token_usage = TokenUsage(input_tokens=10, output_tokens=2, total_tokens=12)
            return response

    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        llm = UsageLLM()
        try:
            await spawn(supervisor, "a", llm)
            await spawn(supervisor, "b", ScriptedLLM(["b reply"]))
            assert await supervisor.send("a", "start") == "finished"
            actor = await supervisor.get_agent_handle("a")
            assert len(llm.calls) == 2
            assert actor._context_manager.get_cumulative_token_usage()["input_tokens"] == 20
        finally:
            await supervisor.shutdown()


async def test_structured_input_and_reply_blocks_survive_persistent_route(tmp_path):
    from ghrah.chat.content import ReasoningBlock, block_to_dict
    from ghrah.core.message import AgentMessage

    class BlockLLM(ScriptedLLM):
        async def generate(self, messages, tools=None):
            self.calls.append(list(messages))
            return LLMResponse(
                content_blocks=[ReasoningBlock(reasoning="internal"), TextBlock(text="reply")]
            )

    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    llm = BlockLLM([])
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        try:
            await spawn(supervisor, "a", llm)
            actor = await supervisor.get_agent_handle("a")
            reply = await actor.receive(
                AgentMessage(
                    sender="user",
                    recipient="a",
                    content="",
                    content_blocks=[block_to_dict(TextBlock(text="structured input"))],
                )
            )
            assert reply.content == "reply"
            assert reply.content_blocks == [
                block_to_dict(ReasoningBlock(reasoning="internal")),
                block_to_dict(TextBlock(text="reply")),
            ]
            assert any("structured input" in m.text for m in llm.calls[0])
        finally:
            await supervisor.shutdown()


async def test_sync_post_execute_hook_observes_final_reply(tmp_path):
    from ghrah.scheme.execution.hooks import Hook, HookPoint, HookResult

    observed = []

    class VerifyHook(Hook):
        hook_point = HookPoint.POST_EXECUTE

        async def should_trigger(self, context):
            return context.current_ability_name == "send_message"

        async def execute(self, context, result):
            observed.append(result.data.get("response"))
            return HookResult.continue_()

    path = tmp_path / "core.sqlite"
    await bootstrap(path)
    async with MessageManager(
        path, project_id="p", agent_ids=(), session_routing=True, allow_persisted_agents=True
    ) as mm:
        supervisor = SupervisorActor(cluster_id="c", message_manager=mm)
        try:
            await spawn(supervisor, "a", RequestLLM())
            await spawn(supervisor, "b", ScriptedLLM(["b reply"]))
            actor = await supervisor.get_agent_handle("a")
            actor._all_hooks.append(VerifyHook())
            assert await supervisor.send("a", "start") == "finished"
            assert observed == ["b reply"]
            assert actor._ability_executor._deferred_contexts == {}
        finally:
            await supervisor.shutdown()
