# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""已消费投递的显式续跑：真实进程退出、持久检查点和恢复边界。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest
from _helpers import ScriptedLLM

from ghrah.agents.builder import AgentBuilder
from ghrah.chat.content import ToolCallBlock
from ghrah.chat.format import LLMResponse
from ghrah.communication import DeliveryState, MessageKind, MessageManager
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.core.config import AgentConfig
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.scheme.execution.base import Ability, ActionOutcome, ActionResult
from ghrah.types.config_types import ContextConfig


class CountingTool(Ability):
    """写入外部文件，以验证已提交工具不会在恢复时重放。"""

    def __init__(self, path: Path):
        self.path = path

    @property
    def name(self) -> str:
        return "count"

    def get_hooks(self):
        return []

    def bind_tool(self):
        return {
            "type": "function",
            "function": {
                "name": "count",
                "description": "count",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def execute(self, context):
        count = int(self.path.read_text()) if self.path.exists() else 0
        self.path.write_text(str(count + 1))
        return ActionResult(outcome=ActionOutcome.SUCCESS, data={"count": count + 1})


class ToolLLM(ScriptedLLM):
    """每轮调用计数工具，子进程在第二轮持久提交后退出。"""

    async def generate(self, messages, tools=None):
        self.calls.append(list(messages))
        return LLMResponse(
            content_blocks=[ToolCallBlock(id=f"call-{len(self.calls)}", name="count", arguments={})]
        )


async def make_agent(backend, llm, count_path):
    """装配真实能力和同文件 Core 持久层。"""
    return AgentBuilder.from_config(
        AgentConfig(name="b", agent_id="b", context=ContextConfig(auto_persist=False)),
        abilities=[CountingTool(count_path), ConversationAbility()],
        llm_factory=lambda config: llm,
        persistence_factory=lambda config: backend,
    )


async def request(mm):
    """幂等获取原请求。"""
    return await mm.send(
        sender_id="a",
        recipient_ids=("b",),
        content="original input",
        kind=MessageKind.REQUEST,
        idempotency_key="input",
    )


async def crash_after_two_commits(path, count_path):
    """以独立进程制造工作已落库但最终结果尚未保存的中断。"""
    script = """
import asyncio, os, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[3]).parents[1]))
ns = runpy.run_path(sys.argv[3])
class CrashManager(ns['MessageManager']):
    async def _commit_atomic(self, scope, changes):
        await super()._commit_atomic(scope, changes)
        if int(Path(sys.argv[2]).read_text()) == 2: os._exit(23)
async def main():
    async with ns['SqliteBackend'](sys.argv[1]) as backend:
        agent = await ns['make_agent'](backend, ns['ToolLLM']([]), Path(sys.argv[2]))
        await agent._context_manager.persist()
        async with CrashManager(sys.argv[1], project_id='p', agent_ids=('a','b')) as mm:
            await ns['request'](mm)
            await mm.bind_atomic(agent)
            await mm.dispatch_pending()
asyncio.run(main())
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(path), str(count_path), str(Path(__file__).resolve())],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 23, result.stderr.decode()


@pytest.mark.parametrize("queued_restart", [False, True])
async def test_resume_from_latest_commit_without_reinjecting_or_replaying(tmp_path, queued_restart):
    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
        msg = await request(mm)
        (delivery,) = await mm.get_deliveries(msg.message_id)
        progress = await mm.get_progress(delivery.delivery_id)
        assert delivery.state == DeliveryState.INTERRUPTED
        assert delivery.last_node_id != delivery.consumed_node_id
        assert progress.resume_block_reason is None
        with pytest.raises(ValueError, match="acknowledgement"):
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.last_node_id,
                acknowledge_side_effect_risk=False,
            )
        with pytest.raises(ValueError, match="checkpoint"):
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.consumed_node_id,
                acknowledge_side_effect_risk=True,
            )
        if queued_restart:
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            assert (await mm.get_progress(delivery.delivery_id)).delivery.resume_requested
            with pytest.raises(ValueError, match="interrupted"):
                await mm.resume_interrupted(
                    delivery.delivery_id,
                    expected_last_node_id=delivery.last_node_id,
                    acknowledge_side_effect_risk=True,
                )
    llm = ScriptedLLM(["resumed final"])
    async with SqliteBackend(path) as backend:
        agent = await make_agent(backend, llm, count_path)
        await agent._context_manager.restore("b")
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            await mm.bind_atomic(agent)
            if not queued_restart:
                await mm.resume_interrupted(
                    delivery.delivery_id,
                    expected_last_node_id=delivery.last_node_id,
                    acknowledge_side_effect_risk=True,
                )
            await mm.dispatch_pending()
            (finished,) = await mm.get_deliveries(msg.message_id)
            assert finished.state == DeliveryState.COMPLETED
            assert finished.consumed_node_id == delivery.consumed_node_id
            assert finished.last_node_id == finished.result_node_id
            assert (await mm.get_replies(msg.message_id))[0].content == "resumed final"
            assert (
                await mm.get_progress(delivery.delivery_id)
            ).resume_block_reason == "final_result_already_saved"
        checkpoint = await backend.load_checkpoint("b")
        inputs = [
            m
            for n in checkpoint.nodes
            for m in n.messages_delta
            if m.metadata.get("delivery_id") == delivery.delivery_id
        ]
        assert len(inputs) == 1
        assert count_path.read_text() == "2"
        assert len(llm.calls) == 1
        assert any("count" in str(m.content_blocks) for m in llm.calls[0]), llm.calls[0]


@pytest.mark.parametrize(
    "problem,reason",
    [
        ("head", "session_head_changed"),
        ("closed", "session_or_branch_closed"),
        ("legacy", "last_checkpoint_unknown"),
        ("unconsumed", "input_not_consumed"),
    ],
)
async def test_resume_rejects_invalid_checkpoint(tmp_path, problem, reason):
    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
        msg = await request(mm)
        (delivery,) = await mm.get_deliveries(msg.message_id)
        async with aiosqlite.connect(path) as db:
            if problem == "head":
                await db.execute("UPDATE branches SET head_node_id=?", (delivery.consumed_node_id,))
            elif problem == "closed":
                await db.execute("UPDATE sessions SET lifecycle='closed'")
            elif problem == "legacy":
                await db.execute("UPDATE mm_deliveries SET last_node_id=NULL")
            else:
                await db.execute("UPDATE mm_deliveries SET consumed_node_id=NULL")
            await db.commit()
        progress = await mm.get_progress(delivery.delivery_id)
        assert progress.resume_block_reason == reason
        with pytest.raises(ValueError, match=reason):
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=progress.delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
        assert count_path.read_text() == "2"


@pytest.mark.parametrize("atomic_receiver", [False, True])
async def test_queued_resume_rechecks_receiver_and_checkpoint(tmp_path, atomic_receiver):
    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    llm = ScriptedLLM(["must not run"])
    async with SqliteBackend(path) as backend:
        agent = await make_agent(backend, llm, count_path)
        await agent._context_manager.restore("b")
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            msg = await request(mm)
            (delivery,) = await mm.get_deliveries(msg.message_id)
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            if atomic_receiver:
                await mm.bind_atomic(agent)
                # 绑定后、领取前改变权威 Head，检验申请之后的再次校验。
                async with mm._lock, aiosqlite.connect(path) as db:
                    await db.execute(
                        "UPDATE branches SET head_node_id=?", (delivery.consumed_node_id,)
                    )
                    await db.commit()
            else:
                mm.bind("b", agent.receive)
            await mm.dispatch_pending()
            (failed,) = await mm.get_deliveries(msg.message_id)
            assert failed.state == DeliveryState.INTERRUPTED
            assert failed.error == (
                "session_head_changed" if atomic_receiver else "atomic_receiver_required"
            )
            assert not failed.resume_requested
            assert llm.calls == []


async def test_failed_continuation_can_be_explicitly_resumed_again(tmp_path):
    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    async with SqliteBackend(path) as backend:
        agent = await make_agent(backend, ScriptedLLM([]), count_path)
        await agent._context_manager.restore("b")
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            msg = await request(mm)
            (delivery,) = await mm.get_deliveries(msg.message_id)
            await mm.bind_atomic(agent)
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            await mm.dispatch_pending()
            progress = await mm.get_progress(delivery.delivery_id)
            assert progress.delivery.state == DeliveryState.FAILED
            assert progress.resume_block_reason is None
            await mm.unbind("b")
            good_llm = ScriptedLLM(["done"])
            restored = await make_agent(backend, good_llm, count_path)
            await restored._context_manager.restore("b")
            await mm.bind_atomic(restored)
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=progress.delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            await mm.dispatch_pending()
            assert (await mm.get_deliveries(msg.message_id))[0].state == DeliveryState.COMPLETED
            assert count_path.read_text() == "2"


async def test_v4_upgrade_does_not_guess_legacy_execution_checkpoint(tmp_path):
    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    async with aiosqlite.connect(path) as db:
        await db.execute("ALTER TABLE mm_deliveries DROP COLUMN last_node_id")
        await db.execute("ALTER TABLE mm_deliveries DROP COLUMN resume_requested")
        await db.execute("UPDATE mm_meta SET version=4 WHERE singleton=1")
        await db.commit()
    async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
        msg = await request(mm)
        (delivery,) = await mm.get_deliveries(msg.message_id)
        assert delivery.consumed_node_id is not None
        assert delivery.last_node_id is None
        assert (
            await mm.get_progress(delivery.delivery_id)
        ).resume_block_reason == "last_checkpoint_unknown"


async def test_resume_session_race_does_not_leave_processing(tmp_path, monkeypatch):
    from ghrah.context.manager import DeliverySessionUnavailableError

    path, count_path = tmp_path / "core.sqlite", tmp_path / "count"
    await crash_after_two_commits(path, count_path)
    async with SqliteBackend(path) as backend:
        agent = await make_agent(backend, ScriptedLLM([]), count_path)
        await agent._context_manager.restore("b")

        async def session_gone(*args, **kwargs):
            raise DeliverySessionUnavailableError("Session disappeared in Runner")

        monkeypatch.setattr(agent, "resume_atomic", session_gone)
        async with MessageManager(path, project_id="p", agent_ids=("a", "b")) as mm:
            msg = await request(mm)
            (delivery,) = await mm.get_deliveries(msg.message_id)
            await mm.bind_atomic(agent)
            await mm.resume_interrupted(
                delivery.delivery_id,
                expected_last_node_id=delivery.last_node_id,
                acknowledge_side_effect_risk=True,
            )
            await mm.dispatch_pending()
            (failed,) = await mm.get_deliveries(msg.message_id)
            assert failed.state == DeliveryState.FAILED
            assert failed.consumed_node_id == delivery.consumed_node_id
            assert not failed.blocked_reason
