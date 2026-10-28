# SPDX-License-Identifier: Apache-2.0
"""Responses 的原生请求、完整响应、流式终态与取消契约。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    import httpx2 as httpx
except ImportError:
    import httpx
import pytest
from openai import AsyncOpenAI

from ghrah.chat.content import (
    AudioBlock,
    ErrorBlock,
    ImageBlock,
    ReasoningBlock,
    TextBlock,
    ToolCallChunkBlock,
)
from ghrah.chat.format.openai import OpenAIFormat
from ghrah.chat.format.openai_responses import OpenAIResponsesFormat
from ghrah.chat.message import ChatMessage
from ghrah.llm.errors import LLMError
from ghrah.types.config_types import ModelOverrides


def response(status="completed"):
    return {
        "id": "resp_test",
        "model": "deployment",
        "status": status,
        "output": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "think"}],
                "encrypted_content": "opaque",
                "status": "completed",
            },
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "answer", "annotations": []}],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read",
                "arguments": '{"path":"x"}',
            },
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 7,
            "total_tokens": 27,
            "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


async def test_native_request_and_tool_reasoning_roundtrip():
    fmt = OpenAIResponsesFormat(model="deployment")
    fmt.apply_model_overrides(
        ModelOverrides(
            temperature=0,
            max_tokens=40,
            top_p=0,
            extra_params={
                "max_output_tokens": 99,
                "max_tokens": 100,
                "reasoning": {"effort": "low"},
            },
        )
    )
    create = AsyncMock(return_value=response())
    fmt._client = SimpleNamespace(responses=SimpleNamespace(create=create))
    result = await fmt.generate(
        [ChatMessage.system("system"), ChatMessage.user("hello")],
        [{"name": "read", "parameters": {"type": "object"}}],
    )
    kwargs = create.call_args.kwargs
    assert "messages" not in kwargs
    assert kwargs["input"][0] == {
        "role": "system",
        "content": [{"type": "input_text", "text": "system"}],
    }
    assert kwargs["tools"][0] == {
        "type": "function",
        "name": "read",
        "parameters": {"type": "object"},
    }
    assert kwargs["temperature"] == kwargs["top_p"] == 0
    assert kwargs["max_output_tokens"] == 40
    assert result.text == "answer" and result.reasoning == "think"
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].arguments == {"path": "x"}
    assert result.token_usage.input_tokens == 20
    assert result.token_usage.cache_read_tokens == 10
    assert result.token_usage.cache_write_tokens == 2
    # 不透明推理状态必须跨序列化保存；回传用 call_id，不能使用 fc_1。
    msg = ChatMessage.from_dict(result.to_chat_message().to_dict())
    replay = fmt._format_input([msg, ChatMessage.tool("call_1", "file contents")])
    assert replay[:-1] == response()["output"]
    assert replay[-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "file contents",
    }


class FakeStream:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield event
        if self.error:
            raise self.error

    async def close(self):
        self.closed = True


def stream_format(events, error=None):
    stream = FakeStream(events, error)
    fmt = OpenAIResponsesFormat(model="deployment")
    fmt._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=stream)))
    return fmt, stream


def events():
    return [
        {"type": "response.output_text.delta", "delta": "answer"},
        {"type": "response.reasoning_summary_text.delta", "delta": "think"},
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": response()["output"][2] | {"arguments": ""},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '{"path":'},
        {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '"x"}'},
        {"type": "response.completed", "response": response()},
    ]


async def test_stream_has_typed_deltas_and_complete_usage():
    fmt, stream = stream_format(events())
    result = [event async for event in fmt.stream([ChatMessage.user("hi")])]
    assert isinstance(result[0].block, TextBlock)
    assert isinstance(result[1].block, ReasoningBlock)
    assert all(isinstance(e.block, ToolCallChunkBlock) for e in result[2:5])
    assert result[2].block.id == "call_1" and result[2].block.index == 2
    assert "".join(e.block.arguments_chunk for e in result[2:5]) == '{"path":"x"}'
    assert result[-1].response.text == "answer"
    assert result[-1].response.token_usage.total_tokens == 27
    assert fmt._client.responses.create.call_args.kwargs["stream"] is True
    assert stream.closed


async def test_generate_aggregates_explicit_stream():
    fmt, stream = stream_format(events())
    fmt.apply_model_overrides(ModelOverrides(extra_params={"stream": True}))
    result = await fmt.generate([])
    assert result.tool_calls[0].arguments == {"path": "x"}
    assert stream.closed


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
def test_terminal_failure_is_a_structured_error(status):
    result = OpenAIResponsesFormat()._parse_response(response(status))
    assert isinstance(result.content_blocks[-1], ErrorBlock)
    assert result.content_blocks[-1].error_type == f"response_{status}"


@pytest.mark.parametrize("kind", ["response.failed", "response.incomplete"])
async def test_stream_terminal_error_preserves_partial_output(kind):
    fmt, stream = stream_format([{"type": kind, "response": response(kind.split(".")[-1])}])
    result = [e async for e in fmt.stream([])]
    assert result[-1].response.text == "answer"
    assert isinstance(result[-1].response.content_blocks[-1], ErrorBlock)
    assert stream.closed


@pytest.mark.parametrize("error", [RuntimeError("transport"), asyncio.CancelledError()])
async def test_transport_exception_or_cancellation_closes_stream(error):
    fmt, stream = stream_format([], error)
    with pytest.raises(
        asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else LLMError
    ):
        _ = [e async for e in fmt.stream([])]
    assert stream.closed


async def test_early_consumer_close_and_truncated_stream():
    fmt, stream = stream_format(events())
    gen = fmt.stream([])
    await anext(gen)
    await gen.aclose()
    assert stream.closed
    fmt, stream = stream_format(events()[:-1])
    with pytest.raises(LLMError, match="terminal"):
        _ = [e async for e in fmt.stream([])]
    assert stream.closed


async def test_error_event_closes_stream():
    fmt, stream = stream_format([{"type": "error", "message": "provider details"}])
    with pytest.raises(LLMError, match="Provider stream error"):
        _ = [e async for e in fmt.stream([])]
    assert stream.closed


@pytest.mark.parametrize(
    "adapter,path",
    [(OpenAIFormat, "/v1/chat/completions"), (OpenAIResponsesFormat, "/v1/responses")],
)
async def test_actual_sdk_serializes_distinct_endpoints_and_extensions(adapter, path):
    seen = []

    async def handler(request):
        seen.append(request)
        payload = (
            response()
            if adapter is OpenAIResponsesFormat
            else {
                "id": "chat",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                        "index": 0,
                    }
                ],
                "model": "deployment",
                "usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
            }
        )
        return httpx.Response(200, json=payload)

    async with AsyncOpenAI(
        api_key="fake",
        base_url="https://local.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as client:
        fmt = adapter(model="deployment")
        fmt._client = client
        fmt.apply_model_overrides(
            ModelOverrides(temperature=0, extra_params={"temperature": 1, "vendor_flag": False})
        )
        assert (await fmt.generate([ChatMessage.user("hi")])).text == "answer"
    assert seen[0].url.path == path
    body = json.loads(seen[0].content)
    assert body["temperature"] == 0 and body["vendor_flag"] is False


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "bad"},
        {"api_key": "bad"},
        {"extra_body": {"model": "bad"}},
        {"input": []},
        {"base_url": "bad"},
    ],
)
def test_extensions_cannot_override_identity(extra):
    with pytest.raises(ValueError, match="identity"):
        OpenAIResponsesFormat().apply_model_overrides(ModelOverrides(extra_params=extra))


async def test_unsupported_input_and_request_parameters_are_diagnosed():
    fmt = OpenAIResponsesFormat()
    with pytest.raises(ValueError, match="Unsupported"):
        fmt._format_input([ChatMessage.user([AudioBlock(data="x")])])
    assert (
        fmt._format_input([ChatMessage.user([ImageBlock(url="https://local.invalid/image")])])[0][
            "content"
        ][0]["type"]
        == "input_image"
    )
    fmt.apply_model_overrides(ModelOverrides(top_k=0))
    with pytest.raises(ValueError, match="top_k"):
        await fmt.generate([])


async def test_real_sdk_stream_events_map_to_final_response():
    async def handler(request):
        assert request.url.path == "/v1/responses"
        assert json.loads(request.content)["stream"] is True
        data = "".join(f"data: {json.dumps(event)}\n\n" for event in events())
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})

    async with AsyncOpenAI(
        api_key="fake",
        base_url="https://local.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as client:
        fmt = OpenAIResponsesFormat(model="deployment")
        fmt._client = client
        result = [event async for event in fmt.stream([ChatMessage.user("hello")])]
    assert result[-1].response.tool_calls[0].id == "call_1"
    assert result[-1].response.token_usage.total_tokens == 27
    assert result[0].block.text == "answer"


def test_invalid_function_arguments_are_an_error_not_a_tool_execution():
    data = response()
    data["output"][2]["arguments"] = "[]"
    result = OpenAIResponsesFormat()._parse_response(data)
    assert not result.tool_calls
    assert any(
        isinstance(b, ErrorBlock) and b.error_type == "invalid_tool_arguments"
        for b in result.content_blocks
    )


async def test_nonstream_provider_error_is_mapped_without_provider_details():
    fmt = OpenAIResponsesFormat(model="deployment")
    fmt._client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("private details")))
    )
    with pytest.raises(LLMError, match="Responses request failed") as error:
        await fmt.generate([])
    assert "private details" not in str(error.value)
