# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Room 协议段测试。

对齐机制：fixtures 与 TS 侧 `packages/protocol/src/__snapshots__/Room*.json`
逐字节同源（protocol-align.spec.ts 的 zod 键集对齐消费同一份内容）——
Python 侧在此 pin wire 格式，双侧漂移任一侧测试即红。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghrah.protocol.types import (
    COMMAND_PAYLOAD_MAP,
    EVENT_PAYLOAD_MAP,
    ROOM_COMMANDS,
    CommandType,
    Envelope,
    EventType,
    RoomCreatePayload,
    RoomDeletedEventPayload,
    RoomDeletePayload,
    RoomEventPayload,
    RoomGetLogPayload,
    RoomIdPayload,
    RoomInfoPayload,
    RoomJoinPayload,
    RoomLeavePayload,
    RoomLifecyclePayload,
    RoomListPayload,
    RoomListResultPayload,
    RoomLogEntryPayload,
    RoomLogEventPayload,
    RoomLogResultPayload,
    RoomMember,
    RoomMemberEventPayload,
    RoomSendPayload,
    RoomStatus,
    RoomSubjectType,
    RoomUpdatePayload,
    envelope_from_dict,
)

# ─── 与 TS __snapshots__/Room*.json 同源的 fixtures ───

ROOM_MEMBER_AGENT = {
    "subject": "architect",
    "subject_type": "agent",
    "subject_name": "",
    "joined_at": "2026-08-25T00:00:00Z",
}

ROOM_INFO = {
    "room_id": "room-001",
    "project_id": "proj-001",
    "name": "architecture",
    "status": "active",
    "members": [ROOM_MEMBER_AGENT],
    "seq_watermark": 42,
    "version": 5,
    "created_at": "2026-08-25T00:00:00Z",
    "updated_at": "2026-08-25T01:00:00Z",
    "archived_at": None,
}

ROOM_LOG_ENTRY = {
    "id": "entry-001",
    "room_id": "room-001",
    "seq": 42,
    "author": "architect",
    "author_type": "agent",
    "timestamp": 1787600000.5,
    "data": {
        "message": "架构评审结论见 docs/adr-001.md",
        "format": "markdown",
    },
}


class TestRoomEnums:
    def test_room_commands_complete(self):
        assert ROOM_COMMANDS == frozenset(
            {
                "room_create",
                "room_list",
                "room_get",
                "room_update",
                "room_archive",
                "room_restore",
                "room_delete",
                "room_join",
                "room_leave",
                "room_get_members",
                "room_get_log",
                "room_send",
            }
        )

    def test_room_commands_registered_in_command_type(self):
        for cmd in ROOM_COMMANDS:
            assert cmd in CommandType._value2member_map_

    def test_room_events_eight(self):
        expected = {
            "room_created",
            "room_updated",
            "room_deleted",
            "room_archived",
            "room_restored",
            "room_member_joined",
            "room_member_left",
            "room_log_appended",
        }
        values = {
            EventType.ROOM_CREATED.value,
            EventType.ROOM_UPDATED.value,
            EventType.ROOM_ARCHIVED.value,
            EventType.ROOM_RESTORED.value,
            EventType.ROOM_DELETED.value,
            EventType.ROOM_MEMBER_JOINED.value,
            EventType.ROOM_MEMBER_LEFT.value,
            EventType.ROOM_LOG_APPENDED.value,
        }
        assert values == expected

    def test_subject_type_and_status_values(self):
        assert {e.value for e in RoomSubjectType} == {"agent", "human"}
        assert {e.value for e in RoomStatus} == {"active", "archived"}


class TestRoomPayloadRoundTrip:
    """model_validate(fixture) → model_dump() 与 fixture 全等（wire pin）。"""

    @pytest.mark.parametrize(
        ("cls", "fixture"),
        [
            (RoomMember, ROOM_MEMBER_AGENT),
            (RoomInfoPayload, ROOM_INFO),
            (RoomLogEntryPayload, ROOM_LOG_ENTRY),
            (RoomCreatePayload, {"project_id": "proj-001", "name": "architecture"}),
            (RoomListPayload, {"project_id": "proj-001", "status": "active"}),
            (RoomIdPayload, {"room_id": "room-001"}),
            (
                RoomUpdatePayload,
                {
                    "room_id": "room-001",
                    "name": "architecture-v2",
                    "expected_version": 5,
                },
            ),
            (RoomLifecyclePayload, {"room_id": "room-001", "expected_version": 5}),
            (RoomDeletePayload, {"room_id": "room-001", "expected_version": 5}),
            (
                RoomJoinPayload,
                {
                    "room_id": "room-001",
                    "subject": "frontend-dev",
                    "subject_type": "agent",
                    "subject_name": "",
                },
            ),
            (RoomLeavePayload, {"room_id": "room-001", "subject": "frontend-dev"}),
            (RoomGetLogPayload, {"room_id": "room-001", "since_seq": 40, "limit": 100}),
            (
                RoomSendPayload,
                {
                    "room_id": "room-001",
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "请评审最新架构草案"},
                    "delivery_policy": "queue",
                },
            ),
            (RoomListResultPayload, {"rooms": [ROOM_INFO], "count": 1}),
            (RoomLogResultPayload, {"entries": [ROOM_LOG_ENTRY], "count": 1}),
            (RoomEventPayload, {"room": ROOM_INFO}),
            (RoomDeletedEventPayload, {"room_id": "room-001", "project_id": "proj-001"}),
            (
                RoomMemberEventPayload,
                {
                    "room": {
                        **ROOM_INFO,
                        "members": [
                            ROOM_MEMBER_AGENT,
                            {
                                "subject": "frontend-dev",
                                "subject_type": "agent",
                                "subject_name": "",
                                "joined_at": "2026-08-25T01:00:00Z",
                            },
                        ],
                        "version": 6,
                    },
                    "member": {
                        "subject": "frontend-dev",
                        "subject_type": "agent",
                        "subject_name": "",
                        "joined_at": "2026-08-25T01:00:00Z",
                    },
                    "subject": None,
                },
            ),
            (RoomLogEventPayload, {"entry": ROOM_LOG_ENTRY}),
        ],
    )
    def test_round_trip_equal(self, cls, fixture):
        model = cls.model_validate(fixture)
        assert model.model_dump() == fixture

    def test_defaults_align_ts_optional_defaults(self):
        """TS 侧 .optional().default(...) 的键在 Python 侧同样落 wire。"""
        room = RoomInfoPayload(room_id="r", project_id="p", name="n")
        dumped = room.model_dump()
        assert dumped["status"] == "active"
        assert dumped["members"] == []
        assert dumped["seq_watermark"] == 0
        assert dumped["version"] == 1
        assert dumped["archived_at"] is None

        entry = RoomLogEntryPayload(
            id="e",
            room_id="r",
            seq=1,
            author="a",
            author_type=RoomSubjectType.AGENT,
            timestamp=1.0,
        )
        assert entry.model_dump()["data"] == {}

        log = RoomGetLogPayload(room_id="r")
        assert log.limit == 100


class TestRoomEnvelopeIntegration:
    def test_room_command_envelope_resolves_model(self):
        env = envelope_from_dict(
            {
                "type": "room_send",
                "payload": {
                    "room_id": "room-001",
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "hi"},
                },
            }
        )
        assert isinstance(env.payload, RoomSendPayload)
        assert env.payload.data["message"] == "hi"
        assert env.payload.delivery_policy == "queue"

    def test_room_send_rejects_unknown_delivery_policy(self):
        with pytest.raises(ValidationError, match="delivery_policy"):
            RoomSendPayload.model_validate(
                {
                    "room_id": "r",
                    "author": "reader",
                    "author_type": "human",
                    "delivery_policy": "now",
                }
            )

    def test_room_event_envelope_resolves_model(self):
        env = envelope_from_dict(
            {
                "type": "room_log_appended",
                "payload": {"entry": ROOM_LOG_ENTRY},
            }
        )
        assert isinstance(env.payload, RoomLogEventPayload)
        assert env.payload.entry.seq == 42

    def test_all_room_commands_have_payload_registration(self):
        for cmd in ROOM_COMMANDS:
            command = next(c for c in CommandType if c.value == cmd)
            assert command in COMMAND_PAYLOAD_MAP, f"missing: {cmd}"

    def test_all_room_events_have_payload_registration(self):
        for event_type in (
            EventType.ROOM_CREATED,
            EventType.ROOM_UPDATED,
            EventType.ROOM_ARCHIVED,
            EventType.ROOM_RESTORED,
            EventType.ROOM_DELETED,
            EventType.ROOM_MEMBER_JOINED,
            EventType.ROOM_MEMBER_LEFT,
            EventType.ROOM_LOG_APPENDED,
        ):
            assert event_type in EVENT_PAYLOAD_MAP

    def test_room_get_members_uses_room_id_payload(self):
        assert COMMAND_PAYLOAD_MAP[CommandType.ROOM_GET_MEMBERS] is RoomIdPayload

    def test_room_lifecycle_commands_use_versioned_payload(self):
        assert COMMAND_PAYLOAD_MAP[CommandType.ROOM_ARCHIVE] is RoomLifecyclePayload
        assert COMMAND_PAYLOAD_MAP[CommandType.ROOM_RESTORE] is RoomLifecyclePayload

    def test_room_payload_validation_error_propagates(self):
        """已知 room 命令 + 缺必填字段 → ValidationError（对齐 envelope 收窄语义）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            envelope_from_dict(
                {
                    "type": "room_send",
                    "payload": {"bogus": True},
                }
            )


class TestEnvelopeRoomSerialization:
    def test_envelope_model_dump_keeps_room_fields(self):
        payload = RoomSendPayload.model_validate(
            {
                "room_id": "room-001",
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "请评审", "targets": ["architect"]},
            }
        )
        env = Envelope(type="room_send", payload=payload)
        dumped = env.model_dump()
        assert dumped["payload"]["data"]["targets"] == ["architect"]
