# SPDX-License-Identifier: Apache-2.0
"""Responses 原生消息与流事件适配；使用 responses.create 接口。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from ghrah.chat.content import (
    ErrorBlock,
    FileBlock,
    ImageBlock,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolCallChunkBlock,
)
from ghrah.chat.format import LLMResponse, LLMStreamEvent
from ghrah.chat.format.openai import OpenAIFormat
from ghrah.chat.message import ChatMessage
from ghrah.llm.errors import LLMError
from ghrah.types.tokens import TokenUsage


def _data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return value.model_dump(mode="json", exclude_none=True)


class OpenAIResponsesFormat(OpenAIFormat):
    """保留 Responses 输出项供下一回合重放，包含不透明的推理状态。"""

    def _format_input(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                items.extend(
                    {
                        "type": "function_call_output",
                        "call_id": r.tool_call_id,
                        "output": r.content if r.success else (r.error or r.content),
                    }
                    for r in msg.tool_results
                )
                continue
            if msg.role == "ai" and "responses_output" in msg.metadata:
                items.extend(msg.metadata["responses_output"])
                continue
            role = "assistant" if msg.role == "ai" else msg.role
            content: list[dict[str, Any]] = []
            for block in msg.content_blocks:
                if isinstance(block, TextBlock):
                    content.append({"type": "input_text", "text": block.text})
                elif isinstance(block, ImageBlock) and role != "assistant":
                    url = block.url or (
                        f"data:{block.mime_type or 'image/png'};base64,{block.base64}"
                        if block.base64
                        else None
                    )
                    if not url:
                        raise ValueError("Responses image requires url or base64")
                    content.append({"type": "input_image", "image_url": url})
                elif isinstance(block, FileBlock) and role != "assistant":
                    part: dict[str, Any] = {"type": "input_file"}
                    if block.url:
                        part["file_url"] = block.url
                    elif block.base64:
                        part["file_data"] = (
                            f"data:{block.mime_type or 'application/octet-stream'};base64,"
                            f"{block.base64}"
                        )
                        part["filename"] = block.filename or "file"
                    else:
                        raise ValueError("Responses file requires url or base64")
                    content.append(part)
                elif isinstance(block, ToolCallBlock):
                    continue
                elif isinstance(block, ReasoningBlock):
                    raise ValueError(
                        "Responses reasoning replay requires responses_output metadata"
                    )
                else:
                    raise ValueError(f"Unsupported Responses input block: {block.type}")
            if content:
                items.append({"role": role, "content": content})
            for call in msg.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    }
                )
        return items

    def _responses_kwargs(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        explicit: dict[str, Any] = {"model": self.model, "input": self._format_input(messages)}
        if self._temperature is not None:
            explicit["temperature"] = self._temperature
        if self._max_tokens is not None:
            explicit["max_output_tokens"] = self._max_tokens
        if self._top_p is not None:
            explicit["top_p"] = self._top_p
        if self._top_k is not None:
            raise ValueError("Responses does not support top_k")
        extras = {k: v for k, v in self._extra_params.items() if k != "stream"}
        if "top_k" in extras:
            raise ValueError("Responses does not support top_k")
        if "max_tokens" in extras:
            value = extras.pop("max_tokens")
            if self._max_tokens is None:
                if "max_output_tokens" in extras:
                    raise ValueError("Conflicting max_tokens/max_output_tokens request parameters")
                extras["max_output_tokens"] = value
        effective_tools = tools if tools is not None else self._tools
        if effective_tools:
            explicit["tools"] = [
                {"type": "function", **tool["function"]}
                for tool in self._format_tools(effective_tools)
            ]
        # stream 属于适配器控制字段，在调用前单独处理。
        return self._request_kwargs(
            explicit,
            self._get_client().responses.create,
            extras,
        )

    async def generate(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        """生成完整响应；显式 stream=True 时聚合原生流的终态。"""
        if self._extra_params.get("stream"):
            async with aclosing(self.stream(messages, tools)) as events:
                async for event in events:
                    if event.response is not None:
                        return event.response
            raise LLMError("responses", "Stream ended without a final response")
        kwargs = self._responses_kwargs(messages, tools)
        response = await self._create_response(kwargs)
        return self._parse_response(response)

    async def _create_response(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await self._get_client().responses.create(**kwargs)
        except Exception as exc:
            raise LLMError("responses", "Responses request failed") from exc

    def _parse_response(self, response: Any) -> LLMResponse:
        data = _data(response)
        blocks: list[Any] = []
        output = data.get("output") or []
        for item in output:
            kind = item["type"]
            if kind == "message":
                for part in item.get("content", []):
                    if part["type"] == "output_text":
                        blocks.append(TextBlock(text=part["text"]))
                    elif part["type"] == "refusal":
                        blocks.append(ErrorBlock(error_type="refusal", message=part["refusal"]))
                    else:
                        raise LLMError("responses", "Unsupported output content type")
            elif kind == "function_call":
                try:
                    args = json.loads(item["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    blocks.append(
                        ErrorBlock(
                            error_type="invalid_tool_arguments",
                            message="Function arguments must be a JSON object",
                        )
                    )
                    continue
                blocks.append(ToolCallBlock(id=item["call_id"], name=item["name"], arguments=args))
            elif kind == "reasoning":
                text = "".join(p["text"] for p in item.get("content", []) or [])
                if not text:
                    text = "".join(p["text"] for p in item.get("summary", []))
                blocks.append(
                    ReasoningBlock(reasoning=text, incomplete=item.get("status") == "incomplete")
                )
            else:
                raise LLMError("responses", f"Unsupported output item type: {kind}")
        status = data.get("status", "completed")
        if status in {"failed", "cancelled", "incomplete"}:
            blocks.append(
                ErrorBlock(error_type=f"response_{status}", message=f"Responses request {status}")
            )
        usage = data.get("usage")
        token_usage = None
        if usage:
            token_usage = TokenUsage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                cache_read_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
                cache_write_tokens=(usage.get("input_tokens_details") or {}).get(
                    "cache_write_tokens", 0
                ),
            )
        return LLMResponse(
            content_blocks=blocks,
            token_usage=token_usage,
            raw=response,
            response_metadata={
                "id": data.get("id"),
                "model": data.get("model"),
                "status": status,
                "responses_output": output,
                "usage": usage,
            },
        )

    async def stream(
        self, messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[LLMStreamEvent]:
        """将原生流映射为内容增量与完整终态；任何退出路径关闭 SDK 流。"""
        stream = await self._create_response(
            {**self._responses_kwargs(messages, tools), "stream": True}
        )
        terminal = False
        try:
            async for raw_event in stream:
                event = _data(raw_event)
                kind = event["type"]
                if kind == "response.output_text.delta":
                    yield LLMStreamEvent(block=TextBlock(text=event["delta"]))
                elif kind in {
                    "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta",
                }:
                    yield LLMStreamEvent(block=ReasoningBlock(reasoning=event["delta"]))
                elif (
                    kind == "response.output_item.added"
                    and event["item"]["type"] == "function_call"
                ):
                    item = event["item"]
                    yield LLMStreamEvent(
                        block=ToolCallChunkBlock(
                            index=event["output_index"],
                            id=item["call_id"],
                            name=item["name"],
                            arguments_chunk=item.get("arguments", ""),
                        )
                    )
                elif kind == "response.function_call_arguments.delta":
                    yield LLMStreamEvent(
                        block=ToolCallChunkBlock(
                            index=event["output_index"], arguments_chunk=event["delta"]
                        )
                    )
                elif kind == "response.refusal.delta":
                    yield LLMStreamEvent(
                        block=ErrorBlock(error_type="refusal", message=event["delta"])
                    )
                elif kind in {"response.completed", "response.incomplete", "response.failed"}:
                    terminal = True
                    yield LLMStreamEvent(response=self._parse_response(event["response"]))
                    return
                elif kind == "error":
                    raise LLMError("responses", "Provider stream error")
            if not terminal:
                raise LLMError("responses", "Stream ended without a terminal event")
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError("responses", "Responses stream failed") from exc
        finally:
            await stream.close()
