# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""内容块 wire 模型（消息内输入）。

这些模型是消息正文的**传输契约**：``ghrah-core`` 的领域块（``chat.content``）
是运行时内部模型，二者通过 dict 互相转换（``block_from_dict`` /
``block_to_dict``），不直接互相 import。

只覆盖「人 → Agent」的输入块（text / image / audio / file）。工具调用、
工具结果、错误等块是**结果侧**的块，由 ``AgentResponsePayload.content_blocks``
以 dict 承载，暂不在本模块建模。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

__all__ = [
    "AudioBlockPayload",
    "FileBlockPayload",
    "ImageBlockPayload",
    "InputContentBlock",
    "TextBlockPayload",
]


class TextBlockPayload(BaseModel):
    """文本块。"""

    type: Literal["text"] = "text"
    text: str = ""


class ImageBlockPayload(BaseModel):
    """图片块。

    ``url`` 与 ``base64`` 二选一；大小、MIME 与总量校验由 Subject 入口负责，
    协议层只保证形状。字段名与 Core ``ImageBlock`` 保持一致。
    """

    type: Literal["image"] = "image"
    url: str | None = None
    base64: str | None = None
    mime_type: str | None = None


class AudioBlockPayload(BaseModel):
    """音频块（字段名沿用 Core ``AudioBlock`` 的 ``data``，不改名为 base64）。"""

    type: Literal["audio"] = "audio"
    data: str = ""
    mime_type: str = ""


class FileBlockPayload(BaseModel):
    """文件块。"""

    type: Literal["file"] = "file"
    url: str | None = None
    base64: str | None = None
    mime_type: str | None = None
    filename: str | None = None


InputContentBlock = Annotated[
    TextBlockPayload | ImageBlockPayload | AudioBlockPayload | FileBlockPayload,
    Field(discriminator="type"),
]
"""输入内容块判别联合（按 ``type`` 字段）。"""
