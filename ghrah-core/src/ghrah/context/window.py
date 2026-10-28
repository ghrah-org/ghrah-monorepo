# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""WindowManager：LLM 上下文窗口管理器。

提供策略模式的窗口压缩框架，支持多种压缩策略的组合管道式执行。

核心组件：
- WindowStrategy：策略接口，定义 apply() 方法
- WindowManager：策略组合器，按顺序应用多个策略
- estimate_tokens / estimate_message_tokens：token 估算工具函数

设计要点：
- 策略按注册顺序依次应用，形成处理管道
- 系统消息（role="system"）始终被保护，不参与截断/折叠
- token 估算委托 ghrah.context.token_estimator 的默认加权估算器
  （CJK 1 token/char + 非 CJK chars/4 + reasoning 计入 + image 固定值，
  零外部依赖）
- 所有消息类型通过 WindowableMessage / WindowableBlock Protocol 解耦，
  不依赖 ChatMessage 等具体类型
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from ghrah.context.token_estimator import DEFAULT_TOKEN_ESTIMATOR, TokenEstimator
from ghrah.core.window_protocol import (
    MessageFactory,
    WindowableMessage,
)
from ghrah.types.config_types import DEFAULT_WINDOW_MAX_TOKENS

logger = logging.getLogger(__name__)

__all__ = [
    "WindowStrategy",
    "WindowManager",
    "estimate_tokens",
    "estimate_message_tokens",
    "parse_vendor_window_tokens",
]


def estimate_message_tokens(message: Any) -> int:
    """估算单条消息的 token 数（委托默认加权估算器）。

    加权口径见 ghrah.context.token_estimator：CJK 1 token/char、
    非 CJK chars/4、reasoning block 按正文长度计、image/file 固定值。
    旧调用面（策略/compact/recall）经本函数自然继承新口径。

    Args:
        message: WindowableMessage 或兼容对象

    Returns:
        估算的 token 数
    """
    return DEFAULT_TOKEN_ESTIMATOR.estimate_message(message)


def estimate_tokens(messages: list[Any]) -> int:
    """估算消息列表的总 token 数（委托默认加权估算器）。

    Args:
        messages: 消息列表

    Returns:
        总 token 数估算值
    """
    return DEFAULT_TOKEN_ESTIMATOR.estimate_messages(messages)


class WindowStrategy(ABC):
    """LLM 上下文窗口压缩策略接口。

    每个策略接收消息列表和 token 预算，返回压缩后的消息列表。
    策略不应修改原始消息列表（返回新列表）。

    系统消息保护约定：
    - role="system" 的消息始终保留，不参与截断/折叠
    - 具体策略负责在 apply() 中分离和保护系统消息

    预算短路约定：
    - skip_when_under_budget=True 的策略在总 token 未超预算时会被
      WindowManager 跳过（适用于"仅在超预算时生效"的策略，且其 apply()
      内应保有同条件的预算自检以保证语义等价）
    - 默认 False：策略无条件执行（如 ToolCallFoldStrategy 属于策略性
      折叠，预算内也生效）
    """

    skip_when_under_budget: bool = False

    @abstractmethod
    async def apply(
        self, messages: list[WindowableMessage], token_budget: int
    ) -> list[WindowableMessage]:
        """应用压缩策略。

        Args:
            messages: 输入消息列表
            token_budget: 剩余 token 预算

        Returns:
            压缩后的消息列表（新列表，不修改原列表）
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称，用于日志和调试。"""
        ...


def _split_system_messages(
    messages: list[WindowableMessage],
) -> tuple[list[WindowableMessage], list[WindowableMessage]]:
    """将消息列表分为系统消息和非系统消息。

    Args:
        messages: 输入消息列表

    Returns:
        (system_messages, other_messages) 元组
    """
    system_msgs: list[WindowableMessage] = []
    other_msgs: list[WindowableMessage] = []
    for msg in messages:
        if msg.role == "system":
            system_msgs.append(msg)
        else:
            other_msgs.append(msg)
    return system_msgs, other_msgs


class WindowManager:
    """LLM 上下文窗口管理器 — 组合多个 WindowStrategy。

    管道式执行：策略按注册顺序依次应用，每个策略接收上一个策略的输出。

    推荐的默认执行顺序：
    1. ToolCallFoldStrategy — 折叠冗长的工具返回（减少单条消息体积）
    2. LLMSummaryStrategy   — 总结旧消息（用摘要替代历史）
    3. SlidingWindowStrategy — 确保总消息数在窗口内
    4. TruncationStrategy    — 最终兜底，暴力截断

    预算优先使用显式声明（declared），其次为 agentconf 解析结果（agentconf）。
    未接入能力数据的客户端才回落模型窗口表（model_table）和内置默认（default）。
    ``max_tokens=None`` 表示未声明，首线 LLM 就绪后优先经
    :meth:`resolve_budget_from_capabilities` 落定；厂商超限错误的运行时
    回填经 :meth:`correct_budget_from_vendor`，口径以厂商为真值。

    Args:
        strategies: 策略列表（按执行顺序）
        max_tokens: token 预算；None 表示未声明（待查表/回落默认）
        message_factory: 消息构造工厂（用于需要构造新消息的策略）
        estimator: token 估算器（预算短路与 estimate_tokens 出口口径）；
            None 时用模块级默认实例 DEFAULT_TOKEN_ESTIMATOR
    """

    def __init__(
        self,
        strategies: list[WindowStrategy] | None = None,
        max_tokens: int | None = None,
        message_factory: MessageFactory | None = None,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self._strategies: list[WindowStrategy] = list(strategies) if strategies else []
        self._message_factory = message_factory
        self._estimator: TokenEstimator = estimator or DEFAULT_TOKEN_ESTIMATOR
        if max_tokens is not None:
            self._max_tokens = max_tokens
            self._max_tokens_source = "declared"
        else:
            self._max_tokens = DEFAULT_WINDOW_MAX_TOKENS
            self._max_tokens_source = "default"

    @property
    def estimator(self) -> TokenEstimator:
        """token 估算器实例（预算短路与 estimate 出口的统一口径）。"""
        return self._estimator

    @property
    def max_tokens(self) -> int:
        """token 预算（声明值或解析后的落定值）。"""
        return self._max_tokens

    @property
    def max_tokens_source(self) -> str:
        """预算来源：declared / agentconf / model_table / default / vendor。"""
        return self._max_tokens_source

    def resolve_budget_from_capabilities(self, capabilities: dict[str, Any]) -> bool:
        """优先消费配置层解析的窗口；显式声明和厂商回填不受影响。

        配置层会完成外部参考数据、模型覆盖和默认值的合并。字段显式为
        None 或非法时保留内置默认，不再通过模型名前缀猜测覆盖它。
        """
        if self._max_tokens_source not in ("default", "model_table"):
            return False
        window = capabilities.get("max_context_length")
        if type(window) is not int or window <= 0:
            return False
        self._max_tokens = window
        self._max_tokens_source = "agentconf"
        logger.info("WindowManager: budget resolved from agentconf — budget=%d", window)
        return True

    def resolve_budget_from_model(self, model_name: str) -> bool:
        """未声明预算时按模型名查内置窗口表落定。

        幂等：已声明或已落定（含 vendor 回填）时不动。查表命中与否均
        打日志（零隐式：来源可观测）。

        Args:
            model_name: 模型标识（如 "claude-sonnet-4-20250514"）

        Returns:
            是否发生了预算落定（即本次调用改变了预算）
        """
        from ghrah.context.model_windows import lookup_model_window

        if self._max_tokens_source != "default":
            return False
        window = lookup_model_window(model_name)
        if window is None:
            logger.info(
                "WindowManager: model %r not in window table; budget stays %d (default)",
                model_name,
                self._max_tokens,
            )
            return False
        self._max_tokens = window
        self._max_tokens_source = "model_table"
        logger.info(
            "WindowManager: budget resolved from model table — model=%r budget=%d",
            model_name,
            window,
        )
        return True

    def correct_budget_from_vendor(self, window_tokens: int) -> bool:
        """厂商超限错误解析出的真实窗口回填。

        厂商口径为真值：无论当前来源为何（含 declared）均覆盖，并记
        prominent 日志。正值校验，非正值拒绝。

        Args:
            window_tokens: 厂商报文中的上下文窗口大小

        Returns:
            是否接受了回填
        """
        if window_tokens <= 0:
            return False
        previous = (self._max_tokens, self._max_tokens_source)
        self._max_tokens = window_tokens
        self._max_tokens_source = "vendor"
        logger.warning(
            "WindowManager: budget corrected from vendor context-limit error — "
            "budget=%d (was %d/%s)",
            window_tokens,
            previous[0],
            previous[1],
        )
        return True

    @property
    def strategies(self) -> list[WindowStrategy]:
        """已注册的策略列表（副本）。"""
        return list(self._strategies)

    @property
    def message_factory(self) -> MessageFactory | None:
        """消息构造工厂。"""
        return self._message_factory

    def add_strategy(self, strategy: WindowStrategy) -> None:
        """添加策略到管道末尾。

        Args:
            strategy: 要添加的策略
        """
        self._strategies.append(strategy)

    async def apply(
        self, messages: list[WindowableMessage], max_tokens: int | None = None
    ) -> list[WindowableMessage]:
        """按顺序应用所有策略。

        预算短路：先估算总 token，未超预算时跳过 skip_when_under_budget=True
        的策略，其余策略照常执行。

        Args:
            messages: 输入消息列表
            max_tokens: 覆盖默认 max_tokens

        Returns:
            压缩后的消息列表
        """
        budget = max_tokens if max_tokens is not None else self._max_tokens

        if self._estimator.estimate_messages(messages) <= budget:
            strategies = [s for s in self._strategies if not s.skip_when_under_budget]
        else:
            strategies = self._strategies

        result = list(messages)  # 浅拷贝

        for strategy in strategies:
            result = await strategy.apply(result, budget)

        return result

    def drain_compaction_records(self) -> list[dict[str, Any]]:
        """收集并清空各策略产生的 compaction 事件记录。

        对策略 duck-typing 调用 drain_compaction_record()（如
        LLMSummaryStrategy），收集非 None 返回值。

        Returns:
            compaction 事件记录列表
        """
        records: list[dict[str, Any]] = []
        for strategy in self._strategies:
            drain = getattr(strategy, "drain_compaction_record", None)
            if callable(drain):
                record = drain()
                if record is not None:
                    records.append(record)
        return records

    def estimate_tokens(self, messages: list[WindowableMessage]) -> int:
        """估算消息列表的总 token 数（经注入的估算器口径）。

        Args:
            messages: 消息列表

        Returns:
            估算的 token 数
        """
        return self._estimator.estimate_messages(messages)


# 厂商超限报文中的窗口数字解析模式 — 只在 _is_context_limit_error 命中后
# 尝试（白名单前置），模式本身保守：数字必须紧邻窗口上限语义短语。
# 多捕获组时取最后一组（上限值，而非当前请求 token 数）。
_VENDOR_WINDOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI: "maximum context length is 4096 tokens. However, you requested ..."
    re.compile(r"maximum context length is ([\d,]+)", re.IGNORECASE),
    # Anthropic: "prompt is too long: 200000 tokens > 190000 maximum"
    re.compile(r"prompt is too long: ([\d,]+) tokens > ([\d,]+) maximum", re.IGNORECASE),
    # Gemini: "exceeds the maximum number of tokens allowed (1048576)"
    re.compile(r"maximum number of tokens allowed \(?([\d,]+)\)?", re.IGNORECASE),
    # 通用: "exceeds the context window of 200000 tokens"
    re.compile(r"context window of ([\d,]+)", re.IGNORECASE),
    # DeepSeek: "exceed the context limit (131072)" / "context limit 131072"
    re.compile(r"context limit \(?([\d,]+)\)?", re.IGNORECASE),
)


def parse_vendor_window_tokens(message: str) -> int | None:
    """从厂商上下文超限错误报文中保守解析真实窗口大小。

    仅解析**窗口上限**语义的数字（多捕获组模式取最后一组，即上限，
    而非当前请求 token 数）。调用方应先以 ``_is_context_limit_error``
    类别白名单确认错误类别，解析失败返回 None（回落现有减半阶梯）。

    Args:
        message: 厂商错误报文（异常字符串）

    Returns:
        窗口 token 数；无匹配返回 None
    """
    for pattern in _VENDOR_WINDOW_PATTERNS:
        match = pattern.search(message)
        if match is None:
            continue
        groups = [g for g in match.groups() if g is not None]
        if not groups:
            continue
        value = int(groups[-1].replace(",", ""))
        if value > 0:
            return value
    return None
