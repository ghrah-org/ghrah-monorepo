# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project archive/restore Root freeze integration across Subject restarts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import ProjectConfig, SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.units import mount_builtin_units


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        project_slice=ProjectConfig(
            default_root_locator_template=str(tmp_path / "projects/{project_id}")
        ),
    )


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def test_archived_project_stays_frozen_after_restart_until_restore(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        project = _data(
            await bridge_command(
                ctx,
                "project_create",
                {"name": "restart-frozen", "writable_workspaces": []},
            )
        )["project"]
        task = _data(
            await bridge_command(
                ctx,
                "task_create",
                {"title": "task", "project_id": project["project_id"]},
            )
        )["task"]
        room = _data(
            await bridge_command(
                ctx,
                "room_create",
                {"name": "room", "project_id": project["project_id"]},
            )
        )["room"]

        entry = _data(
            await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": room["room_id"],
                    "author": "human:user",
                    "author_type": "human",
                    "data": {
                        "message": "更新需求数量",
                        "chat_action": {
                            "action": "requirement.update",
                            "payload": {"change_request": "数量改为 30"},
                        },
                    },
                },
            )
        )["entry"]
        actions = _data(
            await bridge_command(ctx, "chat_action_list", {"room_id": room["room_id"], "limit": 20})
        )["actions"]
        assert len(actions) == 1
        assert actions[0]["action"] == "requirement.update"
        assert actions[0]["status"] == "sent"
        assert actions[0]["room_log_id"] == entry["id"]
        invalid = await bridge_command(
            ctx,
            "room_send",
            {
                "room_id": room["room_id"],
                "author": "human:user",
                "author_type": "human",
                "data": {
                    "message": "非法动作不应落库",
                    "chat_action": {"action": "", "payload": {}},
                },
            },
        )
        assert invalid["success"] is False
        assert invalid["error"].startswith("invalid payload:")
        persisted = _data(
            await bridge_command(ctx, "chat_action_list", {"room_id": room["room_id"], "limit": 20})
        )["actions"]
        assert len(persisted) == 1
        archived = _data(
            await bridge_command(
                ctx,
                "project_archive",
                {
                    "project_id": project["project_id"],
                    "expected_version": project["version"],
                },
            )
        )["project"]

    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        task_store = ctx.get("task_store")
        room_store = ctx.get("room_store")
        assert project["project_id"] in task_store._frozen_projects
        assert project["project_id"] in room_store._frozen_projects
        assert task_store._known_roots.get(project["project_id"]) == project["project_root_locator"]
        assert task_store._task_projects.get(task["task_id"]) == project["project_id"]
        assert room_store._room_projects.get(room["room_id"]) == project["project_id"]
        archived_list = _data(await bridge_command(ctx, "project_list", {"archived": True}))
        assert [item["project_id"] for item in archived_list["projects"]] == [project["project_id"]]
        blocked_task = await bridge_command(ctx, "task_get", {"task_id": task["task_id"]})
        blocked_room = await bridge_command(ctx, "room_get", {"room_id": room["room_id"]})
        assert blocked_task["error"] == "resource_archived"
        assert blocked_room["error"] == "project_archived"

        restored = _data(
            await bridge_command(
                ctx,
                "project_restore",
                {
                    "project_id": project["project_id"],
                    "expected_version": archived["version"],
                },
            )
        )["project"]
        assert restored["archived_at"] is None
        assert restored["status"] == "stopped"
        assert (
            _data(await bridge_command(ctx, "task_get", {"task_id": task["task_id"]}))["task"][
                "task_id"
            ]
            == task["task_id"]
        )
        assert (
            _data(await bridge_command(ctx, "room_get", {"room_id": room["room_id"]}))["room"][
                "room_id"
            ]
            == room["room_id"]
        )
        restored_actions = _data(
            await bridge_command(ctx, "chat_action_list", {"room_id": room["room_id"], "limit": 20})
        )["actions"]
        assert [item["action_id"] for item in restored_actions] == [
            item["action_id"] for item in persisted
        ]


async def test_project_rest_lifecycle_e2e(tmp_path: Path) -> None:
    """REST 全流程（真实 ASGI + 完整 unit 装配）。

    建项目 → 加房间 → 归档 → 恢复 → 删除门禁（409 + 房间数）→ 级联删除；
    断言 ``project_*`` 事件仍触发、desired-state 投影沿用命令总线、Root 被清理。
    """
    from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT
    from ghrah.subject.workspace.locator import locator_to_path

    config = _config(tmp_path)
    async with Context() as ctx:
        events: list[str] = []

        def collect(name: str):
            def append(_payload: Any) -> None:
                events.append(name)

            return append

        for event_name in (
            "project_created",
            "project_archived",
            "project_restored",
            "project_deleted",
        ):
            ctx.on(f"event/{event_name}", collect(event_name))

        await mount_builtin_units(ctx, config, profile="full")
        app = ctx.get(OBSERVER_ENDPOINT.name).app
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/projects",
                json={"name": "rest-e2e", "writable_workspaces": []},
            )
            assert created.status_code == 201, created.text
            project = created.json()
            project_id = project["project_id"]
            assert created.headers["location"] == f"/api/v1/projects/{project_id}"

            attachment_url = f"/api/v1/projects/{project_id}/attachments"
            upload = await client.post(
                attachment_url, content=b"video", headers={"Content-Type": "video/mp4"}
            )
            assert upload.status_code == 201, upload.text
            attachment_id = upload.json()["attachment_id"]

            room = _data(
                await bridge_command(
                    ctx,
                    "room_create",
                    {"name": "room", "project_id": project_id},
                )
            )["room"]

            archived = await client.post(
                f"/api/v1/projects/{project_id}/archive",
                json={"expected_version": project["version"]},
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["archived_at"] is not None

            rejected_upload = await client.post(
                attachment_url, content=b"new video", headers={"Content-Type": "video/mp4"}
            )
            assert rejected_upload.status_code == 409, rejected_upload.text
            assert rejected_upload.json()["error"]["code"] == "project_archived"
            saved_video = await client.get(f"{attachment_url}/{attachment_id}")
            assert saved_video.content == b"video"

            restored = await client.post(
                f"/api/v1/projects/{project_id}/restore",
                json={"expected_version": archived.json()["version"]},
            )
            assert restored.status_code == 200, restored.text
            assert restored.json()["archived_at"] is None

            blocked = await client.delete(
                f"/api/v1/projects/{project_id}",
                params={"expected_version": restored.json()["version"]},
            )
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["error"]["detail"]["room_count"] == 1

            deleted = await client.delete(
                f"/api/v1/projects/{project_id}",
                params={
                    "expected_version": restored.json()["version"],
                    "cascade_rooms": "true",
                },
            )
            assert deleted.status_code == 200, deleted.text
            deleted_body = deleted.json()
            assert deleted_body["deleted"] is True
            assert deleted_body["storage_purged"] is True
            assert deleted_body["rooms_deleted"] == 1

            gone = await client.get(f"/api/v1/projects/{project_id}")
            assert gone.status_code == 404
            assert (await client.get(f"{attachment_url}/{attachment_id}")).status_code == 404

        assert room["room_id"]
        assert events == [
            "project_created",
            "project_archived",
            "project_restored",
            "project_deleted",
        ]
        assert not Path(locator_to_path(project["project_root_locator"])).exists()
