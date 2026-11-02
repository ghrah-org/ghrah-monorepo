# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""把服务器返回的调用结果投影成框架可用的形状。

两条规则比其余细节都重要：

**``is_error`` 必须变成真失败。** 服务器用这个字段表示「工具执行了但结果是错误」。
把它包装成「成功且带一个 error 字段」会让模型以为调用成功了，于是继续沿着错误的
前提往下推理。

**非文本内容必须显式降级，不能静默丢弃。** 本框架目前没有附件存储，图像与音频的
原始字节无处安放——塞进结果会随会话记录每轮重发。正确做法是留下一条诊断文本，
让模型知道「这里本来有内容但拿不到」，而不是让它以为服务器什么都没返回。

另外，本模块**只提供结构化输出**，不决定最终措辞：投影后由上层渲染成给模型看的
文本。这样「把哪一份内容放进上下文」是单一决定，不会出现同一份数据既以文本又以
结构化形式各占一份的局面。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mcp import types

__all__ = ["ToolOutcome", "project_result", "structured_as_text"]

_IMAGE_PLACEHOLDER = (
    "[image unavailable: {mime}; durable image storage is not implemented; raw data dropped]"
)
_AUDIO_PLACEHOLDER = (
    "[audio unavailable: {mime}; durable audio storage is not implemented; raw data dropped]"
)
_BLOB_PLACEHOLDER = "[embedded resource unavailable: {mime}; binary payload dropped ({size} bytes)]"


@dataclass(frozen=True)
class ToolOutcome:
    """一次工具调用的投影结果。"""

    text: str
    """文本内容（多块按顺序以换行连接）。"""

    is_error: bool
    """服务器声明的错误状态。为真时上层必须按失败处理。"""

    structured: Mapping[str, Any] | None = None
    """机器可读的结构化输出。**不要与** :attr:`text` 同时放进上下文。"""

    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    """被降级处理的非文本内容说明。"""

    def render_for_model(self) -> str:
        """渲染成给模型看的文本。

        诊断信息附在正文之后：先给内容，再说明缺了什么，模型才不会把诊断当成
        工具的真实输出。
        """
        parts = [self.text] if self.text else []
        parts.extend(self.diagnostics)
        if not parts:
            return "(tool returned no content)"
        return "\n".join(parts)


def _text_block(block: types.TextContent) -> str:
    return block.text


def _image_block(block: types.ImageContent) -> str:
    return _IMAGE_PLACEHOLDER.format(mime=block.mime_type or "unknown")


def _audio_block(block: types.AudioContent) -> str:
    return _AUDIO_PLACEHOLDER.format(mime=block.mime_type or "unknown")


def _resource_link_block(block: types.ResourceLink) -> str:
    name = block.name or block.uri
    return f"Resource link: {name} ({block.uri})"


def _embedded_resource_block(block: types.EmbeddedResource) -> tuple[str, str | None]:
    """内嵌资源：文本内联，二进制降级。返回 ``(正文, 诊断)``。"""
    resource = block.resource
    if isinstance(resource, types.TextResourceContents):
        return resource.text, None
    if isinstance(resource, types.BlobResourceContents):
        blob = resource.blob
        size = _blob_size(blob)
        return "", _BLOB_PLACEHOLDER.format(mime=resource.mime_type or "unknown", size=size)
    return "", "[embedded resource unavailable: unrecognized payload]"


def _blob_size(blob: object) -> int:
    """估算二进制负载的字节数，仅用于诊断文案。"""
    if isinstance(blob, bytes | bytearray):
        return len(blob)
    if isinstance(blob, str):
        try:
            import base64

            return len(base64.b64decode(blob, validate=False))
        except Exception:  # noqa: BLE001 - 诊断用途，绝不影响主流程
            return len(blob)
    return 0


def project_result(result: types.CallToolResult) -> ToolOutcome:
    """把 ``CallToolResult`` 投影成 :class:`ToolOutcome`。"""
    texts: list[str] = []
    diagnostics: list[str] = []

    for block in result.content or ():
        if isinstance(block, types.TextContent):
            texts.append(_text_block(block))
        elif isinstance(block, types.ImageContent):
            diagnostics.append(_image_block(block))
        elif isinstance(block, types.AudioContent):
            diagnostics.append(_audio_block(block))
        elif isinstance(block, types.ResourceLink):
            texts.append(_resource_link_block(block))
        elif isinstance(block, types.EmbeddedResource):
            body, note = _embedded_resource_block(block)
            if body:
                texts.append(body)
            if note:
                diagnostics.append(note)
        else:  # pragma: no cover - 未来新增块类型
            diagnostics.append(f"[unsupported content block: {type(block).__name__}]")

    structured = result.structured_content
    if structured is not None and not isinstance(structured, Mapping):
        # 规范要求它是对象；拿到别的类型说明对端不合规，降级为文本而不是崩掉。
        diagnostics.append(f"[structured content was not an object: {type(structured).__name__}]")
        structured_out: Mapping[str, Any] | None = None
    else:
        structured_out = structured

    return ToolOutcome(
        text="\n".join(texts),
        is_error=bool(result.is_error),
        structured=structured_out,
        diagnostics=tuple(diagnostics),
    )


def structured_as_text(outcome: ToolOutcome) -> str:
    """把结构化输出序列化成紧凑文本，供需要时替代正文使用。"""
    if outcome.structured is None:
        return ""
    return json.dumps(outcome.structured, ensure_ascii=False, separators=(",", ":"), default=str)
