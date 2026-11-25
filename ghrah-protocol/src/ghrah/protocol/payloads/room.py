# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Room 域载荷模型（成员、命令、结果与事件）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ghrah.protocol.enums import RoomStatus, RoomSubjectType

# ─── Room 载荷模型 ───


class RoomMember(BaseModel):
    """Room 成员（多对多：agent / human 均可为成员）。"""

    subject: str
    subject_type: RoomSubjectType
    subject_name: str = ""
    joined_at: str = ""


class RoomInfoPayload(BaseModel):
    """Room 信息载荷（room_get/room_list 结果项及 ROOM_* 事件回执）。

    对齐 ghrah-subject room/models.RoomRecord；seq_watermark 为该 room
    已分配的最大 RoomLog seq（Subject 单点分配）。
    """

    room_id: str
    project_id: str
    name: str
    status: RoomStatus = RoomStatus.ACTIVE
    members: list[RoomMember] = Field(default_factory=list)
    seq_watermark: int = 0
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None


class RoomLogEntryPayload(BaseModel):
    """RoomLog 节点载荷 = 最小信封 + 自由 map（不预置业务枚举 schema）。"""

    id: str
    room_id: str
    seq: int
    author: str
    author_type: RoomSubjectType
    timestamp: float
    data: dict[str, Any] = Field(default_factory=dict)


class RoomCreatePayload(BaseModel):
    """room_create 命令载荷。"""

    project_id: str
    name: str


class RoomListPayload(BaseModel):
    """room_list 命令载荷（可选 project/status 过滤）。"""

    project_id: str | None = None
    status: RoomStatus | None = None


class RoomIdPayload(BaseModel):
    """room_get / room_update / room_delete / room_get_members /
    room_get_log / room_send 等命令的 room_id 键控基类。"""

    room_id: str


class RoomUpdatePayload(BaseModel):
    """room_update 命令载荷（乐观锁）。"""

    room_id: str
    name: str | None = None
    expected_version: int | None = None


class RoomLifecyclePayload(RoomIdPayload):
    """room_archive / room_restore 命令载荷（乐观锁）。"""

    expected_version: int


class RoomDeletePayload(RoomLifecyclePayload):
    """room_delete 命令载荷。"""


class RoomJoinPayload(BaseModel):
    """room_join 命令载荷。"""

    room_id: str
    subject: str
    subject_type: RoomSubjectType
    subject_name: str = ""


class RoomLeavePayload(BaseModel):
    """room_leave 命令载荷。"""

    room_id: str
    subject: str


class RoomGetLogPayload(BaseModel):
    """room_get_log 命令载荷（since_seq 增量 / limit 截尾）。"""

    room_id: str
    since_seq: int | None = None
    limit: int = 100


class RoomSendPayload(BaseModel):
    """room_send 命令载荷（人类/Mock 公开路径；agent 路径由 Core send
    ability 解析后经同一命令面收敛，双调用方分流）。"""

    room_id: str
    author: str
    author_type: RoomSubjectType
    data: dict[str, Any] = Field(default_factory=dict)


class ChatActionListPayload(BaseModel):
    """chat_action_list 命令载荷：按 Room 恢复页面动作请求。"""

    room_id: str
    limit: int = Field(default=200, ge=1, le=1000)


class ChatActionSendPayload(BaseModel):
    """room_send.data.chat_action 的结构化内嵌模型。"""

    action: str = Field(min_length=1, pattern=r"\S")
    payload: dict[str, Any] = Field(default_factory=dict)


class RoomListResultPayload(BaseModel):
    """room_list 命令结果载荷。"""

    rooms: list[RoomInfoPayload] = Field(default_factory=list)
    count: int = 0


class RoomLogResultPayload(BaseModel):
    """room_get_log 命令结果载荷。"""

    entries: list[RoomLogEntryPayload] = Field(default_factory=list)
    count: int = 0  # ─── Room 事件载荷模型 ───


class ChatActionPayload(BaseModel):
    """已持久化的 Workbench 页面动作请求。"""

    action_id: str
    project_id: str
    room_id: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    room_log_id: str | None = None
    created_at: str
    updated_at: str


class ChatActionListResultPayload(BaseModel):
    """chat_action_list 命令结果。"""

    actions: list[ChatActionPayload] = Field(default_factory=list)
    count: int = 0


class RoomEventPayload(BaseModel):
    """room_created / room_updated 事件载荷（全量快照）。"""

    room: RoomInfoPayload


class RoomDeletedEventPayload(BaseModel):
    """room_deleted 事件载荷。"""

    room_id: str
    project_id: str


class RoomMemberEventPayload(BaseModel):
    """room_member_joined / room_member_left 事件载荷。

    joined 携带 member；left 仅 subject（成员已移出全量快照）。
    """

    room: RoomInfoPayload
    member: RoomMember | None = None
    subject: str | None = None


class RoomLogEventPayload(BaseModel):
    """room_log_appended 事件载荷。"""

    entry: RoomLogEntryPayload
