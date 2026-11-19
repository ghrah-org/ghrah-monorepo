# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""测试共用：脚本化 LLM 与最小 Core 装配桩。

旧的 ``put_agent_template``（往 ManifestStore 写 YAML agent manifest）随 manifest
链路一起退役：模板现在存在 ``agent_templates`` 表里，由 TemplateStore 管。
"""

from __future__ import annotations

from typing import Any

from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse

__all__ = ["EchoLLM"]


class EchoLLM:
    """固定回一句 ``ready`` 的脚本化 LLM；不计调用次数。"""

    model = "project-message-test"

    def configure_tools(self, tools: Any) -> None:
        """忽略工具表：本桩只验证链路，不跑工具。"""

    def apply_model_overrides(self, overrides: Any) -> None:
        """忽略模型覆盖。"""

    async def generate(self, messages: Any, tools: Any = None) -> LLMResponse:
        """固定回复，保证确定性。"""
        return LLMResponse(content_blocks=[TextBlock(text="ready")])
