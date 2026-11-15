# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""加权 token 估算器测试（009 修正口径）。

覆盖：
- effective_length 纯函数：纯 ASCII / 纯 CJK / 混合
- reasoning block 计入正文长度（不再每块 1 token）
- image/file 固定常量（不再按基础串长度计）
- CJK 消息按 1 token/char 加权
- 模块级 estimate_tokens / estimate_message_tokens 委托默认实例
- 降级路径行为不变
"""

from __future__ import annotations

from ghrah.chat.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    ReasoningBlock,
    ToolCallBlock,
    VideoBlock,
)
from ghrah.chat.message import ChatMessage
from ghrah.context.token_estimator import (
    DEFAULT_TOKEN_ESTIMATOR,
    WeightedTokenEstimator,
    effective_length,
)
from ghrah.context.window import estimate_message_tokens, estimate_tokens

# ----------------------------------------------------------------
# TestEffectiveLength
# ----------------------------------------------------------------


class TestEffectiveLength:
    """effective_length 纯函数测试。"""

    def test_empty_string(self) -> None:
        """空串为 0（消息级下限由调用方保证）。"""
        assert effective_length("") == 0

    def test_pure_ascii(self) -> None:
        """纯 ASCII 按 4 chars/token。"""
        assert effective_length("a" * 100) == 25

    def test_ascii_not_multiple_of_four(self) -> None:
        """非 4 倍数 ASCII 向下取整。"""
        assert effective_length("abc") == 0
        assert effective_length("abcde") == 1

    def test_pure_cjk(self) -> None:
        """纯 CJK 按 1 token/char。"""
        assert effective_length("中" * 100) == 100

    def test_mixed_cjk_ascii(self) -> None:
        """混合文本：CJK 逐字 + ASCII 折算。"""
        assert effective_length("a" * 60 + "中" * 40) == 15 + 40

    def test_cjk_punctuation_range(self) -> None:
        """CJK 区段边界（0x2E80 进、0x9FFF 进、界外不计）。"""
        assert effective_length("\u2e80") == 1  # 区段首（CJK 部首补充）
        assert effective_length("\u4e2d") == 1  # 常用汉字
        assert effective_length("\u9fff") == 1  # 区段尾


# ----------------------------------------------------------------
# TestBlockEstimation
# ----------------------------------------------------------------


class TestBlockEstimation:
    """块级加权估算测试。"""

    def test_reasoning_block_counted_by_length(self) -> None:
        """reasoning block 按正文长度计，不再每块 1 token。"""
        msg = ChatMessage.ai(content_blocks=[ReasoningBlock(reasoning="t" * 20000)])
        assert estimate_message_tokens(msg) >= 5000

    def test_reasoning_block_cjk(self) -> None:
        """CJK reasoning 按 1 token/char。"""
        msg = ChatMessage.ai(content_blocks=[ReasoningBlock(reasoning="思" * 400)])
        assert estimate_message_tokens(msg) >= 400

    def test_image_block_fixed_constant(self) -> None:
        """image block 固定常量，不受 base64 长度影响。"""
        small = ChatMessage.user(text_or_blocks=[ImageBlock(base64="x" * 100)])
        huge = ChatMessage.user(text_or_blocks=[ImageBlock(base64="x" * 1_400_000)])
        assert estimate_message_tokens(small) == estimate_message_tokens(huge)
        assert estimate_message_tokens(huge) <= 2000

    def test_file_block_fixed_constant(self) -> None:
        """file block 与 image 同口径。"""
        msg = ChatMessage.user(text_or_blocks=[FileBlock(base64="x" * 800_000, filename="a.pdf")])
        assert estimate_message_tokens(msg) <= 2000

    def test_text_block_ascii_unchanged(self) -> None:
        """纯 ASCII text 维持 chars//4 口径。"""
        msg = ChatMessage.user(text_or_blocks="a" * 100)
        assert estimate_message_tokens(msg) == 25

    def test_text_block_cjk_weighted(self) -> None:
        """CJK text 按 1 token/char（原 100 → 现 400）。"""
        msg = ChatMessage.user(text_or_blocks="中" * 400)
        assert estimate_message_tokens(msg) == 400

    def test_tool_result_cjk_weighted(self) -> None:
        """tool_result content 的 CJK 加权。"""
        msg = ChatMessage.tool(tool_call_id="c1", content="结果" * 200, name="search")
        assert estimate_message_tokens(msg) >= 400

    def test_audio_block_weighted(self) -> None:
        """audio data 按文本加权。"""
        msg = ChatMessage.user(text_or_blocks=[AudioBlock(data="d" * 80)])
        assert estimate_message_tokens(msg) == 20

    def test_tool_call_arguments_cjk(self) -> None:
        """tool_call 参数 JSON 的 CJK 加权。"""
        msg = ChatMessage.ai(
            content_blocks=[
                ToolCallBlock(id="tc1", name="search", arguments={"query": "查" * 100}),
            ]
        )
        tokens = estimate_message_tokens(msg)
        assert tokens >= 100

    def test_unknown_block_floor(self) -> None:
        """未知块类型维持每块 1 token 下限。"""

        class Odd:
            type = "odd_block"

        msg = ChatMessage.ai(content_blocks=[Odd(), Odd()])
        assert estimate_message_tokens(msg) == 2


# ----------------------------------------------------------------
# TestMessageListAndFallback
# ----------------------------------------------------------------


class TestMessageListAndFallback:
    """列表累计与降级路径测试。"""

    def test_estimate_empty_list(self) -> None:
        """空列表为 0。"""
        assert estimate_tokens([]) == 0

    def test_list_accumulates(self) -> None:
        """多消息累加。"""
        messages = [
            ChatMessage.user(text_or_blocks="a" * 40),
            ChatMessage.user(text_or_blocks="中" * 20),
        ]
        assert estimate_tokens(messages) == 10 + 20

    def test_empty_message_floor(self) -> None:
        """空内容消息至少 1 token。"""
        msg = ChatMessage.user(text_or_blocks="")
        assert estimate_message_tokens(msg) >= 1

    def test_plain_object_fallback_weighted(self) -> None:
        """降级路径（无 content_blocks）走 text 属性加权。"""

        class SimpleObj:
            text = "hello world"

        assert estimate_message_tokens(SimpleObj()) == 2  # 11 chars // 4

    def test_plain_object_content_fallback(self) -> None:
        """降级路径（无 text）走 str(message) 加权。"""

        class SimpleObj:
            def __str__(self) -> str:
                return "abcdefgh"

        assert estimate_message_tokens(SimpleObj()) == 2

    def test_module_functions_delegate_default(self) -> None:
        """模块级函数与默认实例口径一致。"""
        msg = ChatMessage.user(text_or_blocks="中" * 100 + "a" * 100)
        assert estimate_message_tokens(msg) == DEFAULT_TOKEN_ESTIMATOR.estimate_message(msg)
        assert estimate_tokens([msg]) == DEFAULT_TOKEN_ESTIMATOR.estimate_messages([msg])


# ----------------------------------------------------------------
# TestCharsBudgetFor
# ----------------------------------------------------------------


class TestCharsBudgetFor:
    """tokens→chars 逆运算测试。"""

    def test_positive(self) -> None:
        """正余量取 CJK 密集下界（tokens）。"""
        assert WeightedTokenEstimator().chars_budget_for(500) == 500

    def test_zero_and_negative(self) -> None:
        """零/负余量截为 0。"""
        estimator = WeightedTokenEstimator()
        assert estimator.chars_budget_for(0) == 0
        assert estimator.chars_budget_for(-5) == 0


class TestVideoBlockEstimate:
    def test_video_estimate_scales_with_frames(self) -> None:
        estimator = WeightedTokenEstimator()
        one = estimator._estimate_block(
            VideoBlock(frames=[ImageBlock(base64="a", mime_type="image/jpeg")])
        )
        three = estimator._estimate_block(
            VideoBlock(
                frames=[
                    ImageBlock(base64="a", mime_type="image/jpeg"),
                    ImageBlock(base64="b", mime_type="image/jpeg"),
                    ImageBlock(base64="c", mime_type="image/jpeg"),
                ]
            )
        )
        assert one > 1
        assert three == one * 3
