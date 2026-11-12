# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""入站内容块（``content_blocks``）的 Subject 侧校验。

wire 形状由 ``ghrah-protocol`` 负责（只做形状），**策略归 Subject**：
Room 的 ``room_send.data`` 是自由字典，协议模型不覆盖它，所以 Room 通路
必须在受理前自行校验；``send_message`` 直聊分支复用同一套规则，保证
两条入站路径的限额与错误码一致。

M1 规则（常量集中在本模块，后续接配置切片时再迁移）：

- 单消息块数 ≤ 8，其中 image ≤ 4；
- 单块内联 base64 ≤ 4 MiB 字符（原始约 3 MiB），单消息合计 ≤ 8 MiB 字符；
- 类型白名单：``text`` / ``image`` / ``audio`` / ``file``；
- image 的 ``mime_type`` 必须在 png / jpeg / webp / gif 白名单内
  （``audio`` / ``file`` 的 MIME 策略待附件库切片再定）。

失败抛 :class:`ghrah.subject.errors.StableError`：``code`` 是稳定错误码
（前端按它做 i18n 映射），``detail`` 只做诊断展示。

体积计数口径：只统计内联载荷字符串（``base64`` / ``audio.data`` /
``data:`` 开头的 ``url``），URL 引用与文本不计入——M1 图片走内联，
K4 附件引用不受该限额约束。
"""

from __future__ import annotations

from typing import Any

from ghrah.subject.errors import StableError

__all__ = [
    "ALLOWED_BLOCK_TYPES",
    "ALLOWED_IMAGE_MIME_TYPES",
    "CONTENT_BLOCKS_MALFORMED",
    "CONTENT_BLOCKS_TOTAL_TOO_LARGE",
    "CONTENT_BLOCK_TOO_LARGE",
    "CONTENT_TEXT_REQUIRED",
    "MAX_BLOCK_BASE64_CHARS",
    "MAX_CONTENT_BLOCKS",
    "MAX_IMAGE_BLOCKS",
    "MAX_TOTAL_BASE64_CHARS",
    "UNSUPPORTED_BLOCK_TYPE",
    "validate_content_blocks",
]

# ─── 稳定错误码（wire 契约，前端按码映射文案） ───

CONTENT_BLOCKS_MALFORMED = "content_blocks_malformed"
UNSUPPORTED_BLOCK_TYPE = "unsupported_block_type"
CONTENT_BLOCK_TOO_LARGE = "content_block_too_large"
CONTENT_BLOCKS_TOTAL_TOO_LARGE = "content_blocks_total_too_large"
CONTENT_TEXT_REQUIRED = "content_text_required"

# ─── M1 限额（字符数；base64 串长度，不是解码后字节数） ───

MAX_CONTENT_BLOCKS = 8
MAX_IMAGE_BLOCKS = 4
MAX_BLOCK_BASE64_CHARS = 4 * 1024 * 1024
MAX_TOTAL_BASE64_CHARS = 8 * 1024 * 1024

ALLOWED_BLOCK_TYPES = frozenset({"text", "image", "audio", "file"})
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


def _sized_payloads(block: dict[str, Any]) -> list[str]:
    """返回计入体积限额的内联载荷字符串。"""
    payloads: list[str] = []
    for key in ("base64", "data"):
        value = block.get(key)
        if isinstance(value, str) and value:
            payloads.append(value)
    url = block.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        payloads.append(url)
    return payloads


def _require_source(block: dict[str, Any], index: int) -> None:
    """``image`` / ``file`` 必须至少带一种载体（base64 或 url）。"""
    for key in ("base64", "url"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return
    raise StableError(
        CONTENT_BLOCKS_MALFORMED,
        f"block {index} requires base64 or url",
    )


def validate_content_blocks(raw: object) -> list[dict[str, Any]] | None:
    """校验并规范化 ``content_blocks``。

    Args:
        raw: wire 上收到的原始值（``None`` / 数组 / 任意非法值）。

    Returns:
        规范化后的块列表（元素为浅拷贝的 dict，未知字段原样保留，便于
        后续切片透传）；``None`` / 空数组归一为 ``None``，表示本条消息
        不带内容块。

    Raises:
        StableError: 形状/类型/数量/体积任一不满足 M1 规则。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            "content_blocks must be an array",
        )
    if not raw:
        return None
    if len(raw) > MAX_CONTENT_BLOCKS:
        raise StableError(
            CONTENT_BLOCKS_TOTAL_TOO_LARGE,
            f"at most {MAX_CONTENT_BLOCKS} blocks per message",
        )

    blocks: list[dict[str, Any]] = []
    image_count = 0
    total_chars = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"block {index} must be an object",
            )
        block_type = item.get("type")
        if not isinstance(block_type, str) or not block_type:
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"block {index} missing type",
            )
        if block_type not in ALLOWED_BLOCK_TYPES:
            raise StableError(
                UNSUPPORTED_BLOCK_TYPE,
                f"block {index} type={block_type!r}",
            )

        block = dict(item)
        if block_type == "text":
            if not isinstance(block.get("text"), str):
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"block {index} text must be a string",
                )
        elif block_type == "image":
            image_count += 1
            if image_count > MAX_IMAGE_BLOCKS:
                raise StableError(
                    CONTENT_BLOCKS_TOTAL_TOO_LARGE,
                    f"at most {MAX_IMAGE_BLOCKS} image blocks per message",
                )
            _require_source(block, index)
            mime_type = block.get("mime_type")
            if not isinstance(mime_type, str) or not mime_type:
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"block {index} image requires mime_type",
                )
            if mime_type.lower() not in ALLOWED_IMAGE_MIME_TYPES:
                raise StableError(
                    UNSUPPORTED_BLOCK_TYPE,
                    f"block {index} unsupported image mime_type={mime_type!r}",
                )
        elif block_type == "audio":
            if not isinstance(block.get("data"), str) or not block.get("data"):
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"block {index} audio requires data",
                )
            mime_type = block.get("mime_type")
            if not isinstance(mime_type, str) or not mime_type:
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"block {index} audio requires mime_type",
                )
        else:  # file
            _require_source(block, index)

        block_chars = sum(len(payload) for payload in _sized_payloads(block))
        if block_chars > MAX_BLOCK_BASE64_CHARS:
            raise StableError(
                CONTENT_BLOCK_TOO_LARGE,
                f"block {index} payload exceeds {MAX_BLOCK_BASE64_CHARS} chars",
            )
        total_chars += block_chars
        blocks.append(block)

    if total_chars > MAX_TOTAL_BASE64_CHARS:
        raise StableError(
            CONTENT_BLOCKS_TOTAL_TOO_LARGE,
            f"content_blocks payload exceeds {MAX_TOTAL_BASE64_CHARS} chars",
        )
    return blocks
