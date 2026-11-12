# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Room/Subject 侧 ``content_blocks`` 校验单测：形状、类型白名单、数量、体积。

错误码是 wire 契约（前端按码映射文案），测试按 ``code`` 精确匹配。
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.subject.errors import StableError
from ghrah.subject.room.content_blocks import (
    CONTENT_BLOCK_TOO_LARGE,
    CONTENT_BLOCKS_MALFORMED,
    CONTENT_BLOCKS_TOTAL_TOO_LARGE,
    MAX_BLOCK_BASE64_CHARS,
    MAX_CONTENT_BLOCKS,
    UNSUPPORTED_BLOCK_TYPE,
    validate_content_blocks,
)


def _image(base64: str = "aGVsbG8=", mime_type: str = "image/png") -> dict[str, Any]:
    return {"type": "image", "base64": base64, "mime_type": mime_type}


def _code(raw: object) -> str:
    with pytest.raises(StableError) as excinfo:
        validate_content_blocks(raw)
    return excinfo.value.code


def test_none_and_empty_list_normalize_to_none() -> None:
    assert validate_content_blocks(None) is None
    assert validate_content_blocks([]) is None


def test_valid_blocks_are_copied_and_unknown_fields_preserved() -> None:
    raw = [
        {"type": "text", "text": "看图"},
        {**_image(), "future_field": 1},
    ]
    blocks = validate_content_blocks(raw)
    assert blocks == raw
    assert blocks is not None
    # 返回副本，调用方改动不会回写 wire 原始值
    blocks[0]["text"] = "changed"
    assert raw[0]["text"] == "看图"


@pytest.mark.parametrize("raw", ["blocks", {"type": "text"}, 42])
def test_non_array_is_malformed(raw: object) -> None:
    assert _code(raw) == CONTENT_BLOCKS_MALFORMED


def test_non_object_element_is_malformed() -> None:
    assert _code(["text"]) == CONTENT_BLOCKS_MALFORMED


def test_missing_type_is_malformed() -> None:
    assert _code([{"text": "hi"}]) == CONTENT_BLOCKS_MALFORMED


def test_unknown_block_type_is_unsupported() -> None:
    assert _code([{"type": "video", "url": "x"}]) == UNSUPPORTED_BLOCK_TYPE


def test_text_block_requires_text_field() -> None:
    assert _code([{"type": "text"}]) == CONTENT_BLOCKS_MALFORMED


def test_image_requires_mime_type_and_whitelist() -> None:
    assert _code([{"type": "image", "base64": "aGVsbG8="}]) == CONTENT_BLOCKS_MALFORMED
    assert _code([{**_image(), "mime_type": "image/svg+xml"}]) == UNSUPPORTED_BLOCK_TYPE
    # 白名单大小写不敏感
    assert validate_content_blocks([_image(mime_type="IMAGE/PNG")]) is not None


def test_image_and_file_require_payload_source() -> None:
    assert _code([{"type": "image", "mime_type": "image/png"}]) == CONTENT_BLOCKS_MALFORMED
    assert _code([{"type": "file", "mime_type": "text/plain"}]) == CONTENT_BLOCKS_MALFORMED
    assert validate_content_blocks([{"type": "file", "url": "file:///tmp/a.txt"}]) is not None


def test_audio_requires_data_and_mime_type() -> None:
    assert _code([{"type": "audio", "mime_type": "audio/wav"}]) == CONTENT_BLOCKS_MALFORMED
    assert _code([{"type": "audio", "data": "AAAA"}]) == CONTENT_BLOCKS_MALFORMED
    assert validate_content_blocks([{"type": "audio", "data": "AAAA", "mime_type": "audio/wav"}])
    # audio 的 MIME 白名单留给附件库切片，M1 只做形状校验
    assert validate_content_blocks([{"type": "audio", "data": "AAAA", "mime_type": "audio/ogg"}])


def test_block_count_limit() -> None:
    assert _code([{"type": "text", "text": "x"}] * (MAX_CONTENT_BLOCKS + 1)) == (
        CONTENT_BLOCKS_TOTAL_TOO_LARGE
    )
    allowed = [{"type": "text", "text": f"x{i}"} for i in range(MAX_CONTENT_BLOCKS)]
    assert len(validate_content_blocks(allowed) or []) == MAX_CONTENT_BLOCKS


def test_image_count_limit() -> None:
    five_images = [_image() for _ in range(5)]
    assert _code(five_images) == CONTENT_BLOCKS_TOTAL_TOO_LARGE
    assert validate_content_blocks(five_images[:4]) is not None


def test_single_block_size_limit() -> None:
    oversized = _image(base64="A" * (MAX_BLOCK_BASE64_CHARS + 1))
    assert _code([oversized]) == CONTENT_BLOCK_TOO_LARGE
    # 恰好等于上限合法
    assert validate_content_blocks([_image(base64="A" * MAX_BLOCK_BASE64_CHARS)]) is not None


def test_total_size_limit() -> None:
    half = "A" * (MAX_BLOCK_BASE64_CHARS - 1024)
    blocks = [
        _image(base64=half),
        _image(base64=half),
        _image(base64=half),
    ]
    # 每块都在单块上限内，但合计超过 8 MiB
    assert _code(blocks) == CONTENT_BLOCKS_TOTAL_TOO_LARGE


def test_text_payload_does_not_count_toward_base64_limits() -> None:
    huge_text = {"type": "text", "text": "A" * (MAX_BLOCK_BASE64_CHARS + 1)}
    assert validate_content_blocks([huge_text]) is not None
