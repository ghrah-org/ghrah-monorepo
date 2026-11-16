# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class TextBlock:
    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ReasoningBlock:
    type: Literal["reasoning"] = "reasoning"
    reasoning: str = ""
    incomplete: bool = False


@dataclass
class ImageBlock:
    type: Literal["image"] = "image"
    url: str | None = None
    base64: str | None = None
    attachment_id: str | None = None
    mime_type: str | None = None


@dataclass
class AudioBlock:
    type: Literal["audio"] = "audio"
    data: str = ""
    attachment_id: str | None = None
    mime_type: str = ""


@dataclass
class FileBlock:
    type: Literal["file"] = "file"
    url: str | None = None
    base64: str | None = None
    attachment_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None


@dataclass
class VideoBlock:
    type: Literal["video"] = "video"
    url: str | None = None
    base64: str | None = None
    attachment_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    frames: list[ImageBlock] = field(default_factory=list)


@dataclass
class ToolCallBlock:
    type: Literal["tool_call"] = "tool_call"
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    name: str | None = None
    content: str = ""
    success: bool = True
    error: str | None = None


@dataclass
class ErrorBlock:
    type: Literal["error"] = "error"
    error_type: str = ""
    message: str = ""
    details: dict[str, Any] | None = None


@dataclass
class ToolCallChunkBlock:
    type: Literal["tool_call_chunk"] = "tool_call_chunk"
    index: int = 0
    id: str | None = None
    name: str | None = None
    arguments_chunk: str = ""


ContentBlock = (
    TextBlock
    | ReasoningBlock
    | ImageBlock
    | AudioBlock
    | FileBlock
    | VideoBlock
    | ToolCallBlock
    | ToolResultBlock
    | ErrorBlock
)

StreamingBlock = TextBlock | ToolCallChunkBlock

_BLOCK_TYPE_MAP: dict[str, type] = {
    "text": TextBlock,
    "reasoning": ReasoningBlock,
    "image": ImageBlock,
    "audio": AudioBlock,
    "file": FileBlock,
    "video": VideoBlock,
    "tool_call": ToolCallBlock,
    "tool_result": ToolResultBlock,
    "error": ErrorBlock,
    "tool_call_chunk": ToolCallChunkBlock,
}


def block_from_dict(data: dict[str, Any]) -> ContentBlock | ToolCallChunkBlock:
    block_type = data.get("type", "")
    cls = _BLOCK_TYPE_MAP.get(block_type)
    if cls is None:
        raise ValueError(f"Unknown block type: {block_type!r}")
    valid_fields = {f.name for f in cls.__dataclass_fields__.values() if f.name != "type"}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in data.items() if k in valid_fields}
    if cls is VideoBlock:
        # 帧是嵌套的 image 块（wire 上是 dict）；解析成 ImageBlock 以便
        # format 投影与 token 估算共用同一份数据。
        raw_frames = kwargs.get("frames", [])
        kwargs["frames"] = [
            frame
            for item in (raw_frames or [])
            if isinstance(item, dict)
            if isinstance(frame := block_from_dict(item), ImageBlock)
        ]
    return cls(**kwargs)  # type: ignore[no-any-return]


def blocks_from_dicts(content_blocks: list[dict[str, Any]]) -> list[ContentBlock]:
    """将序列化的内容块字典列表解析为 ContentBlock 列表。

    单项解析失败时回退为 TextBlock：未知块类型给出可识别的占位文本，
    已知类型但字段异常时取 text 或 reasoning 字段。这样调用方总能拿到
    结构化块而非抛异常，且「旧核收到新块类型」时降级是可见的，
    不会表现为「消息发出去了但什么也没有」。供 AgentMessage.to_chat_message()
    与 AgentActor.receive() 共用，避免两处解析逻辑漂移。
    """
    blocks: list[ContentBlock] = []
    for bd in content_blocks:
        try:
            blocks.append(block_from_dict(bd))  # type: ignore[arg-type]
        except ValueError, KeyError:
            raw_type = bd.get("type") if isinstance(bd, dict) else None
            if isinstance(raw_type, str) and raw_type:
                blocks.append(TextBlock(text=f"[不支持的媒体块: {raw_type}]"))
            else:
                blocks.append(TextBlock(text=str(bd.get("text", bd.get("reasoning", "")))))
    return blocks


def block_to_dict(block: ContentBlock | ToolCallChunkBlock) -> dict[str, Any]:
    result: dict[str, Any] = {"type": block.type}
    for f in block.__dataclass_fields__.values():
        if f.name == "type":
            continue
        val = getattr(block, f.name)
        if isinstance(block, VideoBlock) and f.name == "frames":
            if val:
                result["frames"] = [block_to_dict(frame) for frame in val]
            continue
        if val is not None and not (
            isinstance(val, (list, dict)) and len(val) == 0 and f.name == "arguments"
        ):
            result[f.name] = val
    return result


def expand_video_blocks(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """把 ``video`` 块投影为当前模型可消费的块。

    当前所有 provider 都没有原生视频输入：有 ``frames`` 时投影成 image 块，
    没有帧时投影成可识别的文本占位（不静默丢弃）。非 video 块原样保留。
    供 chat format 适配器在编码前统一调用，避免三家各写一套。
    """
    expanded: list[ContentBlock] = []
    for block in blocks:
        if not isinstance(block, VideoBlock):
            expanded.append(block)
            continue
        if block.frames:
            expanded.extend(block.frames)
            label = block.filename or "video"
            duration = f"，{block.duration_s:g}s" if block.duration_s else ""
            expanded.append(TextBlock(text=f"[视频附件: {label}{duration}，以下为抽帧]"))
        else:
            label = block.filename or "video"
            duration = f"，{block.duration_s:g}s" if block.duration_s else ""
            expanded.append(TextBlock(text=f"[视频附件: {label}{duration}]"))
    return expanded
