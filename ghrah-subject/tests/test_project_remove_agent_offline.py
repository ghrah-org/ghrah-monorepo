# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``project_remove_agent``：runtime 已离线时仍可删除 Project 定义。

C3 端口化把 terminate 的失败从稳定码（``agent_identity_mismatch`` /
``agent_not_found``）改成 ``ApiError`` 文本（``NOT_FOUND: agent '<id>' is not
online``）。旧判定只认稳定码，导致「spawn 失败或已停止」的 Agent 删除被拒
（定义保留、运行侧本来就没有实例）。本文件锁定两条语义：

1. 「运行侧已无该 Agent」的两种形态都视为已终止，继续删定义；
2. 真正的终止失败仍拒绝删除，避免留下「定义已删、实例仍在跑」的游离 Agent。

另锁定删除的另外两个联带行为：忙碌（``runtime.busy=true``）时拒绝删除；
删除成功后清掉项目内所有 Room 中该 Agent 的成员关系（日志保留）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.types import RoomStatus, RoomSubjectType

from ghrah.subject.project.manager import ProjectManager, _is_terminate_absent
from ghrah.subject.project.models import AgentRecord, ProjectRecord, make_project_record
from ghrah.subject.room.models import (
    RoomRecord,
    make_room_log_record,
    make_room_member,
    make_room_record,
)
from ghrah.subject.room.store import RoomStore

AGENT_ID = "a" * 32
OTHER_AGENT_ID = "b" * 32
AGENT_NAME = "requirement"


class _FakeHandle:
    """假句柄：实现删除路径用到的 ``delete_assembly`` 与运行态读取。"""

    def __init__(
        self,
        delete_result: dict[str, Any] | None = None,
        *,
        raise_exc: Exception | None = None,
        busy: bool = False,
        project_id: str = "",
    ) -> None:
        self._delete_result = delete_result or {"success": True, "data": {}}
        self._raise_exc = raise_exc
        self._busy = busy
        self.project_id = project_id
        self.delete_calls: list[str] = []
        self.runtime_calls: list[str] = []

    @property
    def is_connected(self) -> bool:
        return True

    async def get_entity_runtime(self, agent_id: str) -> dict[str, Any]:
        self.runtime_calls.append(agent_id)
        return {"availability": "online", "incarnation_id": "x", "busy": self._busy}

    async def delete_assembly(self, agent_id: str) -> dict[str, Any]:
        self.delete_calls.append(agent_id)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._delete_result


class _FakeTransport:
    """只有已登记的在线 cluster 才会被 remove 路径使用。"""

    def __init__(self, handle: _FakeHandle | None) -> None:
        self._handle = handle

    def has_cluster(self, cluster_id: str) -> bool:
        return self._handle is not None

    def get_handle(self, cluster_id: str) -> _FakeHandle:
        assert self._handle is not None
        return self._handle


class _FakeStore:
    """内存 ProjectStore：只实现 remove 路径用到的 get/update。"""

    def __init__(self, record: ProjectRecord) -> None:
        self.record = record
        self.update_calls = 0

    async def get(self, project_id: str, include_archived: bool = False) -> ProjectRecord | None:
        return self.record if project_id == self.record.project_id else None

    async def update(
        self,
        project_id: str,
        expected_version: int,
        mutator: Any,
    ) -> ProjectRecord:
        assert expected_version == self.record.version
        self.update_calls += 1
        updated = mutator(self.record)
        self.record = updated.model_copy(update={"version": updated.version + 1})
        return self.record


def _record() -> ProjectRecord:
    agent = AgentRecord(
        name=AGENT_NAME,
        agent_id=AGENT_ID,
        desired_state="running",
        runtime_status="failed",
        last_error="model_configuration_invalid",
    )
    return make_project_record(
        name="demo",
        project_root_locator="file:///tmp/demo",
    ).model_copy(update={"agents": [agent]})


def _remove_payload(record: ProjectRecord) -> dict[str, Any]:
    return {
        "project_id": record.project_id,
        "agent_id": AGENT_ID,
        "agent_name": AGENT_NAME,
        "expected_version": record.version,
    }


def _manager(
    record: ProjectRecord,
    *,
    busy: bool = False,
    delete_result: dict[str, Any] | None = None,
    raise_exc: Exception | None = None,
    room_store: RoomStore | None = None,
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> tuple[ProjectManager, _FakeStore, _FakeHandle]:
    handle = _FakeHandle(
        delete_result,
        raise_exc=raise_exc,
        busy=busy,
        project_id=record.project_id,
    )

    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, payload))

    store = _FakeStore(record)
    manager = ProjectManager(
        store=store,  # type: ignore[arg-type]
        workspace_mgr=None,  # type: ignore[arg-type]
        task_mgr=None,  # type: ignore[arg-type]
        cluster_registry=_FakeTransport(handle),  # type: ignore[arg-type]
        on_event=on_event if events is not None else None,
    )
    if room_store is not None:
        manager.register_scoped_resource(room_store, room_store=True)
    return manager, store, handle


async def _room_store(tmp_path: Path) -> RoomStore:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()
    return store


async def _seed_room(
    store: RoomStore,
    *,
    project_id: str,
    name: str,
    members: list[Any],
) -> RoomRecord:
    room = make_room_record(project_id=project_id, name=name).model_copy(
        update={"members": members}
    )
    await store.upsert(room)
    return room


async def test_remove_agent_accepts_absent_instance() -> None:
    """快照/进程都不存在（``delete_assembly`` 幂等成功）→ 删定义成功。"""
    record = _record()
    manager, store, handle = _manager(
        record, delete_result={"success": True, "data": {"deleted": False, "stopped": False}}
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert store.record.agents == []
    assert handle.delete_calls == [AGENT_ID]


async def test_remove_agent_accepts_raised_port_api_error() -> None:
    """端口实现直接抛 ``ApiError('NOT_FOUND: ... is not online')`` 同样视为已终止。"""
    record = _record()
    manager, store, handle = _manager(
        record,
        raise_exc=ApiError(
            Problem.make(
                ProblemCode.NOT_FOUND,
                capability="agents.delete",
                detail=f"agent '{AGENT_ID}' is not online",
            )
        ),
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert store.record.agents == []


async def test_remove_agent_keeps_definition_on_real_terminate_failure() -> None:
    """真正的终止失败仍拒绝删除，定义保留。"""
    record = _record()
    manager, store, handle = _manager(
        record, delete_result={"success": False, "error": "supervisor unavailable"}
    )

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is False, result
    assert result["error"] == "agent_terminate_failed"
    assert store.update_calls == 0
    assert [agent.agent_id for agent in store.record.agents] == [AGENT_ID]


async def test_remove_agent_rejects_busy_agent() -> None:
    """``runtime.busy=true`` → 拒绝删除，不 terminate、不写 Project 记录。"""
    record = _record()
    manager, store, handle = _manager(record, busy=True)

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is False, result
    assert result["error"] == "agent_busy"
    assert "processing messages" in result["error_detail"]
    assert store.update_calls == 0
    assert handle.delete_calls == []
    assert [agent.agent_id for agent in store.record.agents] == [AGENT_ID]
    assert handle.runtime_calls == [AGENT_ID]


async def test_remove_agent_when_not_busy_deletes_and_terminates() -> None:
    """``runtime.busy=false`` → 正常删除并 terminate 运行侧。"""
    record = _record()
    manager, store, handle = _manager(record, busy=False)

    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert store.record.agents == []
    assert handle.runtime_calls == [AGENT_ID]
    assert handle.delete_calls == [AGENT_ID]


async def test_remove_agent_purges_room_members_and_emits_left(tmp_path: Path) -> None:
    """删除后清目标 Room 内该 Agent 成员，其余成员与其他 Room 不动，并发 left 事件。"""
    record = _record()
    room_store = await _room_store(tmp_path)
    try:
        target_room = await _seed_room(
            room_store,
            project_id=record.project_id,
            name="architecture",
            members=[
                make_room_member(AGENT_ID, RoomSubjectType.AGENT, AGENT_NAME),
                make_room_member("c" * 32, RoomSubjectType.AGENT, "tester"),
                make_room_member("human:yuki", RoomSubjectType.HUMAN, "yuki"),
            ],
        )
        other_room = await _seed_room(
            room_store,
            project_id=record.project_id,
            name="frontend",
            members=[
                make_room_member(OTHER_AGENT_ID, RoomSubjectType.AGENT, ""),
                make_room_member("human:kai", RoomSubjectType.HUMAN, "kai"),
            ],
        )
        events: list[tuple[str, dict[str, Any]]] = []
        manager, _store, _handle = _manager(record, room_store=room_store, events=events)

        result = await manager.handle_command("project_remove_agent", _remove_payload(record))

        assert result["success"] is True, result
        purged = await room_store.get(target_room.room_id)
        assert purged is not None
        assert [member.subject for member in purged.members] == ["c" * 32, "human:yuki"]
        assert [member.subject_name for member in purged.members] == ["tester", "yuki"]
        assert purged.version == target_room.version + 1

        untouched = await room_store.get(other_room.room_id)
        assert untouched is not None
        assert [member.subject for member in untouched.members] == [
            OTHER_AGENT_ID,
            "human:kai",
        ]
        assert untouched.version == other_room.version

        left_events = [payload for event, payload in events if event == "room_member_left"]
        assert len(left_events) == 1
        payload = left_events[0]
        assert set(payload) == {"room", "subject"}
        assert payload["subject"] == AGENT_ID
        assert payload["room"]["room_id"] == target_room.room_id
        assert AGENT_ID not in [member["subject"] for member in payload["room"]["members"]]
    finally:
        await room_store.stop()


async def test_remove_agent_keeps_room_logs(tmp_path: Path) -> None:
    """清成员只 update Room；Room 日志（append-only 链）条数不变。"""
    record = _record()
    room_store = await _room_store(tmp_path)
    try:
        room = await _seed_room(
            room_store,
            project_id=record.project_id,
            name="architecture",
            members=[
                make_room_member(AGENT_ID, RoomSubjectType.AGENT, AGENT_NAME),
                make_room_member("human:yuki", RoomSubjectType.HUMAN, "yuki"),
            ],
        )
        for text in ("first", "second"):
            await room_store.append_log(
                room.room_id,
                lambda seq, content=text: make_room_log_record(
                    room_id=room.room_id,
                    seq=seq,
                    author="human:yuki",
                    author_type=RoomSubjectType.HUMAN,
                    data={"message": content},
                ),
            )
        assert await room_store.count_logs(room.room_id) == 2
        manager, _store, _handle = _manager(record, room_store=room_store)

        result = await manager.handle_command("project_remove_agent", _remove_payload(record))

        assert result["success"] is True, result
        assert await room_store.count_logs(room.room_id) == 2
        after = await room_store.get(room.room_id)
        assert after is not None
        assert [member.subject for member in after.members] == ["human:yuki"]
    finally:
        await room_store.stop()


async def test_remove_agent_without_room_store_still_deletes() -> None:
    """reduced profile（无 RoomUnit）下 ``_room_store`` 为 None，删除仍成功。"""
    record = _record()
    manager, store, handle = _manager(record)

    assert manager._room_store is None
    result = await manager.handle_command("project_remove_agent", _remove_payload(record))

    assert result["success"] is True, result
    assert store.update_calls == 1
    assert handle.delete_calls == [AGENT_ID]


async def test_remove_agent_purges_members_joined_by_name(tmp_path: Path) -> None:
    """历史成员用名字登记：``subject`` 或 ``subject_name`` 等于 Agent 名字时同样移除。"""
    record = _record()
    room_store = await _room_store(tmp_path)
    try:
        room = await _seed_room(
            room_store,
            project_id=record.project_id,
            name="by-name",
            members=[
                make_room_member(AGENT_NAME, RoomSubjectType.AGENT, ""),
                make_room_member("d" * 32, RoomSubjectType.AGENT, AGENT_NAME),
                make_room_member("human:yuki", RoomSubjectType.HUMAN, "yuki"),
            ],
        )
        events: list[tuple[str, dict[str, Any]]] = []
        manager, _store, _handle = _manager(record, room_store=room_store, events=events)

        result = await manager.handle_command("project_remove_agent", _remove_payload(record))

        assert result["success"] is True, result
        after = await room_store.get(room.room_id)
        assert after is not None
        assert [member.subject for member in after.members] == ["human:yuki"]
        left_subjects = sorted(
            payload["subject"] for event, payload in events if event == "room_member_left"
        )
        assert left_subjects == sorted([AGENT_NAME, "d" * 32])
    finally:
        await room_store.stop()


async def test_remove_agent_purges_archived_room_members(tmp_path: Path) -> None:
    """归档 Room 的成员同样是现在时：成员清掉，Room 状态与日志语义不变。"""
    record = _record()
    room_store = await _room_store(tmp_path)
    try:
        archived_room = make_room_record(project_id=record.project_id, name="archived").model_copy(
            update={
                "members": [
                    make_room_member(AGENT_ID, RoomSubjectType.AGENT, AGENT_NAME),
                    make_room_member("human:yuki", RoomSubjectType.HUMAN, "yuki"),
                ],
                "status": RoomStatus.ARCHIVED,
                "archived_at": "2026-11-01T00:00:00+00:00",
            }
        )
        await room_store.upsert(archived_room)
        events: list[tuple[str, dict[str, Any]]] = []
        manager, _store, _handle = _manager(record, room_store=room_store, events=events)

        result = await manager.handle_command("project_remove_agent", _remove_payload(record))

        assert result["success"] is True, result
        after = await room_store.get(archived_room.room_id)
        assert after is not None
        assert [member.subject for member in after.members] == ["human:yuki"]
        assert after.status == archived_room.status
        assert after.version == archived_room.version + 1
        assert [payload["subject"] for event, payload in events if event == "room_member_left"] == [
            AGENT_ID
        ]
    finally:
        await room_store.stop()


def test_is_terminate_absent_recognizes_both_shapes() -> None:
    """稳定码（旧命令面）与 ApiError 文本（端口面）都算已终止。"""
    assert _is_terminate_absent("agent_not_found") is True
    assert _is_terminate_absent("agent_identity_mismatch") is True
    assert _is_terminate_absent(f"NOT_FOUND: agent '{AGENT_ID}' is not online") is True
    assert _is_terminate_absent("not_found: agent x") is True
    assert _is_terminate_absent("supervisor unavailable") is False
    assert _is_terminate_absent("") is False
