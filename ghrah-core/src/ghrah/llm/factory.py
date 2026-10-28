# SPDX-License-Identifier: Apache-2.0
"""消费 agentconf 模型端点；请求参数由 ghrah 调用者提供。"""

from __future__ import annotations

from typing import Any

from ghrah.chat.format import ChatFormat
from ghrah.llm.errors import LLMError, UnsupportedFormat
from ghrah.types.config_types import ModelOverrides


def _get_secret_value(value: Any) -> Any:
    """显式提取 SDK 的秘密包装值，不记录其内容。"""
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


class LLMFactory:
    """按端点用途、接口格式和厂商创建适配器，不查询旧 Agent 配置。"""

    @classmethod
    def create(cls, resolved: Any, params: ModelOverrides | None = None) -> ChatFormat:
        """创建 LLM；拒绝非 LLM 用途和未实现的格式，保留能力与来源。"""
        if resolved.model.model_type != "llm":
            raise LLMError("endpoint", f"Unsupported model purpose: {resolved.model.model_type!r}")
        connection = resolved.connection
        fmt = connection.api_format
        if fmt == "openai.chat_completions":
            if connection.provider_type == "deepseek":
                from ghrah.chat.format.deepseek import DeepSeekFormat

                adapter = DeepSeekFormat
            else:
                from ghrah.chat.format.openai import OpenAIFormat

                adapter = OpenAIFormat
        elif fmt == "openai.responses":
            from ghrah.chat.format.openai_responses import OpenAIResponsesFormat

            adapter = OpenAIResponsesFormat
        elif fmt == "anthropic.messages":
            from ghrah.chat.format.anthropic import AnthropicFormat

            adapter = AnthropicFormat
        else:
            raise UnsupportedFormat(
                str(connection.provider_type), f"Unsupported api_format: {fmt!r}"
            )
        options = _get_secret_value(connection.connection_options) or {}
        allowed_options = {"timeout", "max_retries", "default_headers", "default_query"}
        if not isinstance(options, dict) or options.keys() - allowed_options:
            raise LLMError(str(connection.provider_type), "Unsupported connection_options fields")
        result = adapter(
            model=resolved.model.model_name,
            api_key=_get_secret_value(connection.api_key),
            base_url=_get_secret_value(connection.base_url) or None,
            connection_options=options,
            temperature=None,
        )
        if params is not None:
            result.apply_model_overrides(params)
        result.model_capabilities = resolved.capabilities.model_dump(mode="json")
        result.capability_sources = dict(resolved.capability_sources)
        result.model_metadata = (
            resolved.metadata.model_dump(mode="json") if resolved.metadata is not None else None
        )
        return result
