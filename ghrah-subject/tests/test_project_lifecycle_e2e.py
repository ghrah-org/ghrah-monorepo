# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project archive/restore Root freeze integration across Subject restarts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
