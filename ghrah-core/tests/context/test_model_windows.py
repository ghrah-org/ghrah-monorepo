# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型窗口表与预算来源解析测试。"""

from __future__ import annotations

import pytest

from ghrah.context.model_windows import lookup_model_window
from ghrah.context.window import (
    WindowManager,
    parse_vendor_window_tokens,
)
from ghrah.types.config_types import DEFAULT_WINDOW_MAX_TOKENS


class TestLookupModelWindow:
    def test_exact_family_prefix(self) -> None:
        assert lookup_model_window("claude-sonnet-4-20250514") == 200_000
        assert lookup_model_window("gpt-4o") == 128_000
        assert lookup_model_window("deepseek-chat") == 128_000

    def test_longest_prefix_wins(self) -> None:
        # gpt-4 系短前缀不应吃掉 gpt-4o/gpt-4.1 的长前缀
        assert lookup_model_window("gpt-4o-mini-2024-07-18") == 128_000
        assert lookup_model_window("gpt-4.1-2025-04-14") == 1_047_576
        assert lookup_model_window("gpt-4-turbo") == 128_000
        # claude-3-5-haiku 优于 claude-3-5 与 claude
        assert lookup_model_window("claude-3-5-haiku-20241022") == 200_000

    def test_case_insensitive_and_whitespace(self) -> None:
        assert lookup_model_window("  Claude-Sonnet-4  ") == 200_000
        assert lookup_model_window("DeepSeek-Reasoner") == 128_000

    def test_unknown_model_returns_none(self) -> None:
        assert lookup_model_window("some-unknown-model") is None
        assert lookup_model_window("") is None
        assert lookup_model_window("   ") is None


class TestWindowManagerBudgetSource:
    def test_agentconf_replaces_legacy_table(self) -> None:
        wm = WindowManager()
        wm.resolve_budget_from_model("gpt-4o")
        assert wm.resolve_budget_from_capabilities({"max_context_length": 256000}) is True
        assert wm.max_tokens == 256000
        assert wm.max_tokens_source == "agentconf"
        assert wm.resolve_budget_from_model("claude-sonnet-4") is False

    @pytest.mark.parametrize("window", [None, 0, -1, True, "128000"])
    def test_invalid_capability_window_keeps_default(self, window: object) -> None:
        wm = WindowManager()
        assert wm.resolve_budget_from_capabilities({"max_context_length": window}) is False
        assert wm.max_tokens == DEFAULT_WINDOW_MAX_TOKENS

    def test_agentconf_does_not_override_declared_or_vendor(self) -> None:
        wm = WindowManager(max_tokens=4096)
        assert wm.resolve_budget_from_capabilities({"max_context_length": 128000}) is False
        assert wm.max_tokens == 4096
        wm.correct_budget_from_vendor(32768)
        assert wm.resolve_budget_from_capabilities({"max_context_length": 128000}) is False
        assert wm.max_tokens == 32768

    def test_declared_budget(self) -> None:
        wm = WindowManager(max_tokens=8192)
        assert wm.max_tokens == 8192
        assert wm.max_tokens_source == "declared"

    def test_undeclared_falls_back_to_default(self) -> None:
        wm = WindowManager()
        assert wm.max_tokens == DEFAULT_WINDOW_MAX_TOKENS == 1_000_000
        assert wm.max_tokens_source == "default"

    def test_vendor_correction_overrides_default(self) -> None:
        wm = WindowManager()
        assert wm.correct_budget_from_vendor(128_000) is True
        assert wm.max_tokens == 128_000
        assert wm.max_tokens_source == "vendor"

    def test_resolve_from_model_table(self) -> None:
        wm = WindowManager()
        assert wm.resolve_budget_from_model("claude-sonnet-4-20250514") is True
        assert wm.max_tokens == 200_000
        assert wm.max_tokens_source == "model_table"

    def test_resolve_unknown_model_keeps_default(self) -> None:
        wm = WindowManager()
        assert wm.resolve_budget_from_model("mystery-model") is False
        assert wm.max_tokens == DEFAULT_WINDOW_MAX_TOKENS
        assert wm.max_tokens_source == "default"

    def test_resolve_idempotent_after_table_hit(self) -> None:
        wm = WindowManager()
        wm.resolve_budget_from_model("gpt-4o")
        # 已落定后再次调用不动
        assert wm.resolve_budget_from_model("gpt-4.1") is False
        assert wm.max_tokens == 128_000
        assert wm.max_tokens_source == "model_table"

    def test_declared_not_overridden_by_table(self) -> None:
        wm = WindowManager(max_tokens=1000)
        assert wm.resolve_budget_from_model("gpt-4o") is False
        assert wm.max_tokens == 1000
        assert wm.max_tokens_source == "declared"

    def test_vendor_correction_overrides_declared(self) -> None:
        wm = WindowManager(max_tokens=1000)
        assert wm.correct_budget_from_vendor(65_536) is True
        assert wm.max_tokens == 65_536
        assert wm.max_tokens_source == "vendor"

    def test_vendor_correction_rejects_non_positive(self) -> None:
        wm = WindowManager()
        assert wm.correct_budget_from_vendor(0) is False
        assert wm.correct_budget_from_vendor(-100) is False
        assert wm.max_tokens_source == "default"


class TestParseVendorWindowTokens:
    def test_openai_style(self) -> None:
        msg = (
            "Error code: 400 - This model's maximum context length is 128000 tokens. "
            "However, you requested 130000 tokens."
        )
        assert parse_vendor_window_tokens(msg) == 128000

    def test_anthropic_style_takes_maximum(self) -> None:
        msg = "Error code: 400 - prompt is too long: 200000 tokens > 190000 maximum"
        assert parse_vendor_window_tokens(msg) == 190000

    def test_context_window_of(self) -> None:
        assert parse_vendor_window_tokens("input exceeds the context window of 200000") == 200000

    def test_context_limit_with_thousands_separator(self) -> None:
        assert parse_vendor_window_tokens("exceed the context limit (131,072)") == 131_072

    def test_gemini_style(self) -> None:
        msg = "exceeds the maximum number of tokens allowed (1048576)"
        assert parse_vendor_window_tokens(msg) == 1_048_576

    def test_no_match_returns_none(self) -> None:
        assert parse_vendor_window_tokens("some unrelated error") is None
        assert parse_vendor_window_tokens("") is None
