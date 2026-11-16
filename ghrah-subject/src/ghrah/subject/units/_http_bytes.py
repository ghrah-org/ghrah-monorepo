# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""观测端 REST 的字节流共用件（附件库与产物库共享）。

三件事：**有界读请求体**、**解析单段 Range**、**按区间流式吐文件**。
附件上传（POST 原始字节）与产物/工作区文件读取（GET + Range）都复用
这里，保证两个端点面的 Range/超限语义与错误形状一致。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import Request

from ghrah.subject.units.ability_control import AbilityApiError

__all__ = [
    "STREAM_CHUNK_BYTES",
    "content_disposition",
    "iter_file_range",
    "parse_byte_range",
    "read_body_limited",
]

logger = logging.getLogger(__name__)

STREAM_CHUNK_BYTES = 256 * 1024


async def read_body_limited(
    request: Request,
    max_bytes: int,
    *,
    too_large_code: str = "attachment_too_large",
    too_large_status: int = 413,
) -> bytes:
    """按块读取请求体，超过上限立即中止（不把超大流全量缓冲进内存）。

    Args:
        request: 当前请求。
        max_bytes: 上限字节数；``<= 0`` 表示不限制。
        too_large_code: 超限时返回的稳定错误码（附件上传用
            ``attachment_too_large``，产物发布请求体可传 ``invalid_request``）。
        too_large_status: 超限时返回的 HTTP 状态码（默认 413）。
    """
    if max_bytes <= 0:
        return await request.body()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise AbilityApiError(
                too_large_code,
                f"请求体超过上限：>{max_bytes} 字节",
                status=too_large_status,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def parse_byte_range(header: str, size: int) -> tuple[int, int] | None:
    """解析单段 ``Range: bytes=...``。

    Returns:
        ``(start, end)`` 闭区间；``None`` 表示忽略 Range 头（返回完整字节）。

    Raises:
        AbilityApiError: 语法合法但区间不可满足（416 ``range_not_satisfiable``）。
    """
    value = header.strip()
    if not value.lower().startswith("bytes="):
        return None
    spec = value[len("bytes=") :].strip()
    if not spec or "," in spec:
        # 多段 Range 不支持：按 HTTP 语义可忽略（返回完整内容）。
        return None
    start_raw, _, end_raw = spec.partition("-")
    start_raw = start_raw.strip()
    end_raw = end_raw.strip()
    try:
        if start_raw == "":
            # bytes=-N：最后 N 字节
            suffix = int(end_raw)
            if suffix <= 0:
                return None
            if suffix >= size:
                return (0, size - 1) if size else None
            return (size - suffix, size - 1)
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= size:
        raise AbilityApiError(
            "range_not_satisfiable",
            f"Range 超出内容长度：{header!r}（size={size}）",
            status=416,
        )
    return (start, min(end, size - 1))


def content_disposition(filename: str) -> str:
    """``Content-Disposition``：非 ASCII 文件名走 RFC 5987 编码。

    统一用 ``attachment``：观测端预览应 fetch 字节再走 blob（避免把
    不可信内容当同源页面直接渲染）。文件名为空时用 ``attachment`` 兜底。
    """
    name = filename or "attachment"
    return f"attachment; filename*=UTF-8''{quote(name)}"


def iter_file_range(path: Path, start: int, length: int) -> Iterator[bytes]:
    """从 ``start`` 起流式读取 ``length`` 字节（同步迭代器，starlette 走线程池）。"""
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
