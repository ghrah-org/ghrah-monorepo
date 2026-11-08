# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""TaskUnit integration tests (S3a.5 routing/broadcast acceptance, Ouroboros 形态)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ability_testutil import put_agent_template
from ghrah.ability.access import GrantPolicy
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import ProjectConfig, SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.task.manager import TaskManager
from ghrah.subject.units import mount_builtin_units


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        project_slice=ProjectConfig(
            default_root_locator_template=str(tmp_path / "projects/{project_id}")
        ),
    )


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


async def _dispatch(ctx: Context, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await bridge_command(ctx, command, payload)


async def test_task_unit_registered_and_service_available(tmp_path: Path) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="coexistence")

        manager = ctx.get("task_manager")
        assert isinstance(manager, TaskManager)


async def test_task_create_via_dispatcher_routes_to_taskunit(
    tmp_path: Path,
) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="coexistence")

        result = await _dispatch(
            ctx,
            "task_create",
            {"title": "alpha task", "project_id": "proj-1", "agent_name": "alpha"},
        )
        assert result["success"]
        task = result["data"]["task"]
        assert task["title"] == "alpha task"
        assert len(task["task_id"]) == 32
        assert task["status"] == "pending"


async def test_task_event_broadcast_direct(tmp_path: Path) -> None:
    async with Context() as ctx:
        emitted: list[tuple[str, dict[str, Any]]] = []

        def collect(payload: Any) -> None:
            emitted.append(("task_created", payload))

        ctx.on("event/task_created", collect)

        await mount_builtin_units(ctx, _config(tmp_path), profile="coexistence")

        result = await _dispatch(
            ctx,
            "task_create",
            {"title": "t", "project_id": "proj-1", "agent_name": "alpha"},
        )
        task = result["data"]["task"]
        assert emitted, "expected a direct event/task_created broadcast"
        event_type, payload = emitted[-1]
        assert event_type == "task_created"
        assert payload["task"]["task_id"] == task["task_id"]
        # 顶层 agent_name hoist
        assert payload["agent_name"] == "alpha"
        # wire 形态：无 version / deleted_at
        assert "version" not in payload["task"]
        assert "deleted_at" not in payload["task"]


async def test_end_to_end_lifecycle_protected_delete(tmp_path: Path) -> None:
    async with Context() as ctx:
        await mount_builtin_units(ctx, _config(tmp_path), profile="coexistence")

        b = _data(
            await _dispatch(
                ctx,
                "task_create",
                {"title": "b", "project_id": "proj-1", "agent_name": "a"},
            )
        )["task"]
        a = _data(
            await _dispatch(
                ctx,
                "task_create",
                {
                    "title": "a",
                    "project_id": "proj-1",
                    "dependencies": [b["task_id"]],
                    "agent_name": "a",
                },
            )
        )["task"]

        # start a 被拒：依赖未完成
        blocked = await _dispatch(ctx, "task_start", {"task_id": a["task_id"]})
        assert not blocked["success"]
        assert "dependencies not completed" in blocked["error"]

        # 完成 b 后 start a 成功
        await _dispatch(ctx, "task_complete", {"task_id": b["task_id"]})
        started = _data(await _dispatch(ctx, "task_start", {"task_id": a["task_id"]}))
        assert started["task"]["status"] == "in_progress"
        assert started["task"]["started_at"] is not None

        # 完成 a
        await _dispatch(ctx, "task_complete", {"task_id": a["task_id"]})

        # 保护删除 b：仍有 dependents（a 未软删）被拒
        protected = await _dispatch(ctx, "task_delete", {"task_id": b["task_id"]})
        assert not protected["success"]
        assert "dependents" in protected["error"]

        # force 软删 b 成功
        deleted = _data(
            await _dispatch(ctx, "task_delete", {"task_id": b["task_id"], "force": True})
        )
        assert deleted["task_id"] == b["task_id"]

        # 删后 get / list 不返回 b
        miss = await _dispatch(ctx, "task_get", {"task_id": b["task_id"]})
        assert not miss["success"]
        listing = _data(await _dispatch(ctx, "task_list", {"include_terminal": True, "limit": 100}))
        ids = {t["task_id"] for t in listing["tasks"]}
        assert b["task_id"] not in ids


async def test_archived_project_freezes_task_root_until_restore(tmp_path: Path) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        project = _data(
            await _dispatch(
                ctx,
                "project_create",
                {"name": "frozen", "writable_workspaces": []},
            )
        )["project"]
        task = _data(
            await _dispatch(
                ctx,
                "task_create",
                {"title": "inside root", "project_id": project["project_id"]},
            )
        )["task"]

        archived = _data(
            await _dispatch(
                ctx,
                "project_archive",
                {
                    "project_id": project["project_id"],
                    "expected_version": project["version"],
                },
            )
        )["project"]
        blocked_create = await _dispatch(
            ctx,
            "task_create",
            {"title": "blocked", "project_id": project["project_id"]},
        )
        assert blocked_create["error"] == "resource_archived"
        blocked_get = await _dispatch(ctx, "task_get", {"task_id": task["task_id"]})
        assert blocked_get["error"] == "resource_archived"

        restored = _data(
            await _dispatch(
                ctx,
                "project_restore",
                {
                    "project_id": project["project_id"],
                    "expected_version": archived["version"],
                },
            )
        )["project"]
        assert restored["status"] == "stopped"
        assert (
            _data(await _dispatch(ctx, "task_get", {"task_id": task["task_id"]}))["task"]["task_id"]
            == task["task_id"]
        )


async def test_task_rejects_agent_owned_by_another_project(tmp_path: Path) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        await mount_builtin_units(ctx, config, profile="full")
        projects = []
        for name in ("one", "two"):
            projects.append(
                _data(
                    await _dispatch(
                        ctx,
                        "project_create",
                        {"name": name, "writable_workspaces": []},
                    )
                )["project"]
            )
        spawned = _data(
            await _dispatch(
                ctx,
                "spawn_agent",
                {
                    "project_id": projects[0]["project_id"],
                    "config": {"name": "owned-by-one"},
                    "manifest_ref": put_agent_template(ctx.get("manifest_store")),
                },
            )
        )
        task = _data(
            await _dispatch(
                ctx,
                "task_create",
                {"title": "two task", "project_id": projects[1]["project_id"]},
            )
        )["task"]
        rejected = await _dispatch(
            ctx,
            "task_assign",
            {
                "task_id": task["task_id"],
                "agent_id": spawned["agent_id"],
                "agent_name": "owned-by-one",
            },
        )
        assert rejected["success"] is False
        assert rejected["error"] == "agent_project_mismatch"
