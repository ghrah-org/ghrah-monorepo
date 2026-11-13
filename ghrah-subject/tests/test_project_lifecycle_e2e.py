# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Project archive/restore Root freeze integration across Subject restarts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
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


async def test_subject_bootstrap_recovers_agent_and_message_history(tmp_path: Path) -> None:
    from ability_testutil import EchoLLM, put_agent_template
    from ghrah.ability.access import GrantPolicy

    from ghrah.subject.runtime.assembly import assemble_subject
    from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

    config = _config(tmp_path)
    config.ability_grant_policy = GrantPolicy(mode="merge")

    async def start(ctx):
        await assemble_subject(
            ctx,
            config,
            core_cluster_unit=CoreClusterRegistryUnit(config, llm_factory=lambda _: EchoLLM()),
        )
        async with asyncio.timeout(8):
            while (report := await ctx.get("reconciliation_service").reconcile_status()) is None:
                await asyncio.sleep(0.02)
        assert report.success, report.errors
        projects = await ctx.get("project_store").list()
        assert len(projects) == 1
        return projects[0]

    async with Context() as ctx:
        project = await start(ctx)
        project_id = project.project_id
        assert (tmp_path / "workspace" / "projects" / "default").is_dir()
        assert ctx.get("core_cluster_registry").cluster_ids() == [project_id]
        template = put_agent_template(ctx.get("manifest_store"))
        agents = []
        for name in ("worker", "partner"):
            added = _data(
                await bridge_command(
                    ctx,
                    "project_add_agent",
                    {
                        "project_id": project_id,
                        "agent": {"name": name, "manifest_ref": template},
                    },
                )
            )
            agents = added["project"]["agents"]
        source_id, target_id = [agent["agent_id"] for agent in agents]
        send = {
            "project_id": project_id,
            "agent_id": source_id,
            "target": agents[0]["name"],
            "content": "direct",
            "timeout": 5,
        }
        assert _data(await bridge_command(ctx, "send_message", send))["content"] == "ready"
        broadcast = _data(
            await bridge_command(
                ctx, "broadcast_message", {"project_id": project_id, "content": "broadcast"}
            )
        )
        replies = broadcast["clusters"][project_id]["data"]["responses"]
        assert {reply["responder"] for reply in replies} == {"worker", "partner"}
        assert (
            _data(
                await bridge_command(
                    ctx,
                    "delegate",
                    {
                        "project_id": project_id,
                        "from_agent_id": source_id,
                        "to_agent_id": target_id,
                        "content": "delegate",
                        "timeout": 5,
                    },
                )
            )["content"]
            == "ready"
        )

    # 模拟旧版留下的 cluster 列和 Agent JSON。先备份，再在副本上执行升级与重启。
    backup_path = tmp_path / "subject-before-upgrade.db"
    with sqlite3.connect(config.persistence.db_path) as db:
        db.execute("ALTER TABLE subject_projects ADD COLUMN cluster_ids TEXT NOT NULL DEFAULT '[]'")
        for agent, cluster_id in zip(agents, ("legacy-a", "legacy-b"), strict=True):
            agent["cluster_id"] = cluster_id
        db.execute(
            "UPDATE subject_projects SET agents=?, cluster_ids=? WHERE project_id=?",
            (json.dumps(agents), json.dumps(["legacy-a", "legacy-b"]), project_id),
        )
        db.commit()
        with sqlite3.connect(backup_path) as backup:
            db.backup(backup)

    async with Context() as ctx:
        restored = await start(ctx)
        assert restored.project_id == project_id
        assert restored.project_root_locator == project.project_root_locator
        port = ctx.get("core.port").project(project_id)
        assert set((await port.agents.list()).items) == {source_id, target_id}
        catalog = await port.sessions.catalog(agent_id=source_id)
        branch = next(b for b in catalog.branches if b.session_id == catalog.active_session_id)
        history = _data(
            await bridge_command(
                ctx,
                "get_chain_history",
                {
                    "project_id": project_id,
                    "agent_id": source_id,
                    "agent_name": "worker",
                    "session_id": branch.session_id,
                    "branch_id": branch.branch_id,
                },
            )
        )
        assert any("direct" in str(node) for node in history["nodes"])
        branch_command = {
            "project_id": project_id,
            "agent_id": source_id,
            "agent_name": "worker",
            "session_id": branch.session_id,
        }
        new_branch = _data(
            await bridge_command(ctx, "branch_create", {**branch_command, "name": "follow-up"})
        )["branch_id"]
        _data(
            await bridge_command(
                ctx, "branch_activate", {**branch_command, "branch_id": new_branch}
            )
        )
        _data(
            await bridge_command(
                ctx, "branch_activate", {**branch_command, "branch_id": branch.branch_id}
            )
        )
        _data(
            await bridge_command(ctx, "branch_delete", {**branch_command, "branch_id": new_branch})
        )
        stopped = _data(
            await bridge_command(
                ctx,
                "agent_entity",
                {"project_id": project_id, "agent_id": target_id, "operation": {"type": "stop"}},
            )
        )
        assert stopped["runtime"]["availability"] == "offline"

    async with Context() as ctx:
        project = await start(ctx)
        assert project.project_id == project_id
        port = ctx.get("core.port").project(project_id)
        assert (await port.agents.list()).items == [source_id]
        assert (
            next(agent for agent in project.agents if agent.agent_id == target_id).desired_state
            == "stopped"
        )

    with sqlite3.connect(backup_path) as backup:
        stored_agents, clusters = backup.execute(
            "SELECT agents, cluster_ids FROM subject_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        assert [agent["agent_id"] for agent in json.loads(stored_agents)] == [source_id, target_id]
        assert json.loads(clusters) == ["legacy-a", "legacy-b"]


async def test_reconcile_reports_legacy_name_collisions_without_spawning(tmp_path: Path) -> None:
    from ghrah.subject.runtime.assembly import assemble_subject

    config = _config(tmp_path)
    config.recovery.reconcile_on_start = False
    async with Context() as ctx:
        await assemble_subject(ctx, config)
        project = _data(await bridge_command(ctx, "project_create", {"name": "legacy"}))["project"]
        project_id = project["project_id"]
        with sqlite3.connect(config.persistence.db_path) as db:
            agents = [
                {
                    "name": "worker",
                    "agent_id": agent_id,
                    "cluster_id": cluster,
                    "manifest_ref": "tests.agent",
                }
                for agent_id, cluster in (("old-id", "legacy-a"), ("", "legacy-b"))
            ]
            db.execute(
                "UPDATE subject_projects SET agents=? WHERE project_id=?",
                (json.dumps(agents), project_id),
            )
        report = await ctx.get("reconciliation_service").reconcile()
        assert not report.success
        assert report.agents_failed == 2
        assert {item["agent_id"] for item in report.agent_results} == {"old-id", ""}
        assert all(project_id in message and "worker" in message for message in report.errors)
        assert (await ctx.get("core.port").project(project_id).agents.list()).items == []
