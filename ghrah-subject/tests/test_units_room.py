# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""RoomUnit 集成测试（full profile）：装配序（Project 后 / registry 前）、
命令经 ctx.serial 路由、事件直发 ``event/room_*``、send 双路径收敛。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ability_testutil import put_agent_template
from ghrah.ability.access import GrantPolicy
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import SubjectConfig
from ghrah.subject.room.manager import RoomManager
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.units import mount_builtin_units
from ghrah.subject.units.room import RoomUnit


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
    )


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _dispatch(ctx: Context, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await bridge_command(ctx, command, payload)


async def _make_project(ctx: Context) -> str:
    result = await _dispatch(ctx, "project_create", {"name": "demo"})
    return _data(result)["project"]["project_id"]


async def _add_project_agent(ctx: Context, project_id: str, name: str) -> str:
    project = _data(await _dispatch(ctx, "project_get", {"project_id": project_id}))["project"]
    result = await _dispatch(
        ctx,
        "project_add_agent",
        {
            "project_id": project_id,
            "agent": {
                "name": name,
                "cluster_id": project["cluster_ids"][0],
                "manifest_ref": put_agent_template(ctx.get("manifest_store")),
            },
        },
    )
    return _data(result)["agent_id"]


async def test_room_unit_service_and_mount_order(tmp_path: Path) -> None:
    async with Context() as ctx:
        fibers = await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        assert "room" in fibers

        manager = ctx.get("room_manager")
        assert isinstance(manager, RoomManager)
        assert ctx.get("room_store") is not None


async def test_room_commands_via_serial_and_events(tmp_path: Path) -> None:
    async with Context() as ctx:
        emitted: list[tuple[str, dict[str, Any]]] = []

        def collect_room_created(payload: Any) -> None:
            emitted.append(("room_created", payload))

        def collect_log(payload: Any) -> None:
            emitted.append(("room_log_appended", payload))

        ctx.on("event/room_created", collect_room_created)
        ctx.on("event/room_log_appended", collect_log)

        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        project_id = await _make_project(ctx)
        await _add_project_agent(ctx, project_id, "architect")

        room = _data(
            await _dispatch(ctx, "room_create", {"project_id": project_id, "name": "architecture"})
        )["room"]
        assert emitted[0][0] == "room_created"

        await _dispatch(
            ctx,
            "room_join",
            {
                "room_id": room["room_id"],
                "subject": "architect",
                "subject_type": "agent",
            },
        )
        entry = _data(
            await _dispatch(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:yuki",
                    "author_type": "human",
                    "data": {"message": "hello"},
                },
            )
        )["entry"]
        assert entry["seq"] == 1
        assert emitted[-1][0] == "room_log_appended"
        assert emitted[-1][1]["entry"]["id"] == entry["id"]


async def test_room_create_validates_project_via_project_manager(
    tmp_path: Path,
) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        result = await _dispatch(ctx, "room_create", {"project_id": "missing-project", "name": "n"})
        assert not result["success"]
        assert "project not found" in result["error"]


async def test_send_ability_serial_path_converges(tmp_path: Path) -> None:
    """send 实现面：Core send ability 经 ctx.serial("command/room_send") 落账。"""
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="full")
        project_id = await _make_project(ctx)
        agent_id = await _add_project_agent(ctx, project_id, "architect")
        room = _data(await _dispatch(ctx, "room_create", {"project_id": project_id, "name": "r"}))[
            "room"
        ]

        # 模拟 Core 侧 send ability 的落账调用（serial 保持命令面统一）
        result = await _dispatch(
            ctx,
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "architect",
                "author_type": "agent",
                "data": {"message": "via send ability", "targets": []},
            },
        )
        # architect 不是成员（未 join）→ 拒绝；join 后成功
        assert not result["success"]
        await _dispatch(
            ctx,
            "room_join",
            {
                "room_id": room["room_id"],
                "subject": "architect",
                "subject_type": "agent",
            },
        )
        ok_result = await _dispatch(
            ctx,
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "architect",
                "author_type": "agent",
                "data": {"message": "via send ability", "targets": []},
            },
        )
        entry = _data(ok_result)["entry"]
        assert entry["author"] == agent_id


async def test_room_log_persistence_across_restart(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        project_id = await _make_project(ctx)
        room = _data(await _dispatch(ctx, "room_create", {"project_id": project_id, "name": "r"}))[
            "room"
        ]
        await _dispatch(
            ctx,
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:yuki",
                "author_type": "human",
                "data": {"message": "persisted"},
            },
        )
        room_id = room["room_id"]

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        listed = _data(await _dispatch(ctx, "room_list", {}))
        assert any(r["room_id"] == room_id for r in listed["rooms"])
        log = _data(await _dispatch(ctx, "room_get_log", {"room_id": room_id}))
        assert log["count"] == 1
        assert log["entries"][0]["data"]["message"] == "persisted"
        assert log["entries"][0]["seq"] == 1


async def test_room_delivery_routes_agent_id_to_owning_cluster(tmp_path: Path) -> None:
    """两个 cluster 存在同名 agent 时，Room UUID 必须只投递到所属 CoreUnit。"""

    class ProjectManagerStub:
        async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
            project = {
                "project_id": "project-1",
                "project_root_locator": "",
                "agents": [
                    {"agent_id": "a" * 32, "name": "planner", "cluster_id": "c1"},
                    {"agent_id": "b" * 32, "name": "planner", "cluster_id": "c2"},
                ],
            }
            if command == "project_list":
                return {"success": True, "data": {"projects": [project]}}
            if command == "project_get" and payload.get("project_id") == "project-1":
                return {"success": True, "data": {"project": project}}
            return {"success": False, "data": None, "error": "not found"}

    class HandleStub:
        def __init__(self) -> None:
            self.messages: list[Any] = []

        async def send_message(self, payload: Any) -> dict[str, Any]:
            self.messages.append(payload)
            return {"success": True, "data": {"delivered": True}, "error": None}

    class RegistryStub:
        def __init__(self) -> None:
            self.handles = {"c1": HandleStub(), "c2": HandleStub()}
            self.ensure_calls: list[str] = []

        async def ensure_cluster(
            self,
            cluster_id: str,
            *,
            project_id: str = "",
            project_root_locator: str = "",
        ) -> HandleStub:
            self.ensure_calls.append(cluster_id)
            return self.handles[cluster_id]

    class ContextStub:
        def __init__(self, registry: RegistryStub) -> None:
            self.services: dict[str, Any] = {
                "project_manager": ProjectManagerStub(),
                "core_cluster_registry": registry,
            }

        def get(self, name: str) -> Any:
            return self.services[name]

        def provide(self, name: str, value: Any) -> None:
            self.services[name] = value

        def emit(self, name: str, payload: dict[str, Any]) -> None:
            return None

    registry = RegistryStub()
    unit = RoomUnit(_config(tmp_path))
    await unit.init(ContextStub(registry))
    await unit.start()
    try:
        created = await unit.service.handle_command(
            "room_create", {"project_id": "project-1", "name": "routing"}
        )
        room_id = created["data"]["room"]["room_id"]
        ambiguous = await unit.handle_command(
            "room_join",
            {"room_id": room_id, "subject": "planner", "subject_type": "agent"},
            None,  # type: ignore[arg-type]
        )
        assert ambiguous["success"] is False
        joined = await unit.handle_command(
            "room_join",
            {"room_id": room_id, "subject": "b" * 32, "subject_type": "agent"},
            None,  # type: ignore[arg-type]
        )
        [member] = joined["data"]["room"]["members"]
        assert member["subject"] == "b" * 32
        assert member["subject_name"] == "planner"
        result = await unit.service._deliver("b" * 32, "human:yuki", "resume", room_id)
        assert result["success"] is True
        assert registry.ensure_calls == ["c2"]
        assert registry.handles["c1"].messages == []
        [message] = registry.handles["c2"].messages
        assert message.target == "planner"
        assert message.metadata == {
            "room_id": room_id,
            "project_id": "project-1",
            "agent_id": "b" * 32,
        }
    finally:
        await unit.stop()
