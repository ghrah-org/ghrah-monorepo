# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""compact 回合编排测试（触发门 / ghrah.builtin 流程 / 紧急阶梯）。

覆盖：
- 阈值触发：锚点 >= threshold → 下一轮进 compact 回合（不计 max_iterations、
  不排空消息队列、不执行 agent 级 HookPoint）
- 手动触发与合并幂等；窗外为空清标志不循环（振荡防护）
- ghrah.builtin 快照布局（system/preamble/summary/kept 顺序）
- LLM 摘要失败降级（折叠视图提交、degraded 标记）
- 紧急压缩阶梯：厂商超限 → rollback 不丢消息 → 减半 compact → 重试成功；
  4 次后硬失败；非超限错误不进阶梯
- 事件发布（ActionChainUpdated 含 compact 节点）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.chat.message import ChatMessage
from ghrah.context.compact import SUMMARY_MESSAGE_PREFIX
from ghrah.core.config import AgentConfig
from ghrah.core.events import CoreEventType
from ghrah.core.exceptions import AgentError
from ghrah.scheme.execution.base import Ability, ActionOutcome, ActionResult
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.types.config_types import WindowConfig


class MockConversationAbility(Ability):
    """用于测试的 mock ability。"""

    def __init__(self) -> None:
        self._result = ActionResult(outcome=ActionOutcome.SUCCESS, data={"response": "ok"})

    @property
    def name(self) -> str:
        return "conversation"

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        return self._result

    def get_hooks(self) -> list[Any]:
        return []

    def bind_tool(self) -> dict[str, Any] | None:
        return None


def _create_agent(config: AgentConfig) -> Any:
    from ghrah.agents.builder import AgentBuilder

    agent = AgentBuilder.from_config(config)
    agent.register_ability(MockConversationAbility())
    return agent


def _compact_config(max_tokens: int = 1000, threshold: float = 0.8) -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        system_prompt="You are a test agent.",
        window=WindowConfig(max_tokens=max_tokens, strategies=[], compact_threshold=threshold),
    )


def _summary_llm(summary_text: str = "Compacted summary of earlier rounds.") -> AsyncMock:
    """主循环与摘要共用一个 mock LLM：普通文本响应即可。"""
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(content_blocks=[TextBlock(text=summary_text)])
    llm.configure_tools = MagicMock()
    return llm


def _compact_nodes(history: list[Any]) -> list[Any]:
    return [n for n in history if n.metadata.get("node_kind") == "compact"]


class TestThresholdTriggerGate:
    """阈值触发门。"""

    @pytest.mark.asyncio
    async def test_compact_round_runs_when_anchor_reaches_threshold(self) -> None:
        """锚点 >= threshold：第二轮进 compact 回合，产出 compact 节点。"""
        agent = _create_agent(_compact_config())
        # 每轮文本响应让 AFTER_ACTION 之外的循环继续：用 max_iterations=3 控制
        agent._llm = _summary_llm()
        agent._iteration_state.max_iterations = 3
        agent._iteration_state.reset()
        # 预置历史（3 轮普通节点，保证窗外非空）
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        # 锚点超阈值（0.8 × 1000 = 800）
        cm.record_window_occupied(900)

        await agent._drive_loop()

        compact_nodes = _compact_nodes(cm.get_history())
        assert len(compact_nodes) == 1
        node = compact_nodes[0]
        assert node.ability_names == ["compact"]
        assert node.metadata["trigger_source"] == "threshold"
        assert node.metadata["method"] == "ghrah.builtin"
        # 提交后 store 即压缩视图，锚点失效
        assert cm._window_occupied is None

    @pytest.mark.asyncio
    async def test_compact_round_not_counted_in_iterations(self) -> None:
        """compact 回合不消耗 max_iterations（引擎自维护动作）。"""
        agent = _create_agent(_compact_config())
        agent._llm = _summary_llm()
        agent._iteration_state.max_iterations = 2
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        cm.record_window_occupied(900)
        before_iter = agent._iteration_state.iteration

        await agent._drive_loop()

        # compact 回合 + 2 轮普通迭代；iteration 只因普通迭代前进
        assert agent._iteration_state.iteration <= before_iter + 2
        assert len(_compact_nodes(cm.get_history())) == 1

    @pytest.mark.asyncio
    async def test_compact_round_does_not_drain_queue_or_run_hooks(self) -> None:
        """compact 回合不排空消息队列、不执行 agent 级 HookPoint。"""
        from ghrah.scheme.execution.hooks import Hook, HookPoint

        class _CountingHook(Hook):
            hook_point = HookPoint.BEFORE_ACTION

            def __init__(self) -> None:
                self.calls = 0

            async def should_trigger(self, context: Any) -> bool:
                return True

            async def execute(self, context: Any, result: Any) -> Any:
                from ghrah.scheme.execution.hooks import HookResult

                self.calls += 1
                return HookResult.continue_()

        agent = _create_agent(_compact_config())
        agent._llm = _summary_llm()
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()
        hook = _CountingHook()
        agent._all_hooks.append(hook)
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        cm.record_window_occupied(900)
        await agent._message_queue.put(ChatMessage.user(text_or_blocks="queued-msg"))

        # 只跑一轮 compact 回合（直接调用，隔离驱动循环其他行为）
        result = await agent._run_compact_round("manual")

        assert result["executed"] is True
        # compact 回合自身不消费队列：queued-msg 仍在队列
        assert agent._message_queue.qsize() == 1
        # 回合内不执行 agent 级 hook（BEFORE_ACTION 未因 compact 执行）
        assert hook.calls == 0
        assert len(_compact_nodes(cm.get_history())) == 1

    @pytest.mark.asyncio
    async def test_empty_window_clears_flag_no_loop(self) -> None:
        """窗外为空：清手动标志、置跳过标志，不无限循环（振荡防护）。"""
        agent = _create_agent(_compact_config())
        agent._llm = _summary_llm()
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()
        cm = agent._context_manager
        # 只留 1 轮普通节点（keep_recent=2 → 窗外为空）
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="only-round")])
        cm.commit_iteration(ability_names=["only-round"])
        cm.record_window_occupied(900)
        cm.request_compact()

        result = await agent._run_compact_round("manual")

        assert result["executed"] is False
        assert result["reason"] == "empty_window"
        assert cm.compact_requested is False
        # 驱动循环整体也不死循环
        await agent._drive_loop()
        assert len(_compact_nodes(cm.get_history())) == 0


class TestSnapshotLayout:
    """ghrah.builtin 快照布局与降级。"""

    @pytest.mark.asyncio
    async def test_snapshot_layout_summary_path(self) -> None:
        """正常路径：[system(session), preamble, summary, kept…]；工具配对完整。"""
        agent = _create_agent(_compact_config())
        agent._llm = _summary_llm("SUMMARY-TEXT")
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(4):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"user-{i}")])
            cm.commit_iteration(ability_names=[f"round-{i}"])
        cm.record_window_occupied(900)

        result = await agent._run_compact_round("manual")

        assert result["executed"] is True
        assert result["degraded"] is False
        node = _compact_nodes(cm.get_history())[0]
        snapshot = node.messages_snapshot

        assert [m.role for m in snapshot[:3]] == ["system", "system", "system"]
        assert snapshot[2].text == SUMMARY_MESSAGE_PREFIX + "SUMMARY-TEXT"
        # 保留窗 = 最近 2 轮的 delta（keep_recent=2）
        assert [m.text for m in snapshot[3:]] == ["user-2", "user-3"]
        # metadata 形状
        md = node.metadata
        assert md["summarized_range"] == [cm.get_history()[1].id, cm.get_history()[2].id]
        assert md["kept_recent_nodes"] == 2
        assert md["tokens_before"] == 900
        assert md["post_check"] == "ok"
        assert md["degraded"] is False

    @pytest.mark.asyncio
    async def test_summary_llm_failure_degrades_to_folded_view(self) -> None:
        """LLM 摘要失败 → 折叠视图提交，degraded=True，不停摆。"""
        agent = _create_agent(_compact_config())
        llm = AsyncMock()
        llm.generate.side_effect = RuntimeError("summary llm down")
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(4):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"user-{i}")])
            cm.commit_iteration(ability_names=[f"round-{i}"])
        cm.record_window_occupied(900)

        result = await agent._run_compact_round("manual")

        assert result["executed"] is True
        assert result["degraded"] is True
        node = _compact_nodes(cm.get_history())[0]
        snapshot_texts = [m.text for m in node.messages_snapshot]
        # 降级路径：前置提示 + 折叠后摘要窗消息原样 + 保留窗
        assert "compacted" in snapshot_texts[1]
        assert "user-0" in snapshot_texts  # 摘要窗消息保留在降级视图中
        assert node.metadata["degraded"] is True

    @pytest.mark.asyncio
    async def test_event_published_for_compact_node(self) -> None:
        """compact 节点提交后发布 ActionChainUpdated（serialize_node 形态）。"""
        agent = _create_agent(_compact_config())
        agent._llm = _summary_llm()
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher
        cm = agent._context_manager
        for i in range(4):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"user-{i}")])
            cm.commit_iteration(ability_names=[f"round-{i}"])

        await agent._run_compact_round("manual")

        chain_events = [
            call.args[0]
            for call in publisher.publish.await_args_list
            if call.args[0].event_type == CoreEventType.ACTION_CHAIN_UPDATED
        ]
        assert len(chain_events) == 1
        assert chain_events[0].node["ability_names"] == ["compact"]
        assert chain_events[0].node["metadata"]["node_kind"] == "compact"


class TestEmergencyLadder:
    """紧急压缩阶梯。"""

    @pytest.mark.asyncio
    async def test_context_limit_error_triggers_emergency_compact_and_retry(self) -> None:
        """超限 → rollback 不丢消息 → 紧急 compact（减半）→ 重试成功。"""
        agent = _create_agent(_compact_config(max_tokens=1000))
        llm = AsyncMock()
        # 首轮超限；compact 摘要；重试成功；（重试轮成功后循环继续的下一轮）
        llm.generate.side_effect = [
            RuntimeError("prompt is too long: maximum context length exceeded"),
            LLMResponse(content_blocks=[TextBlock(text="summary")]),
            LLMResponse(content_blocks=[TextBlock(text="recovered")]),
            LLMResponse(content_blocks=[TextBlock(text="done")]),
        ]
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 2
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        await agent._message_queue.put(ChatMessage.user(text_or_blocks="user-msg"))

        await agent._drive_loop()  # 不抛 AgentError

        compact_nodes = _compact_nodes(cm.get_history())
        assert len(compact_nodes) == 1
        assert compact_nodes[0].metadata["trigger_source"] == "emergency"
        # 回滚重入队的消息在重试轮进入 store
        texts = [m.text for m in cm.message_store.current_messages]
        assert "user-msg" in texts

    @pytest.mark.asyncio
    async def test_hard_fail_after_four_halvings(self) -> None:
        """连续超限 4 次减半后仍失败 → AgentError 硬失败。"""
        agent = _create_agent(_compact_config(max_tokens=1000))
        llm = AsyncMock()
        # 5 次超限（4 次进阶梯 + 1 次硬失败）；中间 4 次摘要成功
        responses: list[Any] = [RuntimeError("prompt is too long")]
        for _ in range(3):
            responses.append(LLMResponse(content_blocks=[TextBlock(text="summary")]))
            responses.append(RuntimeError("prompt is too long"))
        responses.append(LLMResponse(content_blocks=[TextBlock(text="summary")]))
        responses.append(RuntimeError("prompt is too long"))
        llm.generate.side_effect = responses
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 10
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(4):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])

        with pytest.raises(AgentError):
            await agent._drive_loop()

        compact_nodes = _compact_nodes(cm.get_history())
        assert len(compact_nodes) == 4  # 4 次紧急 compact（budget 500/250/125/62）

    @pytest.mark.asyncio
    async def test_vendor_window_backfill_corrects_budget(self) -> None:
        """超限报文含窗口数字 → 预算以厂商口径回填（不减半）→ 重试成功。"""
        agent = _create_agent(_compact_config(max_tokens=1000))
        llm = AsyncMock()
        llm.generate.side_effect = [
            RuntimeError("prompt is too long: 2000 tokens > 1900 maximum"),
            LLMResponse(content_blocks=[TextBlock(text="summary")]),
            LLMResponse(content_blocks=[TextBlock(text="recovered")]),
            LLMResponse(content_blocks=[TextBlock(text="done")]),
        ]
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 2
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        await agent._message_queue.put(ChatMessage.user(text_or_blocks="user-msg"))

        await agent._drive_loop()

        # 预算被厂商口径回填：来源 vendor、值 1900（非减半 500）
        wm = cm.window_manager
        assert wm.max_tokens == 1900
        assert wm.max_tokens_source == "vendor"
        compact_nodes = _compact_nodes(cm.get_history())
        assert len(compact_nodes) == 1
        assert compact_nodes[0].metadata["trigger_source"] == "emergency"

    @pytest.mark.asyncio
    async def test_vendor_backfill_at_same_budget_falls_to_halving(self) -> None:
        """报文窗口与当前预算一致仍超限 → 输出挤压，回落减半阶梯。"""
        agent = _create_agent(_compact_config(max_tokens=1000))
        llm = AsyncMock()
        # 首次超限报文窗口恰为当前预算 1000 → 回填不动值但也不重抛，
        # 走减半；compact 摘要成功后重试成功
        llm.generate.side_effect = [
            RuntimeError("maximum context length is 1000 tokens"),
            LLMResponse(content_blocks=[TextBlock(text="summary")]),
            LLMResponse(content_blocks=[TextBlock(text="recovered")]),
            LLMResponse(content_blocks=[TextBlock(text="done")]),
        ]
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 2
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        await agent._message_queue.put(ChatMessage.user(text_or_blocks="user-msg"))

        await agent._drive_loop()

        # 值未变（回填拒绝同值），来源仍为 declared
        wm = cm.window_manager
        assert wm.max_tokens == 1000
        assert wm.max_tokens_source == "declared"
        assert len(_compact_nodes(cm.get_history())) == 1

    @pytest.mark.asyncio
    async def test_non_context_error_skips_ladder(self) -> None:
        """非超限错误不进阶梯，直接 AgentError。"""
        agent = _create_agent(_compact_config())
        llm = AsyncMock()
        llm.generate.side_effect = RuntimeError("plain connection error")
        llm.configure_tools = MagicMock()
        agent._llm = llm
        agent._iteration_state.max_iterations = 2
        agent._iteration_state.reset()
        cm = agent._context_manager
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])

        with pytest.raises(AgentError):
            await agent._drive_loop()

        assert len(_compact_nodes(cm.get_history())) == 0
