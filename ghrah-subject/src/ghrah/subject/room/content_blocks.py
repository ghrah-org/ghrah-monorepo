# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""入站内容块（``content_blocks``）的 Subject 侧校验。

wire 形状由 ``ghrah-protocol`` 负责（只做形状），**策略归 Subject**：
Room 的 ``room_send.data`` 是自由字典，协议模型不覆盖它，所以 Room 通路
必须在受理前自行校验；``send_message`` 直聊分支复用同一套规则，保证
两条入站路径的限额与错误码一致。

限额（常量集中在本模块，后续接配置切片时再迁移）：

- 单消息块数 ≤ 8，其中 image ≤ 4、video ≤ 1；
- 单块内联 base64 ≤ 4 MiB 字符（原始约 3 MiB），单消息合计 ≤ 8 MiB 字符；
- video 块不内联字节：必须用 ``attachment_id``（sha256 hex），帧是模型
  消费载体 —— 帧数 ≤ 4、单帧 ≤ 1.5 MiB 字符、帧合计 ≤ 4 MiB 字符；
- 类型白名单：``text`` / ``image`` / ``audio`` / ``file`` / ``video``；
- image（含 video 帧）的 ``mime_type`` 必须是 png / jpeg / webp / gif；
  video 的 ``mime_type`` 必须是 ``video/*``；
- ``image`` / ``audio`` / ``file`` 允许 ``attachment_id`` 而不强制
  ``base64`` / ``url``（至少一种载体）；出现 ``attachment_id`` 即校验其
  sha256 hex 形状（同时用于 REST 层防路径穿越）。

失败抛 :class:`ghrah.subject.errors.StableError`：``code`` 是稳定错误码
（前端按它做 i18n 映射），``detail`` 只做诊断展示。附件存在性/大小等
REST 层错误码（``attachment_not_found`` 等）不在本模块。

体积计数口径：只统计内联载荷字符串（``base64`` / ``audio.data`` /
``data:`` 开头的 ``url``），URL 引用与文本不计入，附件引用亦不计入。
"""

from __future__ import annotations

from typing import Any

from ghrah.subject.errors import StableError
from ghrah.subject.project.attachments import is_valid_attachment_id

__all__ = [
    "ALLOWED_BLOCK_TYPES",
    "ALLOWED_IMAGE_MIME_TYPES",
    "ALLOWED_VIDEO_MIME_PREFIX",
    "CONTENT_BLOCKS_MALFORMED",
    "CONTENT_BLOCKS_TOTAL_TOO_LARGE",
    "CONTENT_BLOCK_TOO_LARGE",
    "CONTENT_TEXT_REQUIRED",
    "MAX_BLOCK_BASE64_CHARS",
    "MAX_CONTENT_BLOCKS",
    "MAX_FRAME_BASE64_CHARS",
    "MAX_FRAMES_TOTAL_CHARS",
    "MAX_IMAGE_BLOCKS",
    "MAX_TOTAL_BASE64_CHARS",
    "MAX_VIDEO_BLOCKS",
    "MAX_VIDEO_FRAMES",
    "UNSUPPORTED_BLOCK_TYPE",
    "validate_content_blocks",
]

# ─── 稳定错误码（wire 契约，前端按码映射文案） ───

CONTENT_BLOCKS_MALFORMED = "content_blocks_malformed"
UNSUPPORTED_BLOCK_TYPE = "unsupported_block_type"
CONTENT_BLOCK_TOO_LARGE = "content_block_too_large"
CONTENT_BLOCKS_TOTAL_TOO_LARGE = "content_blocks_total_too_large"
CONTENT_TEXT_REQUIRED = "content_text_required"

# ─── 限额（字符数；base64 串长度，不是解码后字节数） ───

MAX_CONTENT_BLOCKS = 8
MAX_IMAGE_BLOCKS = 4
MAX_VIDEO_BLOCKS = 1
MAX_BLOCK_BASE64_CHARS = 4 * 1024 * 1024
MAX_TOTAL_BASE64_CHARS = 8 * 1024 * 1024
MAX_VIDEO_FRAMES = 4
MAX_FRAME_BASE64_CHARS = int(1.5 * 1024 * 1024)
MAX_FRAMES_TOTAL_CHARS = 4 * 1024 * 1024

ALLOWED_BLOCK_TYPES = frozenset({"text", "image", "audio", "file", "video"})
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
ALLOWED_VIDEO_MIME_PREFIX = "video/"


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


def _payload_chars(block: dict[str, Any]) -> int:
    return sum(len(payload) for payload in _sized_payloads(block))


def _has_attachment(block: dict[str, Any]) -> bool:
    value = block.get("attachment_id")
    return isinstance(value, str) and bool(value)


def _validate_attachment_id(block: dict[str, Any], label: str) -> bool:
    """块上出现 ``attachment_id`` 时校验其 sha256 hex 形状。"""
    if "attachment_id" not in block:
        return False
    value = block.get("attachment_id")
    if value is None:
        return False
    if not is_valid_attachment_id(value):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} attachment_id must be a sha256 hex string",
        )
    return True


def _require_source(block: dict[str, Any], label: str) -> None:
    """``image`` / ``file`` 至少带一种载体（attachment_id / base64 / url）。"""
    if _has_attachment(block):
        return
    for key in ("base64", "url"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return
    raise StableError(
        CONTENT_BLOCKS_MALFORMED,
        f"{label} requires attachment_id, base64 or url",
    )


def _require_mime(block: dict[str, Any], label: str, what: str) -> str:
    mime_type = block.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type:
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} {what} requires mime_type",
        )
    return mime_type


def _validate_image_block(block: dict[str, Any], label: str) -> None:
    """image 块（顶层 image 与 video 帧共用）。"""
    _validate_attachment_id(block, label)
    _require_source(block, label)
    mime_type = _require_mime(block, label, "image")
    if mime_type.lower() not in ALLOWED_IMAGE_MIME_TYPES:
        raise StableError(
            UNSUPPORTED_BLOCK_TYPE,
            f"{label} unsupported image mime_type={mime_type!r}",
        )


def _optional_number(block: dict[str, Any], key: str, label: str) -> None:
    if key not in block or block.get(key) is None:
        return
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} {key} must be a non-negative number",
        )


def _optional_dimension(block: dict[str, Any], key: str, label: str) -> None:
    if key not in block or block.get(key) is None:
        return
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} {key} must be a positive integer",
        )


def _validate_video_frames(block: dict[str, Any], label: str) -> int:
    """校验 video 的帧（image 块）；返回帧内联载荷总字符数。"""
    frames = block.get("frames")
    if frames is None:
        return 0
    if not isinstance(frames, list):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} frames must be an array",
        )
    if len(frames) > MAX_VIDEO_FRAMES:
        raise StableError(
            CONTENT_BLOCKS_TOTAL_TOO_LARGE,
            f"{label} at most {MAX_VIDEO_FRAMES} frames",
        )
    total_chars = 0
    for frame_index, frame in enumerate(frames):
        frame_label = f"{label} frame {frame_index}"
        if not isinstance(frame, dict):
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"{frame_label} must be an object",
            )
        if frame.get("type") != "image":
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"{frame_label} must be an image block",
            )
        _validate_image_block(frame, frame_label)
        frame_chars = _payload_chars(frame)
        if frame_chars > MAX_FRAME_BASE64_CHARS:
            raise StableError(
                CONTENT_BLOCK_TOO_LARGE,
                f"{frame_label} payload exceeds {MAX_FRAME_BASE64_CHARS} chars",
            )
        total_chars += frame_chars
    if total_chars > MAX_FRAMES_TOTAL_CHARS:
        raise StableError(
            CONTENT_BLOCKS_TOTAL_TOO_LARGE,
            f"{label} frames payload exceeds {MAX_FRAMES_TOTAL_CHARS} chars",
        )
    return total_chars


def _validate_video_block(block: dict[str, Any], label: str) -> int:
    """video 块：必须 attachment_id、不内联字节、帧受独立限额。"""
    if not _has_attachment(block):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} video requires attachment_id",
        )
    for key in ("base64", "data"):
        value = block.get(key)
        if isinstance(value, str) and value:
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"{label} video must not inline {key} (use attachment_id)",
            )
    url = block.get("url")
    if isinstance(url, str) and url.startswith("data:"):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} video must not inline bytes in url (use attachment_id)",
        )
    mime_type = _require_mime(block, label, "video")
    if not mime_type.lower().startswith(ALLOWED_VIDEO_MIME_PREFIX):
        raise StableError(
            UNSUPPORTED_BLOCK_TYPE,
            f"{label} unsupported video mime_type={mime_type!r}",
        )
    filename = block.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise StableError(
            CONTENT_BLOCKS_MALFORMED,
            f"{label} filename must be a string",
        )
    _optional_number(block, "duration_s", label)
    _optional_dimension(block, "width", label)
    _optional_dimension(block, "height", label)
    return _validate_video_frames(block, label)


def validate_content_blocks(raw: object) -> list[dict[str, Any]] | None:
    """校验并规范化 ``content_blocks``。

    Args:
        raw: wire 上收到的原始值（``None`` / 数组 / 任意非法值）。

    Returns:
        规范化后的块列表（元素为浅拷贝的 dict，未知字段原样保留，便于
        后续切片透传）；``None`` / 空数组归一为 ``None``，表示本条消息
        不带内容块。

    Raises:
        StableError: 形状/类型/数量/体积任一不满足本模块限额。
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
    video_count = 0
    total_chars = 0
    for index, item in enumerate(raw):
        label = f"block {index}"
        if not isinstance(item, dict):
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"{label} must be an object",
            )
        block_type = item.get("type")
        if not isinstance(block_type, str) or not block_type:
            raise StableError(
                CONTENT_BLOCKS_MALFORMED,
                f"{label} missing type",
            )
        if block_type not in ALLOWED_BLOCK_TYPES:
            raise StableError(
                UNSUPPORTED_BLOCK_TYPE,
                f"{label} type={block_type!r}",
            )

        block = dict(item)
        _validate_attachment_id(block, label)
        block_chars = 0
        if block_type == "text":
            if not isinstance(block.get("text"), str):
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"{label} text must be a string",
                )
        elif block_type == "image":
            image_count += 1
            if image_count > MAX_IMAGE_BLOCKS:
                raise StableError(
                    CONTENT_BLOCKS_TOTAL_TOO_LARGE,
                    f"at most {MAX_IMAGE_BLOCKS} image blocks per message",
                )
            _validate_image_block(block, label)
        elif block_type == "audio":
            _require_mime(block, label, "audio")
            if not _has_attachment(block) and not (
                isinstance(block.get("data"), str) and block.get("data")
            ):
                raise StableError(
                    CONTENT_BLOCKS_MALFORMED,
                    f"{label} audio requires data or attachment_id",
                )
        elif block_type == "video":
            video_count += 1
            if video_count > MAX_VIDEO_BLOCKS:
                raise StableError(
                    CONTENT_BLOCKS_TOTAL_TOO_LARGE,
                    f"at most {MAX_VIDEO_BLOCKS} video block per message",
                )
            block_chars = _validate_video_block(block, label)
        else:  # file
            _require_source(block, label)

        block_chars += _payload_chars(block)
        # video 自身不内联字节（帧限额在 _validate_video_block 内单独裁决），
        # 其余块按单块上限校验。
        if block_type != "video" and block_chars > MAX_BLOCK_BASE64_CHARS:
            raise StableError(
                CONTENT_BLOCK_TOO_LARGE,
                f"{label} payload exceeds {MAX_BLOCK_BASE64_CHARS} chars",
            )
        total_chars += block_chars
        blocks.append(block)

    if total_chars > MAX_TOTAL_BASE64_CHARS:
        raise StableError(
            CONTENT_BLOCKS_TOTAL_TOO_LARGE,
            f"content_blocks payload exceeds {MAX_TOTAL_BASE64_CHARS} chars",
        )
    return blocks
