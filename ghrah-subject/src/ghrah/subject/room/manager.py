# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RoomManager：Room 命令编排层（12 命令 + 生命周期守卫 + send 收敛点）。

范式对齐 task/manager.TaskManager：``_HANDLERS`` 表 + ``_ok/_err`` +
ValidationError 收窄；纯 store + 回调，不依赖 SubjectContext，便于独立单测。

生产装配通过 publish 回调先由 MM 持久受理，再经 append_log 写入引用日志。
日志读取与事件展示从 Core 读通正文，消息正文的权威属于 MM。
未装配 publish 的独立集成仍保留 append_log 和 human deliver 回调；
持久引用日志不再触发该投递回调，避免重复发送。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
from ghrah.protocol.types import (
    RoomCreatePayload,
    RoomDeletePayload,
    RoomGetLogPayload,
    RoomJoinPayload,
    RoomLeavePayload,
    RoomLifecyclePayload,
    RoomListPayload,
    RoomSendPayload,
    RoomStatus,
    RoomSubjectType,
    RoomUpdatePayload,
)
from pydantic import ValidationError

from ghrah.subject.project.errors import ProjectArchivedError
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.room.models import (
    RoomLogRecord,
    RoomRecord,
    make_room_log_record,
    make_room_member,
    make_room_record,
    now_iso,
)
from ghrah.subject.room.store import (
    ConcurrentModificationError,
    RoomArchivedError,
    RoomStore,
)

logger = logging.getLogger(__name__)

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
ProjectExists = Callable[[str], Awaitable[bool]]
ProjectLookup = Callable[[str], Awaitable[dict[str, Any] | None]]
Deliver = Callable[[str, str, str, str], Awaitable[dict[str, Any]]]
"""deliver(target, sender, content, room_id) → command_result。

room_id 随投递写入消息 metadata（AgentMessage.metadata.room_id）→ 链节点
messages_delta，供回复归属推导（Room Filter）消费。
"""

__all__ = ["RoomManager"]


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(msg: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": msg}


def _err_code(code: str, detail: str = "") -> dict[str, Any]:
    """Return a stable machine code separately from its display detail."""
    return {
        "success": False,
        "data": None,
        "error": code,
        "error_detail": detail,
    }


class RoomManager:
    """Room 命令编排：生命周期 + 成员 + seq 单点分配 + send 收敛。"""

    def __init__(
        self,
        store: RoomStore,
        *,
        on_event: OnEvent | None = None,
        project_exists: ProjectExists | None = None,
        project_get: ProjectLookup | None = None,
        deliver: Deliver | None = None,
        publish: Callable[[RoomRecord, RoomSendPayload], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._store = store
        self._on_event = on_event
        self._project_exists = project_exists
        self._project_get = project_get
        self._deliver = deliver
        self._publish = publish

    @property
    def store(self) -> RoomStore:
        return self._store

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = _HANDLERS.get(command)
        if handler is None:
            return _err(f"unknown command: {command}")
        try:
            return await handler(self, payload)
        except ValidationError as e:
            missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
            if missing:
                return _err(f"{missing[0]} required")
            return _err(f"invalid payload: {e}")
        except RoomArchivedError:
            return _err("resource_archived")
        except ProjectArchivedError:
            return _err("project_archived")

    # ─── helpers ───

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        await self._on_event(event_type, payload)

    async def _emit_room(self, event_type: str, room: RoomRecord) -> None:
        await self._emit(event_type, {"room": room.to_wire()})

    async def _project_access(
        self, project_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve a Project and enforce the parent archive boundary."""

        if self._project_get is not None:
            project = await self._project_get(project_id)
            if project is None:
                return None, _err(f"project not found: {project_id}")
            # deleted_at is accepted only as the legacy archive marker.
            if project.get("archived_at") or project.get("deleted_at"):
                return project, _err("project_archived")
            return project, None
        if self._project_exists is not None and not await self._project_exists(project_id):
            return None, _err(f"project not found: {project_id}")
        return None, None

    async def _require_room_access(
        self, room_id: str, *, allow_archived: bool = False
    ) -> tuple[RoomRecord | None, dict[str, Any] | None, dict[str, Any] | None]:
        room = await self._store.get(room_id)
        if room is None:
            return None, None, _err(f"room not found: {room_id}")
        project, project_error = await self._project_access(room.project_id)
        if project_error is not None:
            return room, project, project_error
        if not allow_archived and room.status == RoomStatus.ARCHIVED:
            return room, project, _err("resource_archived")
        return room, project, None

    # ─── 命令 handlers ───

    async def _handle_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomCreatePayload.model_validate(payload)
        name = p.name.strip()
        if not name:
            return _err("name required")
        _project, project_error = await self._project_access(p.project_id)
        if project_error is not None:
            return project_error
        record = make_room_record(project_id=p.project_id, name=name)
        await self._store.upsert(record)
        await self._emit_room("room_created", record)
        return _ok({"room": record.to_wire()})

    async def _handle_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomListPayload.model_validate(payload)
        if p.project_id is not None:
            _project, project_error = await self._project_access(p.project_id)
            if project_error is not None:
                return project_error
        records = await self._store.list(
            project_id=p.project_id,
            status=p.status.value if p.status is not None else None,
        )
        rooms: list[dict[str, Any]] = []
        for record in records:
            _project, project_error = await self._project_access(record.project_id)
            if project_error is None:
                rooms.append(record.to_wire())
        return _ok({"rooms": rooms, "count": len(rooms)})

    async def _handle_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        room_id = payload.get("room_id", "")
        existing, _project, error = await self._require_room_access(room_id, allow_archived=True)
        if error is not None:
            return error
        assert existing is not None
        return _ok({"room": existing.to_wire()})

    async def _handle_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomUpdatePayload.model_validate(payload)
        _existing, _project, error = await self._require_room_access(p.room_id)
        if error is not None:
            return error

        def mutator(r: RoomRecord) -> RoomRecord:
            if r.status == RoomStatus.ARCHIVED:
                raise RoomArchivedError(r.room_id)
            updates: dict[str, Any] = {"updated_at": now_iso()}
            if p.name is not None:
                updates["name"] = p.name
            return r.model_copy(update=updates)

        try:
            updated = await self._store.update(
                p.room_id, expected_version=p.expected_version, mutator=mutator
            )
        except ConcurrentModificationError:
            return _err_code(
                "room_version_conflict",
                f"version conflict: expected {p.expected_version}",
            )
        if updated is None:
            return _err(f"room not found: {p.room_id}")
        await self._emit_room("room_updated", updated)
        return _ok({"room": updated.to_wire()})

    async def _handle_archive(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomLifecyclePayload.model_validate(payload)
        existing, _project, error = await self._require_room_access(p.room_id, allow_archived=True)
        if error is not None:
            return error
        assert existing is not None
        if existing.status == RoomStatus.ARCHIVED:
            return _ok({"room": existing.to_wire()})

        def mutator(room: RoomRecord) -> RoomRecord:
            return room.model_copy(
                update={
                    "status": RoomStatus.ARCHIVED,
                    "archived_at": now_iso(),
                    "updated_at": now_iso(),
                }
            )

        try:
            updated = await self._store.update(
                p.room_id, expected_version=p.expected_version, mutator=mutator
            )
        except ConcurrentModificationError:
            return _err_code(
                "room_version_conflict",
                f"version conflict: expected {p.expected_version}",
            )
        if updated is None:
            return _err(f"room not found: {p.room_id}")
        await self._emit_room("room_archived", updated)
        return _ok({"room": updated.to_wire()})

    async def _handle_restore(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomLifecyclePayload.model_validate(payload)
        existing, _project, error = await self._require_room_access(p.room_id, allow_archived=True)
        if error is not None:
            return error
        assert existing is not None
        if existing.status == RoomStatus.ACTIVE:
            return _ok({"room": existing.to_wire()})

        def mutator(room: RoomRecord) -> RoomRecord:
            return room.model_copy(
                update={
                    "status": RoomStatus.ACTIVE,
                    "archived_at": None,
                    "updated_at": now_iso(),
                }
            )

        try:
            updated = await self._store.update(
                p.room_id, expected_version=p.expected_version, mutator=mutator
            )
        except ConcurrentModificationError:
            return _err_code(
                "room_version_conflict",
                f"version conflict: expected {p.expected_version}",
            )
        if updated is None:
            return _err(f"room not found: {p.room_id}")
        await self._emit_room("room_restored", updated)
        return _ok({"room": updated.to_wire()})

    async def _handle_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomDeletePayload.model_validate(payload)
        existing, _project, error = await self._require_room_access(p.room_id, allow_archived=True)
        if error is not None:
            return error
        assert existing is not None
        try:
            deleted = await self._store.delete(p.room_id, expected_version=p.expected_version)
        except ConcurrentModificationError:
            return _err_code(
                "room_version_conflict",
                f"version conflict: expected {p.expected_version}",
            )
        if not deleted:
            return _err(f"room not found: {p.room_id}")
        await self._emit(
            "room_deleted",
            {"room_id": existing.room_id, "project_id": existing.project_id},
        )
        return _ok({"room_id": existing.room_id, "project_id": existing.project_id})

    async def _handle_join(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomJoinPayload.model_validate(payload)
        if not p.subject.strip():
            return _err("subject required")
        existing, project, error = await self._require_room_access(p.room_id)
        if error is not None:
            return error
        assert existing is not None
        if p.subject_type == RoomSubjectType.AGENT and project is not None:
            agents = project.get("agents") or []
            matches = [
                agent
                for agent in agents
                if agent.get("agent_id") == p.subject or agent.get("name") == p.subject
            ]
            if len(matches) != 1:
                return _err("agent_project_mismatch")
        if any(m.subject == p.subject for m in existing.members):
            # 幂等 join：已在 room → 成功返回，不发事件（对齐 mock 契约）
            return _ok({"room": existing.to_wire()})

        member = make_room_member(p.subject, p.subject_type, p.subject_name)

        def mutator(r: RoomRecord) -> RoomRecord:
            if r.status == RoomStatus.ARCHIVED:
                raise RoomArchivedError(r.room_id)
            return r.model_copy(update={"members": [*r.members, member], "updated_at": now_iso()})

        updated = await self._store.update(p.room_id, expected_version=None, mutator=mutator)
        if updated is None:
            return _err(f"room not found: {p.room_id}")
        await self._emit(
            "room_member_joined",
            {"room": updated.to_wire(), "member": member.model_dump(mode="json")},
        )
        return _ok({"room": updated.to_wire()})

    async def _handle_leave(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomLeavePayload.model_validate(payload)
        existing, _project, error = await self._require_room_access(p.room_id)
        if error is not None:
            return error
        assert existing is not None
        if not any(m.subject == p.subject for m in existing.members):
            return _err(f"subject not in room: {p.subject}")

        def mutator(r: RoomRecord) -> RoomRecord:
            if r.status == RoomStatus.ARCHIVED:
                raise RoomArchivedError(r.room_id)
            return r.model_copy(
                update={
                    "members": [m for m in r.members if m.subject != p.subject],
                    "updated_at": now_iso(),
                }
            )

        updated = await self._store.update(p.room_id, expected_version=None, mutator=mutator)
        if updated is None:
            return _err(f"room not found: {p.room_id}")
        await self._emit(
            "room_member_left",
            {"room": updated.to_wire(), "subject": p.subject},
        )
        return _ok({"room": updated.to_wire()})

    async def _handle_get_members(self, payload: dict[str, Any]) -> dict[str, Any]:
        room_id = payload.get("room_id", "")
        existing, _project, error = await self._require_room_access(room_id)
        if error is not None:
            return error
        assert existing is not None
        members = [m.model_dump(mode="json") for m in existing.members]
        return _ok({"room_id": existing.room_id, "members": members, "count": len(members)})

    async def _handle_get_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomGetLogPayload.model_validate(payload)
        existing, _project, error = await self._require_room_access(p.room_id)
        if error is not None:
            return error
        records = await self._store.get_log(p.room_id, since_seq=p.since_seq, limit=p.limit)
        entries = [(await self._hydrate_reference(r)).to_wire() for r in records]
        return _ok({"room_id": p.room_id, "entries": entries, "count": len(entries)})

    async def _handle_send(self, payload: dict[str, Any]) -> dict[str, Any]:
        p = RoomSendPayload.model_validate(payload)
        room, _project, error = await self._require_room_access(p.room_id)
        if error is not None:
            return error
        assert room is not None
        # 写时校验：author 合法 = human 或 room 成员 agent
        if p.author_type == RoomSubjectType.AGENT and not any(
            m.subject == p.author for m in room.members
        ):
            return _err(f"author not in room: {p.author}")
        # data.targets（若给）⊆ room 的 agent 成员（{message, targets?} 约定）
        error = self._validate_targets(room, p.data.get("targets"))
        if error is not None:
            return _err(error)
        if not p.data.get("mm_message_id") and self._publish is not None:
            return await self._publish(room, p)
        result = await self.append_log(
            p.room_id, author=p.author, author_type=p.author_type, data=p.data
        )
        if (
            result.get("success")
            and p.author_type == RoomSubjectType.HUMAN
            and not p.data.get("mm_message_id")
        ):
            self._schedule_delivery(room, p)
        return result

    def _schedule_delivery(self, room: RoomRecord, p: RoomSendPayload) -> None:
        """human 消息落账成功后 fire-and-forget 投递给 room 内 agent。

        投递对象：data.targets（已校验 ⊆ agent 成员）优先；缺省整室 agent
        成员；恒排除作者。不阻塞 room_send 回执，失败仅记日志不重试。
        """
        if self._deliver is None:
            return
        message = p.data.get("message")
        if not isinstance(message, str) or not message:
            logger.warning(
                "room_send: skip delivery (no message) room=%s author=%s",
                p.room_id,
                p.author,
            )
            return
        targets = self._resolve_delivery_targets(room, p)
        for target in targets:
            try:
                asyncio.get_running_loop().create_task(
                    self._deliver_one(target, p.room_id, sender=p.author, content=message)
                )
            except RuntimeError:
                logger.warning(
                    "room_send: no running loop, delivery skipped room=%s target=%s",
                    p.room_id,
                    target,
                )

    @staticmethod
    def _resolve_delivery_targets(room: RoomRecord, p: RoomSendPayload) -> list[str]:
        data_targets = p.data.get("targets")
        agent_members = [m.subject for m in room.members if m.subject_type == RoomSubjectType.AGENT]
        if isinstance(data_targets, list) and data_targets:
            member_set = set(agent_members)
            targets = [t for t in data_targets if t in member_set]
        else:
            targets = list(agent_members)
        return [t for t in targets if t != p.author]

    async def _deliver_one(self, target: str, room_id: str, *, sender: str, content: str) -> None:
        try:
            result = await self._deliver(target, sender, content, room_id)  # type: ignore[misc]
        except Exception:
            logger.exception("room delivery failed: target=%s sender=%s", target, sender)
            return
        if not result.get("success"):
            logger.warning(
                "room delivery rejected: target=%s sender=%s error=%s",
                target,
                sender,
                result.get("error"),
            )

    @staticmethod
    def _validate_targets(room: RoomRecord, targets: Any) -> str | None:
        if targets is None:
            return None
        if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
            return "invalid targets: expected string[]"
        agent_members = {m.subject for m in room.members if m.subject_type == RoomSubjectType.AGENT}
        for t in targets:
            if t not in agent_members:
                return f"target not in room: {t}"
        return None

    # ─── send 收敛点（双路径共用：room_send 命令 + Core send ability serial） ───

    async def _hydrate_reference(self, record: RoomLogRecord) -> RoomLogRecord:
        """从 Core MM 权威读通正文，Room 日志仅持消息引用。"""
        message_id = record.data.get("mm_message_id")
        if not message_id:
            return record
        room, project, error = await self._require_room_access(record.room_id)
        if error or project is None or room is None:
            raise ValueError(error or "Room project unavailable")
        path = ProjectPaths.from_locator(project["project_root_locator"]).action_chain_db_path
        async with aiosqlite.connect(f"file:{path}?mode=ro", uri=True) as db:
            row = await (
                await db.execute(
                    "SELECT content, sender_id, metadata FROM mm_messages WHERE message_id=?",
                    (message_id,),
                )
            ).fetchone()
            deliveries = await (
                await db.execute(
                    "SELECT delivery_id, recipient_id, state, error, blocked_reason "
                    "FROM mm_deliveries WHERE message_id=? ORDER BY seq",
                    (message_id,),
                )
            ).fetchall()
        if row is None or row[1] not in (record.author, f"human:{record.author}"):
            raise ValueError("Room message reference is missing or has a different author")
        metadata = json.loads(row[2])
        rooms = metadata.get("room_ids") or [metadata.get("room_id")]
        if record.room_id not in rooms:
            raise ValueError("Message does not belong to this Room")
        data = {**metadata.get("room_data", {}), **record.data, "via": "message_manager"}
        if metadata.get("room_has_message", True):
            data["message"] = row[0]
        if metadata.get("targets"):
            data["targets"] = metadata["targets"]
        data["deliveries"] = [
            {
                "delivery_id": d[0],
                "recipient_id": d[1],
                "state": "blocked" if d[4] else d[2],
                "error": d[3],
                "blocked_reason": d[4],
            }
            for d in deliveries
        ]
        return record.model_copy(update={"data": data})

    async def append_log(
        self,
        room_id: str,
        *,
        author: str,
        author_type: RoomSubjectType,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """分配 seq → 落库 → 广播 ROOM_LOG_APPENDED → 返回 _ok({entry})。"""
        _room, _project, error = await self._require_room_access(room_id)
        if error is not None:
            return error
        data = dict(data or {})
        message_id = data.get("mm_message_id")
        reference_id = f"mm:{message_id}:{room_id}" if message_id else None
        if reference_id:
            data.pop("message", None)
            candidate = make_room_log_record(
                room_id=room_id, seq=0, author=author, author_type=author_type, data=data
            )
            candidate.id = reference_id
            try:
                await self._hydrate_reference(candidate)
            except (ValueError, aiosqlite.Error) as exc:
                return _err(str(exc))
            existing = await self._store.get_log_by_id(room_id, reference_id)
            if existing:
                return _ok({"entry": (await self._hydrate_reference(existing)).to_wire()})

        def make_record(seq: int) -> RoomLogRecord:
            record = make_room_log_record(
                room_id=room_id, seq=seq, author=author, author_type=author_type, data=data
            )
            if reference_id:
                record.id = reference_id
            return record

        try:
            result = await self._store.append_log(room_id, make_record)
        except RoomArchivedError:
            return _err("resource_archived")
        if result is None:
            return _err(f"room not found: {room_id}")
        record: RoomLogRecord = await self._hydrate_reference(result[0])
        await self._emit("room_log_appended", {"entry": record.to_wire()})
        return _ok({"entry": record.to_wire()})

    # ─── send ability 支撑（Core 侧 recipients 解析用） ───

    async def resolve_recipients(
        self, room_ids: list[str], extra_targets: list[str] | None = None
    ) -> dict[str, Any]:
        """recipients = Σ room 的 agent 成员 ∪ targets 去重。

        任一 room 不存在 → _err；返回 _ok({recipients, rooms})（rooms 为
        校验通过的 room wire 列表，供调用方逐 room 落账）。
        """
        recipients: list[str] = []
        seen: set[str] = set()
        rooms: list[dict[str, Any]] = []
        for room_id in room_ids:
            room, _project, error = await self._require_room_access(room_id)
            if error is not None:
                return error
            assert room is not None
            rooms.append(room.to_wire())
            for m in room.members:
                if m.subject_type == RoomSubjectType.AGENT and m.subject not in seen:
                    seen.add(m.subject)
                    recipients.append(m.subject)
        for t in extra_targets or []:
            if t not in seen:
                seen.add(t)
                recipients.append(t)
        return _ok({"recipients": recipients, "rooms": rooms})


_HANDLERS: dict[str, Callable[[RoomManager, dict[str, Any]], Any]] = {
    "room_create": RoomManager._handle_create,
    "room_list": RoomManager._handle_list,
    "room_get": RoomManager._handle_get,
    "room_update": RoomManager._handle_update,
    "room_archive": RoomManager._handle_archive,
    "room_restore": RoomManager._handle_restore,
    "room_delete": RoomManager._handle_delete,
    "room_join": RoomManager._handle_join,
    "room_leave": RoomManager._handle_leave,
    "room_get_members": RoomManager._handle_get_members,
    "room_get_log": RoomManager._handle_get_log,
    "room_send": RoomManager._handle_send,
}
