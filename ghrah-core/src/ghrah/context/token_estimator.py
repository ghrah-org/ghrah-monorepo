# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""加权 token 估算器 — 修正 reasoning/CJK/image 三处系统性失真。

009 事故档案（plans/issue/2026-09-14-compaction-introspection-unreliable.md）
外证坐实的旧口径失真：

- reasoning block 每块只计 1 token（172k chars → 1，低估三个数量级）；
- CJK 文本按 chars/4 计（中文实际 ≈1 token/char，低估 3-6 倍）；
- image/file 按基础串长度 chars/4 计（1MB 截图估 25 万 token，真实
  ≈1600，高估两个数量级，反向触发无谓压缩）。

修正口径（保守近似，零外部 tokenizer 依赖）：

- 文本类内容：CJK 字符 1 token/char，非 CJK 字符 4 chars/token；
- image/file 块：固定常量（Anthropic 现代模型口径近似）；
- 未知块：维持每块 1 token 的现状下限。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

__all__ = [
    "TokenEstimator",
    "WeightedTokenEstimator",
    "DEFAULT_TOKEN_ESTIMATOR",
    "effective_length",
]

# CJK 区段上界（保守宽区间：覆盖 CJK 统一表意、扩展 A/B、注音、假名等）
_CJK_MAX = 0x9FFF

# image/file 块的固定 token 估算值（Anthropic 现代模型口径近似）
_IMAGE_FILE_BLOCK_TOKENS = 1600

# 非 CJK 字符的 chars/token 比
_CHARS_PER_TOKEN = 4


def effective_length(text: str) -> int:
    """按加权口径计算文本的等效字符长度（即 token 数）。

    CJK 字符（U+0000_2E80..U+0000_9FFF）计 1 token/char，非 CJK 字符按
    4 chars/token 折算。O(n) 单遍扫描。

    Args:
        text: 待估算文本

    Returns:
        加权 token 数（至少 0；空串为 0，调用方自行取下限）
    """
    cjk = 0
    other = 0
    for ch in text:
        code = ord(ch)
        if 0x2E80 <= code <= _CJK_MAX:
            cjk += 1
        else:
            other += 1
    return cjk + other // _CHARS_PER_TOKEN


class TokenEstimator(Protocol):
    """token 估算器协议 — 窗口管道/compact/recall 的可插拔口径。

    实现方须知：
    - ``estimate_message`` 对空消息返回值应 >=1（块级/消息级下限由实现
      自定，但不得为 0，与历史行为一致）；
    - ``chars_budget_for`` 是 tokens→chars 的逆运算上界起点，用于"按
      token 余量截断字符"的场景；返回值应保守偏小（宁可多截一轮也不
      超预算），调用方以 estimate 复评兜底。
    """

    def estimate_message(self, message: Any) -> int:
        """估算单条消息的 token 数。

        Args:
            message: WindowableMessage 或兼容对象

        Returns:
            估算的 token 数
        """
        ...

    def estimate_messages(self, messages: list[Any]) -> int:
        """估算消息列表的总 token 数。

        Args:
            messages: 消息列表

        Returns:
            总 token 数估算值
        """
        ...

    def chars_budget_for(self, tokens: int) -> int:
        """把 token 预算换算为保守的字符截断上界。

        Args:
            tokens: token 预算余量

        Returns:
            字符数上界（保守：保证按此截断不超预算）
        """
        ...


class WeightedTokenEstimator:
    """加权 token 估算器 — reasoning/CJK/image 三处失真的修正实现。

    块级分派口径：
    - ``text``: effective_length(text)
    - ``reasoning``: effective_length(reasoning)（修正：不再每块 1 token）
    - ``tool_call``: effective_length(arguments JSON) + effective_length(name)
    - ``tool_result``: effective_length(content)
    - ``audio``: effective_length(data)
    - ``image`` / ``file``: 固定常量 ``_IMAGE_FILE_BLOCK_TOKENS``
      （修正：不再按基础串长度计，消除两个数量级高估）
    - 其他块类型: 1（维持现状下限）

    降级路径与历史行为一致：无 ``content_blocks``/``role`` 属性的对象
    回退 text/content 属性后按文本加权估算。

    ``chars_budget_for`` 取 CJK 密集下界（tokens ≈ chars）：按该上界截断
    在纯 ASCII 场景可能一次截得太短，由调用方的 estimate 复评循环补齐；
    宁可保守也不超预算。
    """

    def estimate_message(self, message: Any) -> int:
        """估算单条消息的 token 数（加权口径）。

        Args:
            message: WindowableMessage 或兼容对象

        Returns:
            估算的 token 数（至少 1）
        """
        if hasattr(message, "content_blocks") and hasattr(message, "role"):
            total = 0
            for block in message.content_blocks:
                total += self._estimate_block(block)
            return max(1, total)

        # 降级处理：尝试获取 text 属性
        content = getattr(message, "text", None)
        if content is not None:
            return max(1, effective_length(str(content)))
        content = getattr(message, "content", str(message))
        return max(1, effective_length(str(content)))

    def estimate_messages(self, messages: list[Any]) -> int:
        """估算消息列表的总 token 数。

        Args:
            messages: 消息列表

        Returns:
            总 token 数估算值
        """
        return sum(self.estimate_message(m) for m in messages)

    def chars_budget_for(self, tokens: int) -> int:
        """把 token 余量换算为保守的字符截断上界。

        加权口径下 1 token 至少对应 1 个字符（CJK 密集场景），至多对应
        4 个字符（纯 ASCII）。取下界（tokens）保证不超预算；截得太短由
        调用方按 estimate 复评收紧循环自然收敛。

        Args:
            tokens: token 预算余量

        Returns:
            字符数上界（负值截为 0）
        """
        return max(tokens, 0)

    def _estimate_block(self, block: Any) -> int:
        """按块类型分派估算（duck typing，与旧实现同风格）。

        Args:
            block: WindowableBlock 或兼容对象

        Returns:
            该块的 token 数（至少 1）
        """
        match getattr(block, "type", None):
            case "text":
                return max(1, effective_length(getattr(block, "text", "")))
            case "reasoning":
                return max(1, effective_length(getattr(block, "reasoning", "")))
            case "tool_call":
                args = getattr(block, "arguments", None)
                args_str = json.dumps(args, ensure_ascii=False) if args else ""
                total = max(1, effective_length(args_str))
                return total + max(1, effective_length(getattr(block, "name", "")))
            case "tool_result":
                return max(1, effective_length(getattr(block, "content", "")))
            case "image" | "file":
                return _IMAGE_FILE_BLOCK_TOKENS
            case "video":
                # 视频不直接进模型：按抽帧数计，避免落 case _ 严重低估。
                frames = getattr(block, "frames", None) or []
                return max(1, len(frames)) * _IMAGE_FILE_BLOCK_TOKENS
            case "audio":
                return max(1, effective_length(getattr(block, "data", "")))
            case _:
                return 1


#: 默认估算器实例 — 模块级估算函数与其全部调用方的统一口径
DEFAULT_TOKEN_ESTIMATOR = WeightedTokenEstimator()
