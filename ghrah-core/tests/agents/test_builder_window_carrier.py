# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""窗口管理器恒建契约测试。

守护两条不变式：

1. **预算载体恒在**：未声明 ``config.window`` 的 Agent 也持有 WindowManager
   （否则 agentconf 解析出的 ``max_context_length`` 无处落定，
   ``budget_tokens`` 恒为 0，配置链在源头断开）。
2. **零策略不改写消息**：未声明窗口时的管理器只承载预算落定与观测，
   其 ``apply`` 必须原样返回消息，不得引入任何压缩副作用——这是
   「恒建」不构成行为变更的唯一保证。

另守护：显式声明窗口时行为与既往完全一致（策略、预算来源、compact 参数）。
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.agents.builder import AgentBuilder
from ghrah.chat.message import ChatMessage
from ghrah.context.window import WindowManager
from ghrah.types.config_types import AgentConfig, WindowConfig


def _agent(config: AgentConfig) -> Any:
    return AgentBuilder.from_config(config)


def _plain() -> AgentConfig:
    return AgentConfig(name="plain-agent")


def _declared() -> AgentConfig:
    return AgentConfig(
        name="declared-agent",
        window=WindowConfig(
            max_tokens=4096,
            strategies=["truncation"],
            compact_threshold=0.75,
            compact_keep_recent=3,
        ),
    )


def _wm(agent: Any) -> WindowManager:
    wm = agent._context_manager.window_manager
    assert wm is not None, "预算载体应恒存在"
    return wm


# ── 预算载体恒在 ──


class TestBudgetCarrierAlwaysPresent:
    def test_plain_agent_has_window_manager(self) -> None:
        """未声明 window 的 Agent 仍持有预算载体。"""
        _wm(_agent(_plain()))

    def test_plain_agent_budget_falls_back_to_default(self) -> None:
        """未声明窗口且无能力信息 → 内置默认预算（1M）与 default 来源。"""
        wm = _wm(_agent(_plain()))
        assert wm.max_tokens == 1_000_000
        assert wm.max_tokens_source == "default"

    def test_plain_agent_has_no_compact_config(self) -> None:
        """未声明窗口 → 无 compact 阈值，行为与既往管道等价。"""
        agent = _agent(_plain())
        status = agent._context_manager.get_window_status()
        assert status["compact_threshold"] is None
        assert agent._context_manager.evaluate_compaction_decision() is None

    def test_declared_agent_budget_unchanged(self) -> None:
        """显式声明窗口：预算来源仍为 declared，参数原样透传。"""
        agent = _agent(_declared())
        wm = _wm(agent)
        assert wm.max_tokens == 4096
        assert wm.max_tokens_source == "declared"
        status = agent._context_manager.get_window_status()
        assert status["compact_threshold"] == 0.75
        assert status["compact_keep_recent"] == 3

    def test_agentconf_capabilities_land_on_plain_agent(self) -> None:
        """未声明窗口的 Agent：能力信息仍能落定预算（agentconf 来源）。

        这是「恒建」要解决的问题本身——此前 WindowManager 为 None，
        ``resolve_budget_from_capabilities`` 无处调用，窗口被静默丢弃。
        """
        wm = _wm(_agent(_plain()))
        assert wm.resolve_budget_from_capabilities({"max_context_length": 200_000}) is True
        assert wm.max_tokens == 200_000
        assert wm.max_tokens_source == "agentconf"


# ── 零策略不改写消息 ──


class TestZeroStrategyDoesNotRewriteMessages:
    def test_plain_agent_has_zero_strategies(self) -> None:
        """未声明窗口 → 零策略（不得默认装 tool_call_fold/truncation）。"""
        wm = _wm(_agent(_plain()))
        assert wm.strategies == []

    def test_declared_agent_keeps_declared_strategies(self) -> None:
        """显式声明窗口 → 策略按声明装配，不被「零策略」规则波及。"""
        wm = _wm(_agent(_declared()))
        assert [s.name for s in wm.strategies] == ["truncation"]

    @pytest.mark.asyncio
    async def test_apply_returns_messages_unchanged(self) -> None:
        """零策略 apply 原样返回消息：条数、顺序、内容均不变。"""
        wm = _wm(_agent(_plain()))
        messages = [
            ChatMessage.system("sys"),
            ChatMessage.user(text_or_blocks="hello"),
            ChatMessage.user(text_or_blocks="world"),
        ]

        result = await wm.apply(list(messages), max_tokens=10)  # 预算故意极小

        assert len(result) == len(messages)
        assert [m.text for m in result] == [m.text for m in messages]
        assert [m.role for m in result] == [m.role for m in messages]

    @pytest.mark.asyncio
    async def test_apply_survives_exceeding_budget(self) -> None:
        """即便远超预算也不截断——预算观测不等于消息压缩。"""
        wm = _wm(_agent(_plain()))
        long_text = "x" * 100_000
        messages = [ChatMessage.user(text_or_blocks=long_text)]

        result = await wm.apply(list(messages), max_tokens=1)

        assert len(result) == 1
        assert result[0].text == long_text
