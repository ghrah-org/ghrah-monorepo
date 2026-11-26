# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""观察端轮次卡与轮次详情的只读视图。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghrah.protocol.views.core import NodeView, TurnView

__all__ = [
    "MessageFactsView",
    "RoomFactsQuery",
    "RoomMessageReference",
    "RoomNodeReference",
    "RoomNodeTurnView",
    "RoomTranscriptView",
    "TranscriptDeliveryView",
    "TranscriptEntryView",
    "TranscriptFactsView",
    "TurnCardView",
    "TurnDetailView",
    "TurnStatsView",
]


class TurnStatsView(BaseModel):
    """同一轮次中已持久化的节点用量与审批等待时间。"""

    model_config = ConfigDict(extra="forbid")

    steps: int = 0
    node_count: int = 0
    token_input: int = 0
    token_output: int = 0
    token_total: int = 0
    token_cache_read: int = 0
    token_cache_write: int = 0
    cache_hit_rate: float | None = None
    duration_ms: int | None = None
    waiting_ms: int = 0


class TurnCardView(TurnView):
    """含统计与导航锚点的轮次视图。"""

    injections: int = Field(default=0, exclude=True)
    turn_index: int
    opener_message_id: str | None = None
    opener_entry_id: str | None = None
    parent_turn_id: str | None = None
    stats: TurnStatsView


class TurnDetailView(BaseModel):
    """单轮卡片及其按时间与节点 ID 排序的节点。"""

    model_config = ConfigDict(extra="forbid")

    turn: TurnCardView
    nodes: list[NodeView]


class RoomMessageReference(BaseModel):
    """Room 日志中记录的消息引用及其作者。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    author: str


class RoomNodeReference(BaseModel):
    """旧 Room 日志中记录的链节点引用及其 Agent。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    agent_id: str


class RoomFactsQuery(BaseModel):
    """经 Subject 校验的 Room 页与相应的 Core 引用。"""

    model_config = ConfigDict(extra="forbid")

    room_id: str = Field(min_length=1)
    messages: list[RoomMessageReference] = Field(default_factory=list)
    nodes: list[RoomNodeReference] = Field(default_factory=list)
    active_agent_ids: list[str] = Field(default_factory=list)
    include_turns: bool = False

    def matches_message(self, metadata: dict[str, Any]) -> bool:
        """读取消息元数据中的 Room 归属，兼容早期单房间字段。"""
        rooms = metadata.get("room_ids") or [metadata.get("room_id")]
        return isinstance(rooms, list) and self.room_id in rooms

    def matches_legacy_node(self, metadata: dict[str, Any], messages: Any) -> bool:
        """按日志投影使用的归属规则校验旧链节点。"""
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                context = message.get("metadata")
                room_id = context.get("room_id") if isinstance(context, dict) else None
                if isinstance(room_id, str) and room_id:
                    return room_id == self.room_id
        context = metadata.get("delivery_context")
        room_id = context.get("room_id") if isinstance(context, dict) else None
        return isinstance(room_id, str) and room_id == self.room_id


class MessageFactsView(BaseModel):
    """经过 Room 与作者归属校验的消息正文及可展示数据。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    sender_id: str
    content: str
    content_blocks: list[dict[str, Any]] | None = None
    targets: list[str] = Field(default_factory=list)
    room_data: dict[str, Any] = Field(default_factory=dict)
    room_has_message: bool = True
    turn_id: str | None = None


class TranscriptDeliveryView(BaseModel):
    """Room 页内消息的投递状态和会话队列位置。"""

    model_config = ConfigDict(extra="forbid")

    delivery_id: str
    message_id: str
    recipient_id: str
    state: str
    error: str | None = None
    blocked_reason: str | None = None
    turn_id: str | None = None
    delivery_policy: str = "queue"
    display_state: str
    queue_position: int = 0
    queue_reason: str | None = None
    cancelable: bool = False
    injection_step: Annotated[int, Field(gt=0)] | None = None


class RoomNodeTurnView(RoomNodeReference):
    """旧 Room 日志所引用节点的轮次。"""

    turn_id: str


class TranscriptFactsView(BaseModel):
    """从同一个 Core 只读快照取出的 Room 页数据。"""

    model_config = ConfigDict(extra="forbid")

    messages: list[MessageFactsView] = Field(default_factory=list)
    deliveries: list[TranscriptDeliveryView] = Field(default_factory=list)
    node_turns: list[RoomNodeTurnView] = Field(default_factory=list)
    turns: list[TurnCardView] = Field(default_factory=list)


class TranscriptEntryView(BaseModel):
    """单条 Room 日志及它引用的消息和投递。"""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    seq: int
    author: str
    author_type: Literal["agent", "human"]
    timestamp: float
    message_id: str | None = None
    content: str = ""
    content_blocks: list[dict[str, Any]] | None = None
    targets: list[str] = Field(default_factory=list)
    deliveries: list[TranscriptDeliveryView] = Field(default_factory=list)
    turn_id: str | None = None
    insert_before_turn_id: str | None = None
    injection_step: Annotated[int, Field(gt=0)] | None = None
    human_state: str | None = None
    node_id: str | None = None
    ability: str | None = None


class RoomTranscriptView(BaseModel):
    """Room 的历史页或正向增量页。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    room_id: str
    entries: list[TranscriptEntryView]
    turns: list[TurnCardView]
    next_before_seq: int | None = None
    next_since_seq: int | None = None
    latest_seq: int
    has_more: bool
    read_at: str
