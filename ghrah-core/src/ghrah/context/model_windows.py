# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型上下文窗口表 — 预算来源解析的查表面（纯常量，零内部依赖）。

仅在运营者未显式声明 ``WindowConfig.max_tokens`` 且客户端未携带
agentconf 解析后的能力信息时作为兼容预算来源；
显式声明恒优先。表按**最长前缀匹配**（大小写不敏感）查找，未命中回落
``DEFAULT_WINDOW_MAX_TOKENS``。表是静态快照，厂商新模型未收录属预期——
运行时厂商超限错误回填是兜底真值通道。
"""

from __future__ import annotations

# (前缀, 窗口 token 数) — 前缀按模型家族粗粒度收录，宁粗勿错：
# 窗口只作默认预算，偏大触发的后果由紧急压缩阶梯兜底。
_MODEL_WINDOW_PREFIXES: tuple[tuple[str, int], ...] = (
    # Anthropic Claude
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-3-7", 200_000),
    ("claude-3-5-haiku", 200_000),
    ("claude-3-5", 200_000),
    ("claude-3", 200_000),
    ("claude", 200_000),
    # OpenAI GPT
    ("gpt-5", 400_000),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o-mini", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("gpt-4", 8_192),
    ("gpt-3.5", 16_385),
    ("o3-mini", 200_000),
    ("o3", 200_000),
    ("o4-mini", 200_000),
    ("o1", 200_000),
    # DeepSeek
    ("deepseek-reasoner", 128_000),
    ("deepseek-chat", 128_000),
    ("deepseek", 128_000),
    # Google Gemini
    ("gemini-2", 1_048_576),
    ("gemini-1.5-pro", 2_097_152),
    ("gemini-1.5-flash", 1_048_576),
    ("gemini", 1_048_576),
    # Alibaba Qwen
    ("qwen3", 131_072),
    ("qwen2.5", 131_072),
    ("qwen", 131_072),
    # Meta Llama（hosted 常见口径）
    ("llama-3", 128_000),
    ("llama", 128_000),
    # Mistral
    ("mistral-large", 128_000),
    ("mistral", 32_000),
)


def lookup_model_window(model_name: str) -> int | None:
    """按最长前缀匹配查模型上下文窗口大小。

    Args:
        model_name: 模型标识（大小写不敏感，去除首尾空白）

    Returns:
        窗口 token 数；未收录返回 None
    """
    normalized = model_name.strip().lower()
    if not normalized:
        return None
    best: int | None = None
    best_len = -1
    for prefix, window in _MODEL_WINDOW_PREFIXES:
        if normalized.startswith(prefix) and len(prefix) > best_len:
            best = window
            best_len = len(prefix)
    return best


__all__ = ["lookup_model_window"]
