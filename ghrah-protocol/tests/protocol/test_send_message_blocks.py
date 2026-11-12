# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""send_message 携带内容块的 wire 契约测试。

覆盖 K1 的协议侧验收：
- ``content_blocks`` 可携带 image / text / file / audio；
- 空正文 + 非空块合法（多模态消息）；
- ``model_dump(mode="json")`` 往返幂等；
- 未知块类型被判别联合拒绝（而不是静默吞掉）；
- 不带块时行为与旧载荷一致。
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from ghrah.protocol.types import ImageBlockPayload, SendMessagePayload


def _image_block() -> dict[str, object]:
    return {
        "type": "image",
        "base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"),
        "mime_type": "image/png",
    }


def test_send_message_accepts_image_block() -> None:
    """图片块可被解析，且 text 与 image 可混合。"""
    payload = SendMessagePayload.model_validate(
        {
            "project_id": "p1",
            "agent_id": "a1",
            "target": "agent-a",
            "content": "看这张图",
            "content_blocks": [
                {"type": "text", "text": "看这张图"},
                _image_block(),
            ],
        }
    )
    assert payload.content_blocks is not None
    assert len(payload.content_blocks) == 2
    assert isinstance(payload.content_blocks[1], ImageBlockPayload)
    assert payload.content_blocks[1].mime_type == "image/png"


def test_send_message_empty_content_with_blocks() -> None:
    """纯块消息：正文为空字符串，块非空，协议层接受。"""
    payload = SendMessagePayload.model_validate(
        {
            "project_id": "p1",
            "agent_id": "a1",
            "target": "agent-a",
            "content": "",
            "content_blocks": [_image_block()],
        }
    )
    assert payload.content == ""
    assert payload.content_blocks is not None and len(payload.content_blocks) == 1


def test_send_message_blocks_dump_roundtrip() -> None:
    """dump → validate → dump 幂等。"""
    payload = SendMessagePayload.model_validate(
        {
            "project_id": "p1",
            "agent_id": "a1",
            "target": "agent-a",
            "content": "hi",
            "content_blocks": [
                {"type": "text", "text": "hi"},
                _image_block(),
                {
                    "type": "file",
                    "base64": base64.b64encode(b"hello").decode("ascii"),
                    "mime_type": "text/plain",
                    "filename": "note.txt",
                },
                {"type": "audio", "data": "AAAA", "mime_type": "audio/wav"},
            ],
        }
    )
    dumped = payload.model_dump(mode="json")
    parsed = SendMessagePayload.model_validate(dumped)
    assert parsed.model_dump(mode="json") == dumped


def test_send_message_without_blocks_matches_legacy() -> None:
    """不带块时，dump 结果只多出 content_blocks=None。"""
    payload = SendMessagePayload.model_validate(
        {
            "project_id": "p1",
            "agent_id": "a1",
            "target": "agent-a",
            "content": "hello",
        }
    )
    dumped = payload.model_dump(mode="json")
    assert dumped["content"] == "hello"
    assert dumped.get("content_blocks") is None


def test_send_message_rejects_unknown_block_type() -> None:
    """未知块类型必须失败，不能静默吞掉（旧核兼容降级由调用方处理）。"""
    with pytest.raises(ValidationError):
        SendMessagePayload.model_validate(
            {
                "project_id": "p1",
                "agent_id": "a1",
                "target": "agent-a",
                "content": "",
                "content_blocks": [{"type": "hologram", "data": "..."}],
            }
        )
