# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""将 Room 的日志页和 Core 权威事实按引用关联。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ghrah.protocol.types import RoomSubjectType
from ghrah.protocol.views.observer_room import (
    MessageFactsView,
    RoomFactsQuery,
    RoomMessageReference,
    RoomNodeReference,
    RoomTranscriptView,
    TranscriptDeliveryView,
    TranscriptEntryView,
    TranscriptFactsView,
)

from ghrah.subject.room.models import RoomLogRecord, RoomRecord, now_iso
from ghrah.subject.room.store import RoomLogPage


def room_facts_query(
    room: RoomRecord,
    entries: Sequence[RoomLogRecord],
    *,
    include_turns: bool = False,
) -> RoomFactsQuery:
    """从当前 Room 页提取引用，保留每个引用在日志中的作者。"""
    messages = []
    nodes = []
    for entry in entries:
        if isinstance(message_id := entry.data.get("mm_message_id"), str) and message_id:
            messages.append(RoomMessageReference(message_id=message_id, author=entry.author))
        if include_turns and entry.author_type == RoomSubjectType.AGENT:
            if isinstance(node_id := entry.data.get("node_id"), str) and node_id:
                nodes.append(RoomNodeReference(node_id=node_id, agent_id=entry.author))
    return RoomFactsQuery(
        room_id=room.room_id,
        messages=messages,
        nodes=nodes,
        active_agent_ids=[
            member.subject
            for member in room.members
            if include_turns and member.subject_type == RoomSubjectType.AGENT
        ],
        include_turns=include_turns,
    )


class RoomLogFacts:
    """按消息、投递和节点索引一次 Core 批读的结果。"""

    def __init__(self, facts: TranscriptFactsView) -> None:
        self._messages = {message.message_id: message for message in facts.messages}
        self._deliveries = defaultdict(list)
        for delivery in facts.deliveries:
            self._deliveries[delivery.message_id].append(delivery)
        self._node_turns = {
            (node.node_id, node.agent_id): node.turn_id for node in facts.node_turns
        }
        self._turns = facts.turns

    def _message(self, record: RoomLogRecord) -> MessageFactsView | None:
        message_id = record.data.get("mm_message_id")
        message = self._messages.get(message_id) if isinstance(message_id, str) else None
        if message is not None and message.sender_id in (record.author, f"human:{record.author}"):
            return message
        return None

    def hydrate(self, record: RoomLogRecord) -> RoomLogRecord:
        """为旧命令和回执组装原有 data 形状。"""
        if not record.data.get("mm_message_id"):
            return record
        message = self._message(record)
        if message is None:
            raise ValueError("Room message reference is missing or has a different author or Room")
        data = {**message.room_data, **record.data, "via": "message_manager"}
        if message.content_blocks is not None:
            data["content_blocks"] = message.content_blocks
        if message.room_has_message:
            data["message"] = message.content
        elif message.content_blocks is not None:
            data.pop("message", None)
        if message.targets:
            data["targets"] = message.targets
        data["deliveries"] = [
            d.model_dump(
                include={"delivery_id", "recipient_id", "state", "error", "blocked_reason"}
            )
            for d in self._deliveries.get(message.message_id, [])
        ]
        return record.model_copy(update={"data": data})

    def _entry(self, record: RoomLogRecord) -> TranscriptEntryView:
        data = record.data
        message = self._message(record)
        message_id = data.get("mm_message_id")
        if message_id:
            # 引用缺失时仍保留日志和游标，正文只从经过校验的权威消息取得。
            data = self.hydrate(record).data if message is not None else {}
        node_id = data.get("node_id")
        if not isinstance(node_id, str):
            node_id = None
        deliveries = self._deliveries.get(message.message_id, []) if message else []
        human = record.author_type == RoomSubjectType.HUMAN
        injected = (
            next((d for d in deliveries if d.turn_id and d.delivery_policy == "inject"), None)
            if human
            else None
        )
        human_state = _human_state(deliveries) if human else None
        ability = data.get("ability")
        return TranscriptEntryView(
            entry_id=record.id,
            seq=record.seq,
            author=record.author,
            author_type=record.author_type.value,
            timestamp=record.timestamp,
            message_id=message_id if isinstance(message_id, str) and message_id else node_id,
            content=str(data.get("message") or ""),
            content_blocks=data.get("content_blocks"),
            targets=data.get("targets") or [],
            deliveries=deliveries,
            turn_id=(message.turn_id if message else self._node_turns.get((node_id, record.author)))
            if record.author_type == RoomSubjectType.AGENT
            else None,
            insert_before_turn_id=injected.turn_id if injected else None,
            injection_step=injected.injection_step if injected else None,
            human_state=human_state,
            node_id=node_id,
            ability=ability if isinstance(ability, str) else None,
        )

    def transcript(self, page: RoomLogPage) -> RoomTranscriptView:
        """组装历史页，同时保留 Subject 的日志游标。"""
        entries = [self._entry(record) for record in page.entries]
        opener_entries = {
            message.message_id: record.id
            for record in page.entries
            if (message := self._message(record)) is not None
        }
        return RoomTranscriptView(
            project_id=page.room.project_id,
            room_id=page.room.room_id,
            entries=entries,
            turns=[
                turn.model_copy(
                    update={"opener_entry_id": opener_entries.get(turn.opener_message_id)}
                )
                for turn in self._turns
            ],
            next_before_seq=page.next_before_seq,
            next_since_seq=page.next_since_seq,
            latest_seq=page.latest_seq,
            has_more=page.has_more,
            read_at=now_iso(),
        )


def _human_state(deliveries: list[TranscriptDeliveryView]) -> str | None:
    if not deliveries:
        return None
    states = {d.display_state for d in deliveries}
    if states & {"injected", "consumed"} or any(d.injection_step is not None for d in deliveries):
        return "injected"
    if states & {"queued", "blocked"}:
        return "queued"
    if states & {"failed", "interrupted", "parked", "cancelled"}:
        return "failed"
    return "sent"
