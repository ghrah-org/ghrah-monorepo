# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""上下文占用事件与手动 compact 命令入口测试。

覆盖：
- pre_call/post_call 双相位事件（有 window agent 双发、无 window agent
  仅 post_call；post_call 真实值口径、pre_call 锚点口径可 None）
- 事件字段组装（budget/threshold/compaction/iteration）
- 发布失败不阻断主循环
- request_compact() 双态：驱动中置标志回 scheduled（连续请求合并）、
  空闲直接执行回 node_id、窗外为空回 empty_window
- receive 期间 _drive_active 置位正确（进入/退出驱动）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.chat.message import ChatMessage
from ghrah.core.config import AgentConfig
from ghrah.core.events import CoreEventType
from ghrah.core.message import AgentMessage
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


def _window_config(max_tokens: int = 1000, threshold: float = 0.8) -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        system_prompt="You are a test agent.",
        window=WindowConfig(max_tokens=max_tokens, strategies=[], compact_threshold=threshold),
    )


def _plain_config() -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        system_prompt="You are a test agent.",
    )


def _llm_with_usage(
    input_tokens: int = 500,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> AsyncMock:
    """带 token_usage 的 mock LLM（首响应即含 usage，无需重试）。"""
    from ghrah.types.tokens import TokenUsage

    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(
        content_blocks=[TextBlock(text="answer")],
        token_usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
    )
    llm.configure_tools = MagicMock()
    return llm


def _usage_events(publisher: MagicMock) -> list[Any]:
    return [
        call.args[0]
        for call in publisher.publish.await_args_list
        if call.args[0].event_type == CoreEventType.CONTEXT_USAGE_UPDATED
    ]


# ── 事件发布 ──


class TestContextUsageEvents:
    @pytest.mark.asyncio
    async def test_window_agent_publishes_pre_and_post_call(self) -> None:
        """配置 window 的 agent：一次 action 发布 pre_call + post_call 两事件。"""
        agent = _create_agent(_window_config())
        agent._llm = _llm_with_usage(input_tokens=500, output_tokens=50)
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher
        cm = agent._context_manager
        for i in range(2):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])

        await agent._action({})

        events = _usage_events(publisher)
        assert len(events) == 2
        pre, post = events
        assert pre.phase == "pre_call"
        assert pre.basis == "anchor"
        assert pre.occupied_tokens is None  # 首次调用无锚点
        assert pre.real_input_tokens is None
        assert pre.compact_threshold == 0.8
        assert pre.budget_tokens == 1000

        assert post.phase == "post_call"
        assert post.basis == "real"
        assert post.occupied_tokens == 500  # 真实 input_tokens
        assert post.real_input_tokens == 500
        assert post.real_output_tokens == 50
        assert post.real_cache_read_tokens == 0  # 未上报 cache 时为 0
        assert post.real_cache_write_tokens == 0
        assert post.compaction is not None
        assert post.compaction["threshold"] == 0.8
        assert post.compaction["needs_compaction"] is False  # 500 < 800

    @pytest.mark.asyncio
    async def test_post_call_carries_cache_dimensions(self) -> None:
        """post_call 事件携带 cache 三段式维度（归一化口径下 input 含 cache）。"""
        agent = _create_agent(_plain_config())
        agent._llm = _llm_with_usage(
            input_tokens=2400, output_tokens=50, cache_read_tokens=2000, cache_write_tokens=300
        )
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher

        await agent._action({})

        events = _usage_events(publisher)
        assert len(events) == 1
        post = events[0]
        assert post.real_input_tokens == 2400
        assert post.real_cache_read_tokens == 2000
        assert post.real_cache_write_tokens == 300

    @pytest.mark.asyncio
    async def test_plain_agent_publishes_only_post_call(self) -> None:
        """无 window 配置的 agent：仅发布 post_call（真实值）。"""
        agent = _create_agent(_plain_config())
        agent._llm = _llm_with_usage(input_tokens=300)
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher

        await agent._action({})

        events = _usage_events(publisher)
        assert len(events) == 1
        post = events[0]
        assert post.phase == "post_call"
        assert post.basis == "real"
        assert post.occupied_tokens == 300
        assert post.budget_tokens == 0
        assert post.compact_threshold is None
        assert post.compaction is None  # 未配置窗口无决策记录

    @pytest.mark.asyncio
    async def test_pre_call_uses_existing_anchor(self) -> None:
        """pre_call 取既有锚点（上一轮真实值）。"""
        agent = _create_agent(_window_config())
        agent._llm = _llm_with_usage(input_tokens=850)
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher
        agent._context_manager.record_window_occupied(850)

        await agent._action({})

        events = _usage_events(publisher)
        pre = events[0]
        assert pre.occupied_tokens == 850
        assert pre.basis == "anchor"

    @pytest.mark.asyncio
    async def test_post_call_records_anchor_before_event(self) -> None:
        """post_call 事件时锚点已更新（事件与锚点一致性）。"""
        agent = _create_agent(_window_config())
        agent._llm = _llm_with_usage(input_tokens=850)
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher

        await agent._action({})

        assert agent._context_manager._window_occupied == 850
        post = _usage_events(publisher)[1]
        assert post.occupied_tokens == 850
        assert post.compaction["needs_compaction"] is True  # 850 >= 800

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_break_action(self) -> None:
        """事件发布异常不阻断主循环（fire-and-forget）。"""
        agent = _create_agent(_window_config())
        agent._llm = _llm_with_usage(input_tokens=100)
        publisher = MagicMock()
        publisher.publish = AsyncMock(side_effect=RuntimeError("publisher down"))
        agent._event_publisher = publisher

        output = await agent._action({})

        assert "results" in output  # action 正常完成

    @pytest.mark.asyncio
    async def test_no_usage_still_publishes_post_call(self) -> None:
        """LLM 无 usage 时 post_call 照发（occupied/real 为 None）。"""
        agent = _create_agent(_plain_config())
        llm = AsyncMock()
        llm.generate.return_value = LLMResponse(content_blocks=[TextBlock(text="answer")])
        llm.configure_tools = MagicMock()
        agent._llm = llm
        publisher = MagicMock()
        publisher.publish = AsyncMock()
        agent._event_publisher = publisher

        await agent._action({})

        events = _usage_events(publisher)
        assert len(events) == 1
        post = events[0]
        assert post.occupied_tokens is None
        assert post.real_input_tokens is None


# ── request_compact 双态 ──


class TestRequestCompact:
    @pytest.mark.asyncio
    async def test_idle_executes_compact_directly(self) -> None:
        """空闲态：直接执行 compact，回执含 node_id。"""
        agent = _create_agent(_window_config())
        from ghrah.chat.factory import ChatMessageFactory

        agent._llm = AsyncMock()
        agent._llm.generate.return_value = LLMResponse(
            content_blocks=[TextBlock(text="summary text")]
        )
        agent._llm.configure_tools = MagicMock()
        cm = agent._context_manager
        factory = ChatMessageFactory()
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([factory.create_message("user", text=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])

        result = await agent.request_compact()

        assert result["executed"] is True
        assert "node_id" in result
        compact_nodes = [n for n in cm.get_history() if n.metadata.get("node_kind") == "compact"]
        assert len(compact_nodes) == 1
        assert compact_nodes[0].metadata["trigger_source"] == "manual"

    @pytest.mark.asyncio
    async def test_idle_empty_window_returns_reason(self) -> None:
        """空闲态窗外为空：回 empty_window 不产出节点。"""
        agent = _create_agent(_window_config())

        result = await agent.request_compact()

        assert result["executed"] is False
        assert result["reason"] == "empty_window"

    @pytest.mark.asyncio
    async def test_drive_active_sets_flag_and_schedules(self) -> None:
        """驱动中：置手动标志，回 scheduled；连续请求合并。"""
        agent = _create_agent(_window_config())
        cm = agent._context_manager

        agent._drive_active = True
        try:
            first = await agent.request_compact()
            assert first == {"scheduled": True, "merged": False}
            assert cm.compact_requested is True

            second = await agent.request_compact()
            assert second == {"scheduled": True, "merged": True}
        finally:
            agent._drive_active = False

    @pytest.mark.asyncio
    async def test_drive_active_flag_managed_by_receive(self) -> None:
        """receive 驱动期间标志置位、结束清除。"""
        agent = _create_agent(_window_config())
        agent._llm = _llm_with_usage(input_tokens=100)
        observed: list[bool] = []

        original_action = agent._action

        async def _spy_action(accumulated: dict[str, Any]) -> dict[str, Any]:
            observed.append(agent._drive_active)
            return await original_action(accumulated)

        agent._action = _spy_action  # type: ignore[method-assign]

        message = AgentMessage(sender="human", recipient="test-agent", content="hi")
        await agent.receive(message)

        assert observed and all(observed)  # 所有 action 执行于驱动期
        assert agent._drive_active is False  # 退出驱动后清除

    @pytest.mark.asyncio
    async def test_manual_flag_consumed_by_trigger_gate(self) -> None:
        """驱动中置的标志由下一轮触发门消费（端到端合并语义）。"""
        from ghrah.chat.factory import ChatMessageFactory

        agent = _create_agent(_window_config())
        agent._llm = AsyncMock()
        agent._llm.generate.return_value = LLMResponse(
            content_blocks=[TextBlock(text="summary text")]
        )
        agent._llm.configure_tools = MagicMock()
        cm = agent._context_manager
        factory = ChatMessageFactory()
        for i in range(3):
            cm.begin_iteration()
            cm.add_messages([factory.create_message("user", text=f"seed-{i}")])
            cm.commit_iteration(ability_names=[f"seed-{i}"])
        agent._iteration_state.max_iterations = 1
        agent._iteration_state.reset()

        cm.request_compact()  # 模拟驱动中命令置位
        await agent._drive_loop()

        compact_nodes = [n for n in cm.get_history() if n.metadata.get("node_kind") == "compact"]
        assert len(compact_nodes) == 1
        assert compact_nodes[0].metadata["trigger_source"] == "manual"
        assert cm.compact_requested is False  # 已消费
