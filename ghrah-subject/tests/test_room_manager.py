# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RoomManager 单测：12 命令 + 生命周期守卫 + 事件面 + send 收敛点。

契约基准 = mock-server ``state.ts``（同 payload schema / 同事件名 /
同 seq 分配语义 / 同 result 形状）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ghrah.protocol.types import RoomSubjectType

from ghrah.subject.room.manager import RoomManager
from ghrah.subject.room.store import RoomStore

PROJECT_IDS = {"proj-1"}
PROJECT_AGENTS = {"architect", "frontend-dev", "tester", "bad"}


async def _manager(
    tmp_path: Path, events: list[tuple[str, dict[str, Any]]] | None = None
) -> RoomManager:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, payload))

    async def project_get(project_id: str) -> dict[str, Any] | None:
        if project_id not in PROJECT_IDS:
            return None
        return {
            "project_id": project_id,
            "archived_at": None,
            "agents": [
                {"agent_id": name, "name": name, "cluster_id": "default"}
                for name in sorted(PROJECT_AGENTS)
            ],
        }

    return RoomManager(store, on_event=on_event, project_get=project_get)


def _data(result: dict) -> dict:
    assert result["success"], result
    return result["data"]


async def _make_room(m: RoomManager, name: str = "architecture") -> dict:
    result = await m.handle_command("room_create", {"project_id": "proj-1", "name": name})
    return _data(result)["room"]


async def test_create_validates_project_and_name(tmp_path: Path) -> None:
    m = await _manager(tmp_path)
    try:
        bad_project = await m.handle_command("room_create", {"project_id": "nope", "name": "n"})
        assert not bad_project["success"]
        assert "project not found" in bad_project["error"]

        blank = await m.handle_command("room_create", {"project_id": "proj-1", "name": "  "})
        assert not blank["success"]

        missing = await m.handle_command("room_create", {"name": "n"})
        assert not missing["success"]
        assert "project_id required" in missing["error"]
    finally:
        await m.store.stop()


async def test_create_list_get_event_flow(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        assert room["status"] == "active"
        assert room["version"] == 1
        assert events[0][0] == "room_created"
        assert events[0][1]["room"]["room_id"] == room["room_id"]

        other = await _make_room(m, "frontend")
        listed = _data(await m.handle_command("room_list", {"project_id": "proj-1"}))
        assert listed["count"] == 2
        assert {r["name"] for r in listed["rooms"]} == {"architecture", "frontend"}

        got = _data(await m.handle_command("room_get", {"room_id": room["room_id"]}))
        assert got["room"]["room_id"] == room["room_id"]

        missing = await m.handle_command("room_get", {"room_id": "nope"})
        assert not missing["success"]
        del other
    finally:
        await m.store.stop()


async def test_update_optimistic_lock_and_event(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        updated = _data(
            await m.handle_command(
                "room_update",
                {"room_id": room["room_id"], "name": "v2", "expected_version": 1},
            )
        )["room"]
        assert updated["name"] == "v2"
        assert updated["version"] == 2
        assert events[-1][0] == "room_updated"

        conflict = await m.handle_command(
            "room_update",
            {"room_id": room["room_id"], "name": "v3", "expected_version": 1},
        )
        assert not conflict["success"]
        assert conflict["error"] == "room_version_conflict"
        assert "version conflict" in conflict["error_detail"]
    finally:
        await m.store.stop()


async def test_archive_restore_guards_and_preserves_room_aggregate(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        joined = _data(
            await m.handle_command(
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "architect",
                    "subject_type": "agent",
                },
            )
        )["room"]
        await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "preserved"},
            },
        )

        archived = _data(
            await m.handle_command(
                "room_archive",
                {
                    "room_id": room["room_id"],
                    "expected_version": joined["version"],
                },
            )
        )["room"]
        assert archived["status"] == "archived"
        assert archived["archived_at"] is not None
        assert archived["members"] == joined["members"]
        assert await m.store.count_logs(room["room_id"]) == 1
        assert events[-1] == ("room_archived", {"room": archived})

        event_count = len(events)
        repeated = _data(
            await m.handle_command(
                "room_archive",
                {"room_id": room["room_id"], "expected_version": joined["version"]},
            )
        )["room"]
        assert repeated == archived
        assert len(events) == event_count

        guarded_commands = [
            ("room_update", {"name": "blocked", "expected_version": archived["version"]}),
            ("room_join", {"subject": "human:other", "subject_type": "human"}),
            ("room_leave", {"subject": "architect"}),
            ("room_get_members", {}),
            ("room_get_log", {}),
            (
                "room_send",
                {
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "blocked"},
                },
            ),
        ]
        for command, extra in guarded_commands:
            result = await m.handle_command(command, {"room_id": room["room_id"], **extra})
            assert result["error"] == "resource_archived", command
        direct = await m.append_log(room["room_id"], author="architect", author_type="agent")
        assert direct["error"] == "resource_archived"
        recipients = await m.resolve_recipients([room["room_id"]])
        assert recipients["error"] == "resource_archived"

        # System-level discovery remains available for restore/delete UI.
        got = _data(await m.handle_command("room_get", {"room_id": room["room_id"]}))
        assert got["room"] == archived
        listed = _data(
            await m.handle_command("room_list", {"project_id": "proj-1", "status": "archived"})
        )
        assert [item["room_id"] for item in listed["rooms"]] == [room["room_id"]]

        conflict = await m.handle_command(
            "room_restore",
            {"room_id": room["room_id"], "expected_version": joined["version"]},
        )
        assert conflict["error"] == "room_version_conflict"
        assert "version conflict" in conflict["error_detail"]
        restored = _data(
            await m.handle_command(
                "room_restore",
                {
                    "room_id": room["room_id"],
                    "expected_version": archived["version"],
                },
            )
        )["room"]
        assert restored["status"] == "active"
        assert restored["archived_at"] is None
        assert restored["members"] == joined["members"]
        assert await m.store.count_logs(room["room_id"]) == 1
        assert events[-1] == ("room_restored", {"room": restored})
    finally:
        await m.store.stop()


async def test_parent_project_archive_blocks_all_room_access(tmp_path: Path) -> None:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()
    project = {"project_id": "proj-1", "archived_at": None, "agents": []}

    async def project_get(project_id: str) -> dict[str, Any] | None:
        return project if project_id == "proj-1" else None

    m = RoomManager(store, project_get=project_get)
    try:
        room = await _make_room(m)
        project["archived_at"] = "2026-08-28T00:00:00Z"
        for command, payload in (
            ("room_create", {"project_id": "proj-1", "name": "blocked"}),
            ("room_get", {"room_id": room["room_id"]}),
            ("room_list", {"project_id": "proj-1"}),
            (
                "room_update",
                {
                    "room_id": room["room_id"],
                    "name": "blocked",
                    "expected_version": room["version"],
                },
            ),
            (
                "room_archive",
                {"room_id": room["room_id"], "expected_version": room["version"]},
            ),
            (
                "room_restore",
                {"room_id": room["room_id"], "expected_version": room["version"]},
            ),
            (
                "room_delete",
                {"room_id": room["room_id"], "expected_version": room["version"]},
            ),
            (
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "human:blocked",
                    "subject_type": "human",
                },
            ),
            (
                "room_leave",
                {"room_id": room["room_id"], "subject": "human:blocked"},
            ),
            ("room_get_members", {"room_id": room["room_id"]}),
            ("room_get_log", {"room_id": room["room_id"]}),
            (
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:blocked",
                    "author_type": "human",
                    "data": {"message": "blocked"},
                },
            ),
        ):
            result = await m.handle_command(command, payload)
            assert result["error"] == "project_archived", command
        direct = await m.append_log(room["room_id"], author="architect", author_type="agent")
        assert direct["error"] == "project_archived"
        recipients = await m.resolve_recipients([room["room_id"]])
        assert recipients["error"] == "project_archived"
        assert await store.get(room["room_id"]) is not None
    finally:
        await store.stop()


async def test_join_rejects_agent_from_another_project(tmp_path: Path) -> None:
    m = await _manager(tmp_path)
    try:
        room = await _make_room(m)
        result = await m.handle_command(
            "room_join",
            {
                "room_id": room["room_id"],
                "subject": "foreign-agent-id",
                "subject_type": "agent",
            },
        )
        assert result["error"] == "agent_project_mismatch"
        assert (await m.store.get(room["room_id"])).members == []  # type: ignore[union-attr]
    finally:
        await m.store.stop()


async def test_delete_permanently_removes_logs_without_force(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        joined = _data(
            await m.handle_command(
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "architect",
                    "subject_type": "agent",
                },
            )
        )["room"]
        await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "hi"},
            },
        )
        conflict = await m.handle_command(
            "room_delete",
            {"room_id": room["room_id"], "expected_version": room["version"]},
        )
        assert conflict["error"] == "room_version_conflict"
        assert "version conflict" in conflict["error_detail"]
        assert await m.store.count_logs(room["room_id"]) == 1

        agents_before = set(PROJECT_AGENTS)
        deleted = _data(
            await m.handle_command(
                "room_delete",
                {
                    "room_id": room["room_id"],
                    "expected_version": joined["version"],
                },
            )
        )
        assert deleted == {
            "room_id": room["room_id"],
            "project_id": "proj-1",
        }
        assert await m.store.count_logs(room["room_id"]) == 0
        assert PROJECT_AGENTS == agents_before
        assert events[-1][0] == "room_deleted"
    finally:
        await m.store.stop()


async def test_join_leave_membership_and_events(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)

        joined = _data(
            await m.handle_command(
                "room_join",
                {
                    "room_id": room["room_id"],
                    "subject": "architect",
                    "subject_type": "agent",
                },
            )
        )["room"]
        assert [mm["subject"] for mm in joined["members"]] == ["architect"]
        assert events[-1][0] == "room_member_joined"
        assert events[-1][1]["member"]["subject"] == "architect"

        # 幂等 join：成功但不发事件
        again = await m.handle_command(
            "room_join",
            {
                "room_id": room["room_id"],
                "subject": "architect",
                "subject_type": "agent",
            },
        )
        assert again["success"]
        assert events[-1][0] == "room_member_joined"

        members = _data(await m.handle_command("room_get_members", {"room_id": room["room_id"]}))
        assert members["count"] == 1
        assert members["room_id"] == room["room_id"]

        left = _data(
            await m.handle_command(
                "room_leave",
                {"room_id": room["room_id"], "subject": "architect"},
            )
        )["room"]
        assert left["members"] == []
        assert events[-1][0] == "room_member_left"
        assert events[-1][1]["subject"] == "architect"

        not_member = await m.handle_command(
            "room_leave", {"room_id": room["room_id"], "subject": "ghost"}
        )
        assert not not_member["success"]
    finally:
        await m.store.stop()


async def test_send_seq_allocation_events_and_validation(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        await m.handle_command(
            "room_join",
            {
                "room_id": room["room_id"],
                "subject": "architect",
                "subject_type": "agent",
            },
        )

        entry = _data(
            await m.handle_command(
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "hello", "targets": ["architect"]},
                },
            )
        )["entry"]
        assert entry["seq"] == 1
        assert entry["author_type"] == "human"
        assert events[-1][0] == "room_log_appended"
        assert events[-1][1]["entry"]["id"] == entry["id"]

        second = _data(
            await m.handle_command(
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "architect",
                    "author_type": "agent",
                    "data": {"message": "world"},
                },
            )
        )["entry"]
        assert second["seq"] == 2

        # agent 作者必须是成员
        outsider = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "ghost",
                "author_type": "agent",
                "data": {"message": "x"},
            },
        )
        assert not outsider["success"]
        assert "author not in room" in outsider["error"]

        # targets ⊆ room agent 成员
        bad_target = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "x", "targets": ["not-member"]},
            },
        )
        assert not bad_target["success"]
        assert "target not in room" in bad_target["error"]

        bad_type = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "x", "targets": "architect"},
            },
        )
        assert not bad_type["success"]
        assert "invalid targets" in bad_type["error"]
    finally:
        await m.store.stop()


async def test_get_log_since_limit(tmp_path: Path) -> None:
    m = await _manager(tmp_path)
    try:
        room = await _make_room(m)
        for i in range(5):
            await m.handle_command(
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": str(i)},
                },
            )
        result = _data(
            await m.handle_command(
                "room_get_log",
                {"room_id": room["room_id"], "since_seq": 3, "limit": 10},
            )
        )
        assert result["count"] == 2
        assert [e["seq"] for e in result["entries"]] == [4, 5]
    finally:
        await m.store.stop()


async def test_append_log_direct_convergence(tmp_path: Path) -> None:
    """send 收敛点：append_log 直调（send ability serial 等价路径）与 room_send
    命令同语义——seq 连续、事件同形。"""
    events: list[tuple[str, dict[str, Any]]] = []
    m = await _manager(tmp_path, events)
    try:
        room = await _make_room(m)
        await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "via command"},
            },
        )
        direct = _data(
            await m.append_log(
                room["room_id"],
                author="architect",
                author_type="agent",
                data={"message": "via ability"},
            )
        )["entry"]
        assert direct["seq"] == 2
        assert events[-1][1]["entry"]["data"]["message"] == "via ability"

        missing = await m.append_log("nope", author="a", author_type="agent")
        assert not missing["success"]
    finally:
        await m.store.stop()


async def test_resolve_recipients_union_dedup(tmp_path: Path) -> None:
    m = await _manager(tmp_path)
    try:
        r1 = await _make_room(m, "architecture")
        r2 = await _make_room(m, "frontend")
        for room, members in ((r1, ["architect", "frontend-dev"]), (r2, ["architect", "tester"])):
            for subject in members:
                result = await m.handle_command(
                    "room_join",
                    {
                        "room_id": room["room_id"],
                        "subject": subject,
                        "subject_type": "agent",
                    },
                )
                assert result["success"]

        # human 成员不计入 recipients
        await m.handle_command(
            "room_join",
            {
                "room_id": r1["room_id"],
                "subject": "human:yuki",
                "subject_type": "human",
            },
        )

        resolved = _data(
            await m.resolve_recipients(
                [r1["room_id"], r2["room_id"]], extra_targets=["architect", "extern"]
            )
        )
        assert resolved["recipients"] == ["architect", "frontend-dev", "tester", "extern"]
        assert len(resolved["rooms"]) == 2

        missing = await m.resolve_recipients(["nope"])
        assert not missing["success"]
    finally:
        await m.store.stop()


async def test_unknown_command(tmp_path: Path) -> None:
    m = await _manager(tmp_path)
    try:
        result = await m.handle_command("room_nope", {})
        assert not result["success"]
        assert "unknown command" in result["error"]
    finally:
        await m.store.stop()


# ─── human 消息投递（投递回路补全：fire-and-forget + targets 解析） ───


async def _delivery_manager(
    tmp_path: Path,
    deliveries: list[tuple[str, str, str]],
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> RoomManager:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def on_event(event_type: str, payload: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, payload))

    async def project_get(project_id: str) -> dict[str, Any] | None:
        if project_id not in PROJECT_IDS:
            return None
        return {
            "project_id": project_id,
            "archived_at": None,
            "agents": [
                {"agent_id": name, "name": name, "cluster_id": "default"}
                for name in sorted(PROJECT_AGENTS)
            ],
        }

    async def deliver(
        target: str,
        sender: str,
        content: str,
        room_id: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deliveries.append((target, sender, content))
        return {"success": True, "data": {"content": "ok"}, "error": None}

    return RoomManager(store, on_event=on_event, project_get=project_get, deliver=deliver)


async def _join(m: RoomManager, room_id: str, subject: str, subject_type: str) -> None:
    result = await m.handle_command(
        "room_join",
        {"room_id": room_id, "subject": subject, "subject_type": subject_type},
    )
    assert result["success"], result


async def _drain_delivery_tasks() -> None:
    import asyncio

    for task in asyncio.all_tasks():
        if task is not asyncio.current_task() and not task.done():
            await asyncio.wait([task], timeout=2.0)


async def test_human_send_delivers_to_targets(tmp_path: Path) -> None:
    """data.targets 优先：只投递 targets 内的 agent 成员，排除作者。"""
    deliveries: list[tuple[str, str, str]] = []
    m = await _delivery_manager(tmp_path, deliveries)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        await _join(m, room["room_id"], "frontend-dev", "agent")

        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "你好", "targets": ["architect"]},
            },
        )
        assert result["success"], result
        await _drain_delivery_tasks()
        assert deliveries == [("architect", "human:yuki", "你好")]
    finally:
        await m.store.stop()


async def test_human_send_broadcasts_to_all_agents_when_no_targets(tmp_path: Path) -> None:
    """缺省 targets：整室 agent 成员投递；human 成员与作者排除。"""
    deliveries: list[tuple[str, str, str]] = []
    m = await _delivery_manager(tmp_path, deliveries)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        await _join(m, room["room_id"], "frontend-dev", "agent")
        await _join(m, room["room_id"], "human:yuki", "human")

        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "hi all"},
            },
        )
        assert result["success"], result
        await _drain_delivery_tasks()
        assert sorted(deliveries) == [
            ("architect", "human:yuki", "hi all"),
            ("frontend-dev", "human:yuki", "hi all"),
        ]
    finally:
        await m.store.stop()


async def test_agent_send_not_delivered(tmp_path: Path) -> None:
    """author_type=agent 不投递（作者即发送者，回路由对端 send ability 承担）。"""
    deliveries: list[tuple[str, str, str]] = []
    m = await _delivery_manager(tmp_path, deliveries)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        await _join(m, room["room_id"], "frontend-dev", "agent")

        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "architect",
                "author_type": "agent",
                "data": {"message": "done", "targets": ["frontend-dev"]},
            },
        )
        assert result["success"], result
        await _drain_delivery_tasks()
        assert deliveries == []
    finally:
        await m.store.stop()


async def test_delivery_failure_does_not_affect_room_send(tmp_path: Path) -> None:
    """投递异常/被拒：fire-and-forget，不阻断回执、不重试。"""
    deliveries: list[tuple[str, str, str]] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(
        target: str,
        sender: str,
        content: str,
        room_id: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deliveries.append((target, sender, content))
        if target == "bad":
            raise RuntimeError("boom")
        return {"success": False, "data": None, "error": "rejected"}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "bad", "agent")
        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "x"},
            },
        )
        assert result["success"], result
        assert result["data"]["entry"]["seq"] == 1
        await _drain_delivery_tasks()
        assert deliveries == [("bad", "human:yuki", "x")]
    finally:
        await m.store.stop()


# ─── 入站内容块（content_blocks）：受理、投递、日志回读 ───

IMAGE_BLOCK: dict[str, Any] = {
    "type": "image",
    "base64": "aGVsbG8=",
    "mime_type": "image/png",
}

VIDEO_BLOCK: dict[str, Any] = {
    "type": "video",
    "attachment_id": "a" * 64,
    "mime_type": "video/mp4",
    "filename": "demo.mp4",
    "frames": [{"type": "image", "base64": "ZmFrZQ==", "mime_type": "image/jpeg"}],
}


async def test_room_send_with_blocks_keeps_blocks_and_delivers_them(tmp_path: Path) -> None:
    """纯块消息：空正文受理成功；日志 data 与投递闭包都拿到校验后的块。"""
    deliveries: list[tuple[str, str, str, list[dict[str, Any]] | None]] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(
        target: str,
        sender: str,
        content: str,
        room_id: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deliveries.append((target, sender, content, blocks))
        return {"success": True, "data": {"content": "ok"}, "error": None}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")

        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"content_blocks": [IMAGE_BLOCK]},
            },
        )
        entry = _data(result)["entry"]
        assert entry["data"]["content_blocks"] == [IMAGE_BLOCK]
        # 纯块消息不把兜底 JSON 当正文
        assert "message" not in entry["data"]

        await _drain_delivery_tasks()
        assert deliveries == [("architect", "human:yuki", "", [IMAGE_BLOCK])]
    finally:
        await m.store.stop()


async def test_room_send_video_block_flows_through_legacy_delivery(tmp_path: Path) -> None:
    """无 publish 的 legacy 路径：video 块同样进入日志 data 与 deliver 闭包。"""
    deliveries: list[tuple[str, str, str, list[dict[str, Any]] | None]] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(
        target: str,
        sender: str,
        content: str,
        room_id: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deliveries.append((target, sender, content, blocks))
        return {"success": True, "data": {}, "error": None}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"content_blocks": [VIDEO_BLOCK]},
            },
        )
        entry = _data(result)["entry"]
        assert entry["data"]["content_blocks"] == [VIDEO_BLOCK]
        await _drain_delivery_tasks()
        assert deliveries == [("architect", "human:yuki", "", [VIDEO_BLOCK])]
    finally:
        await m.store.stop()


async def test_room_send_text_with_blocks_keeps_both(tmp_path: Path) -> None:
    deliveries: list[tuple[str, str, str, list[dict[str, Any]] | None]] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(
        target: str,
        sender: str,
        content: str,
        room_id: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deliveries.append((target, sender, content, blocks))
        return {"success": True, "data": {}, "error": None}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "看图", "content_blocks": [IMAGE_BLOCK]},
            },
        )
        entry = _data(result)["entry"]
        assert entry["data"]["message"] == "看图"
        assert entry["data"]["content_blocks"] == [IMAGE_BLOCK]
        await _drain_delivery_tasks()
        assert deliveries == [("architect", "human:yuki", "看图", [IMAGE_BLOCK])]
    finally:
        await m.store.stop()


async def test_room_send_rejects_malformed_blocks_with_stable_code(tmp_path: Path) -> None:
    """非法块在受理前被拒：稳定错误码 + 不落日志、不投递。"""
    deliveries: list[Any] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(*args: Any, **kwargs: Any) -> dict[str, Any]:
        deliveries.append(args)
        return {"success": True, "data": {}, "error": None}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        cases = [
            ({"content_blocks": "blocks"}, "content_blocks_malformed"),
            ({"content_blocks": [{"type": "hologram", "url": "x"}]}, "unsupported_block_type"),
            ({"message": "x", "content_blocks": [{"type": "image"}]}, "content_blocks_malformed"),
            (
                {"message": "x", "content_blocks": [{"type": "text", "text": "x"}] * 9},
                "content_blocks_total_too_large",
            ),
            (
                {
                    "content_blocks": [
                        {
                            "type": "image",
                            "base64": "A" * (4 * 1024 * 1024 + 1),
                            "mime_type": "image/png",
                        }
                    ]
                },
                "content_block_too_large",
            ),
        ]
        for data, expected in cases:
            result = await m.handle_command(
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": data,
                },
            )
            assert result["success"] is False, (data, result)
            assert result["error"] == expected, (data, result)
            assert result["error_detail"], result
        await _drain_delivery_tasks()
        assert deliveries == []
        log = _data(await m.handle_command("room_get_log", {"room_id": room["room_id"]}))
        assert log["count"] == 0
    finally:
        await m.store.stop()


async def test_room_send_blank_message_without_blocks_requires_text(tmp_path: Path) -> None:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()
    m = RoomManager(store)
    try:
        room = await _make_room(m)
        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "   "},
            },
        )
        assert result["success"] is False
        assert result["error"] == "content_text_required"
    finally:
        await m.store.stop()


async def test_room_send_non_message_event_without_message_still_accepted(tmp_path: Path) -> None:
    """既有语义保留：无 message 键、无块的非消息条目仍可落账（不投递）。"""
    deliveries: list[Any] = []
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def deliver(*args: Any, **kwargs: Any) -> dict[str, Any]:
        deliveries.append(args)
        return {"success": True, "data": {}, "error": None}

    m = RoomManager(store, deliver=deliver)
    try:
        room = await _make_room(m)
        await _join(m, room["room_id"], "architect", "agent")
        result = await m.handle_command(
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"event": "presence"},
            },
        )
        entry = _data(result)["entry"]
        assert entry["data"]["event"] == "presence"
        await _drain_delivery_tasks()
        assert deliveries == []
    finally:
        await m.store.stop()


def _write_mm_db(root: Path, messages: list[dict[str, Any]]) -> None:
    """在 Project Root 下造一份最小 MM sqlite（供 ``_hydrate_reference`` 只读）。"""
    db_dir = root / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_dir / "action-chains.sqlite3") as conn:
        conn.execute(
            "CREATE TABLE mm_messages ("
            "message_id TEXT PRIMARY KEY, sender_id TEXT, content TEXT, metadata TEXT)"
        )
        conn.execute(
            "CREATE TABLE mm_deliveries ("
            "delivery_id TEXT, message_id TEXT, recipient_id TEXT, state TEXT, "
            "error TEXT, blocked_reason TEXT, seq INTEGER)"
        )
        for message in messages:
            conn.execute(
                "INSERT INTO mm_messages VALUES (?, ?, ?, ?)",
                (
                    message["message_id"],
                    message["sender_id"],
                    message["content"],
                    json.dumps(message["metadata"], ensure_ascii=False),
                ),
            )
            conn.execute(
                "INSERT INTO mm_deliveries VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"delivery:{message['message_id']}",
                    message["message_id"],
                    "architect",
                    "delivered",
                    None,
                    None,
                    1,
                ),
            )
        conn.commit()


async def _hydrate_manager(tmp_path: Path, root: Path) -> RoomManager:
    store = RoomStore(tmp_path / "rooms.db")
    await store.start()

    async def project_get(project_id: str) -> dict[str, Any] | None:
        return {
            "project_id": project_id,
            "project_root_locator": str(root),
            "archived_at": None,
            "agents": [{"agent_id": "architect", "name": "architect", "cluster_id": "c1"}],
        }

    return RoomManager(store, project_get=project_get)


async def test_hydrate_reference_copies_blocks_from_mm_metadata(tmp_path: Path) -> None:
    """Room 日志只持消息引用；回读时把 MM metadata 顶层的块复制进 data。"""
    root = tmp_path / "root"
    m = await _hydrate_manager(tmp_path, root)
    try:
        room = await _make_room(m)
        _write_mm_db(
            root,
            [
                {
                    "message_id": "m-blocks",
                    "sender_id": "human:yuki",
                    "content": "",
                    "metadata": {
                        "room_ids": [room["room_id"]],
                        "room_id": room["room_id"],
                        "room_projection": True,
                        "room_data": {"message": ""},
                        "room_has_message": False,
                        "content_blocks": [IMAGE_BLOCK],
                    },
                }
            ],
        )
        result = await m.append_log(
            room["room_id"],
            author="human:yuki",
            author_type=RoomSubjectType.HUMAN,
            data={"mm_message_id": "m-blocks"},
        )
        entry = _data(result)["entry"]
        assert entry["data"]["content_blocks"] == [IMAGE_BLOCK]
        assert entry["data"]["via"] == "message_manager"
        # 纯块消息不把受理时的兜底 JSON 当正文
        assert "message" not in entry["data"]
        assert entry["data"]["deliveries"] == [
            {
                "delivery_id": "delivery:m-blocks",
                "recipient_id": "architect",
                "state": "delivered",
                "error": None,
                "blocked_reason": None,
            }
        ]
    finally:
        await m.store.stop()


async def test_hydrate_reference_keeps_plain_text_message(tmp_path: Path) -> None:
    """回归：无块消息的正文仍从 MM 权威读通，不被块逻辑影响。"""
    root = tmp_path / "root"
    m = await _hydrate_manager(tmp_path, root)
    try:
        room = await _make_room(m)
        _write_mm_db(
            root,
            [
                {
                    "message_id": "m-text",
                    "sender_id": "human:yuki",
                    "content": "纯文本",
                    "metadata": {
                        "room_ids": [room["room_id"]],
                        "room_id": room["room_id"],
                        "room_projection": True,
                        "room_data": {"message": "纯文本"},
                        "room_has_message": True,
                    },
                }
            ],
        )
        result = await m.append_log(
            room["room_id"],
            author="human:yuki",
            author_type=RoomSubjectType.HUMAN,
            data={"mm_message_id": "m-text"},
        )
        entry = _data(result)["entry"]
        assert entry["data"]["message"] == "纯文本"
        assert "content_blocks" not in entry["data"]
    finally:
        await m.store.stop()
