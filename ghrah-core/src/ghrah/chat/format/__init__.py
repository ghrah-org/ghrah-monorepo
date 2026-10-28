# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LLM 交互格式适配器。

封装不同 SDK 的消息格式和 API 差异，统一转换为项目内部的 ChatMessage + LLMResponse。

包含：
- ChatFormat: 格式适配器抽象基类
- LLMResponse: 统一的 LLM 响应
- TokenUsage: token 用量
- create_format: 工厂函数
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ghrah.chat.content import (
    ContentBlock,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolCallChunkBlock,
)
from ghrah.chat.message import ChatMessage
from ghrah.types.tokens import TokenUsage

if TYPE_CHECKING:
    from ghrah.types.config_types import ModelOverrides


@dataclass
class LLMResponse:
    content_blocks: list[ContentBlock] = field(default_factory=list)
    token_usage: TokenUsage | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.content_blocks if isinstance(b, TextBlock))

    @property
    def tool_calls(self) -> list[ToolCallBlock]:
        return [b for b in self.content_blocks if isinstance(b, ToolCallBlock)]

    @property
    def reasoning(self) -> str | None:
        for b in self.content_blocks:
            if isinstance(b, ReasoningBlock):
                return b.reasoning
        return None

    def to_chat_message(self, source: str | None = None) -> ChatMessage:
        return ChatMessage(
            role="ai",
            content_blocks=list(self.content_blocks),
            source=source,
            metadata=self.response_metadata,
        )


@dataclass
class LLMStreamEvent:
    """流式增量：内容块、最终响应或错误；取消以 CancelledError 传播。"""

    block: ContentBlock | ToolCallChunkBlock | None = None
    response: LLMResponse | None = None


class ChatFormat(ABC):
    _tools: list[dict[str, Any]]

    def __init__(self) -> None:
        self._tools: list[dict[str, Any]] = []
        # None 表示调用方未提供能力信息；字典是 agentconf 的完整解析结果。
        self.model_capabilities: dict[str, Any] | None = None
        self.capability_sources: dict[str, str] = {}
        self.model_metadata: dict[str, Any] | None = None
        self._extra_params: dict[str, Any] = {}

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[LLMStreamEvent]:
        """返回结构化增量；未提供原生流式适配的实现明确拒绝。"""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")
        yield  # 保持异步生成器签名。

    def configure_tools(self, tools: list[dict[str, Any]]) -> None:
        self._tools = tools

    def apply_model_overrides(self, overrides: ModelOverrides) -> None:
        """应用调用者的请求参数；显式类型字段优先于扩展参数。"""
        self._validate_extra_params(overrides.extra_params)
        self._extra_params = dict(overrides.extra_params)
        if overrides.temperature is not None:
            self._temperature = overrides.temperature
        if overrides.max_tokens is not None:
            self._max_tokens = overrides.max_tokens
        if overrides.top_p is not None:
            self._top_p = overrides.top_p

    @staticmethod
    def _validate_extra_params(params: dict[str, Any]) -> None:
        """扩展参数只含请求选项，禁止改写连接、请求消息或 SDK 包装层。"""
        forbidden = {
            "model",
            "api_key",
            "base_url",
            "provider_type",
            "api_format",
            "endpoint_name",
            "connection_options",
            "messages",
            "input",
            "tools",
            "system",
            "instructions",
            "extra_body",
            "extra_headers",
            "extra_query",
            "http_client",
            "default_headers",
            "default_query",
            "organization",
            "project",
            "previous_response_id",
            "conversation",
        }
        if not isinstance(params, dict) or params.keys() & forbidden:
            raise ValueError("extra_params cannot override model, connection or request identity")

    def _request_kwargs(
        self, explicit: dict[str, Any], create: Any, extra_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """已知 SDK 参数直接传入，其余厂商扩展通过 extra_body 无损传递。"""
        params = dict(self._extra_params if extra_params is None else extra_params)
        params.update(explicit)
        if params.get("stream"):
            raise ValueError("stream=True is unsupported by this adapter; use Responses.stream")
        accepted = inspect.signature(create).parameters
        # 接受任意关键字参数的包装调用方自行处理字段；显式 SDK 签名按字段分流。
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in accepted.values()):
            return params
        body = {k: v for k, v in params.items() if k not in accepted}
        result = {k: v for k, v in params.items() if k in accepted}
        if body:
            result["extra_body"] = body
        return result


__all__ = ["ChatFormat", "LLMResponse", "LLMStreamEvent", "TokenUsage", "create_format"]


def create_format(provider_type: str, **kwargs: object) -> ChatFormat:
    if provider_type == "openai":
        from ghrah.chat.format.openai import OpenAIFormat

        return OpenAIFormat(**kwargs)  # type: ignore[arg-type]
    elif provider_type == "anthropic":
        from ghrah.chat.format.anthropic import AnthropicFormat

        return AnthropicFormat(**kwargs)  # type: ignore[arg-type]
    elif provider_type == "deepseek":
        from ghrah.chat.format.deepseek import DeepSeekFormat

        return DeepSeekFormat(**kwargs)  # type: ignore[arg-type]
    else:
        raise ValueError(f"Unsupported provider type: {provider_type!r}")
