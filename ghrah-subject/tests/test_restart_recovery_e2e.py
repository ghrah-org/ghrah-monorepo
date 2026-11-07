# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""真实单进程 Subject 重启恢复验收。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from ghrah.ability.access import GrantPolicy
from ghrah.chat.message import ChatMessage
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import ProjectConfig, RecoveryConfig, SubjectConfig
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        recovery_slice=RecoveryConfig(
            enabled=True,
            reconcile_on_start=True,
            bootstrap_default_project=False,
        ),
        project_slice=ProjectConfig(
            bootstrap_workspace_locator=str(tmp_path / "ws/default"),
            default_root_locator_template=str(tmp_path / "projects/{project_id}"),
        ),
    )


def _write_agent_manifest(ctx: Context) -> None:
    ctx.get("manifest_store").put_agent(
        "recovery.agent",
        """\
manifest: agent
version: "2"
metadata:
  namespace: recovery
  name: agent
model:
  endpoint_name: default
system_prompt: Test recovery.
abilities:
  - ref: ghrah.core.session_info
""",
    )


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result["success"], result
    return result["data"]


def _actor(ctx: Context, cluster_id: str, name: str) -> Any:
    registry = ctx.get("core_cluster_registry")
    supervisor = registry.get_handle(cluster_id)._unit.supervisor
    return supervisor._registry.get_info(name).actor_handle


async def test_subject_restart_restores_two_durable_agents_from_project_store(
    tmp_path: Path,
) -> None:
    """销毁整个 Context 后，A/B 从各自完整 checkpoint 恢复并继续旧链。"""
    config = _config(tmp_path)
    before: dict[str, dict[str, Any]] = {}
    project_id = ""
    cluster_id = ""
    task_id = ""

    async with Context() as first_ctx:
        await assemble_subject(first_ctx, config, profile="full")
        _write_agent_manifest(first_ctx)
        project = _data(
            await bridge_command(first_ctx, "project_create", {"name": "recovery-e2e"})
        )["project"]
        project_id = project["project_id"]
        cluster_id = project["cluster_ids"][0]

        for name in ("planner", "reviewer"):
            added = _data(
                await bridge_command(
                    first_ctx,
                    "project_add_agent",
                    {
                        "project_id": project_id,
                        "agent": {
                            "name": name,
                            "cluster_id": cluster_id,
                            "manifest_ref": "recovery.agent",
                        },
                    },
                )
            )
            agent_id = added["agent_id"]
            actor = _actor(first_ctx, cluster_id, name)
            cm = actor._context_manager
            assert cm.agent_name == agent_id

            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"work:{name}")])
            cm.apply_state_changes({"phase": "waiting", "owner": name})
            cm.commit_iteration(ability_names=["conversation"])
            session = cm.create_session(
                metadata={"owner": name},
            )
            head = cm.active_head
            before[name] = {
                "agent_id": agent_id,
                "head_id": head.id,
                "node_ids": {
                    node.id
                    for runtime in cm._session_runtimes.values()
                    for node in runtime.chain.nodes
                },
                "session_ids": {s.session_id for s in cm.list_sessions()},
                "message_count": cm.message_count,
            }
            assert session.session_id in before[name]["session_ids"]

        # ProjectStore 已提交完整 durable 意图；DesiredStateStore 只是缓存。
        stored = _data(await bridge_command(first_ctx, "project_get", {"project_id": project_id}))[
            "project"
        ]
        assert {a["agent_id"] for a in stored["agents"]} == {
            item["agent_id"] for item in before.values()
        }
        task = _data(
            await bridge_command(
                first_ctx,
                "task_create",
                {
                    "project_id": project_id,
                    "title": "durable assignment",
                    "agent_name": "planner",
                },
            )
        )["task"]
        task_id = task["task_id"]
        assert task["agent_id"] == before["planner"]["agent_id"]
        assert task["agent_name"] == "planner"

    # first_ctx 已完整 dispose；以下使用全新的 Context/CoreUnit/Supervisor/backend。
    async with Context() as restarted_ctx:
        await assemble_subject(restarted_ctx, config, profile="full")
        report = _data(await bridge_command(restarted_ctx, "reconcile_status", {}))
        assert report["success"] is True
        assert report["agents_restored"] == 2
        assert report["agents_initialized"] == 0

        restored_project = _data(
            await bridge_command(restarted_ctx, "project_get", {"project_id": project_id})
        )["project"]
        assert restored_project["cluster_ids"] == [cluster_id]
        restored_task = _data(
            await bridge_command(restarted_ctx, "task_get", {"task_id": task_id})
        )["task"]
        assert restored_task["agent_id"] == before["planner"]["agent_id"]

        for name, expected in before.items():
            actor = _actor(restarted_ctx, cluster_id, name)
            cm = actor._context_manager
            assert cm.agent_name == expected["agent_id"]
            assert cm.active_head.id == expected["head_id"]
            restored_node_ids = {
                node.id for runtime in cm._session_runtimes.values() for node in runtime.chain.nodes
            }
            assert expected["node_ids"].issubset(restored_node_ids)
            assert cm.get_current_state() == {"phase": "waiting", "owner": name}
            assert cm.message_count == expected["message_count"]
            assert {s.session_id for s in cm.list_sessions()} == expected["session_ids"]

            old_head = cm.active_head
            cm.begin_iteration()
            continued = cm.commit_iteration(ability_names=["resume"])
            assert continued.parent_id == old_head.id

        second = _data(await bridge_command(restarted_ctx, "reconcile_now", {}))
        assert second["success"] is True
        assert second["agents_spawned"] == 0


async def test_corrupt_agent_snapshot_fails_closed_without_blocking_peer(
    tmp_path: Path,
) -> None:
    """单个损坏 checkpoint 不上线、不被覆盖，健康 Agent 仍正常恢复。"""
    config = _config(tmp_path)
    identities: dict[str, str] = {}
    cluster_id = ""
    action_db: Path | None = None

    async with Context() as first_ctx:
        await assemble_subject(first_ctx, config, profile="full")
        _write_agent_manifest(first_ctx)
        project = _data(
            await bridge_command(first_ctx, "project_create", {"name": "corruption-e2e"})
        )["project"]
        cluster_id = project["cluster_ids"][0]
        action_db = ProjectPaths.from_locator(project["project_root_locator"]).action_chain_db_path
        for name in ("healthy", "corrupt"):
            added = _data(
                await bridge_command(
                    first_ctx,
                    "project_add_agent",
                    {
                        "project_id": project["project_id"],
                        "agent": {
                            "name": name,
                            "cluster_id": cluster_id,
                            "manifest_ref": "recovery.agent",
                        },
                    },
                )
            )
            identities[name] = added["agent_id"]
            cm = _actor(first_ctx, cluster_id, name)._context_manager
            cm.begin_iteration()
            cm.apply_state_changes({"owner": name})
            cm.commit_iteration(ability_names=[])

    assert action_db is not None
    with sqlite3.connect(action_db) as conn:
        old_node_count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE agent_name = ?",
            (identities["corrupt"],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE sessions SET active_branch_id = ? WHERE agent_name = ?",
            ("missing-branch", identities["corrupt"]),
        )
        conn.commit()

    async with Context() as restarted_ctx:
        await assemble_subject(restarted_ctx, config, profile="full")
        report = _data(await bridge_command(restarted_ctx, "reconcile_status", {}))
        assert report["success"] is False
        assert report["agents_restored"] == 1
        assert report["agents_failed"] == 1
        assert any(
            item["agent_id"] == identities["corrupt"] and item["outcome"] == "failed"
            for item in report["agent_results"]
        )

        registry = restarted_ctx.get("core_cluster_registry")
        core_registry = registry.get_handle(cluster_id)._unit.supervisor._registry
        assert core_registry.exists("healthy")
        assert not core_registry.exists("corrupt")
        healthy_cm = _actor(restarted_ctx, cluster_id, "healthy")._context_manager
        assert healthy_cm.get_current_state() == {"owner": "healthy"}

        # fail-closed：损坏快照的 meta 与旧节点均未被空白初始化覆盖。
        with sqlite3.connect(action_db) as conn:
            active_branch_id = conn.execute(
                "SELECT active_branch_id FROM sessions WHERE agent_name = ?",
                (identities["corrupt"],),
            ).fetchone()[0]
            node_count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE agent_name = ?",
                (identities["corrupt"],),
            ).fetchone()[0]
        assert active_branch_id == "missing-branch"
        assert node_count == old_node_count


async def test_restart_migrates_unique_legacy_name_snapshot_to_stable_uuid(
    tmp_path: Path,
) -> None:
    """旧 ProjectSpec/name-key DB 在 reconcile 前备份、重键并恢复。"""
    pytest.skip("v2 schema deliberately does not migrate pre-production databases")
    config = _config(tmp_path)
    project_id = ""
    cluster_id = ""
    old_agent_id = ""
    old_head_id = ""
    action_db: Path | None = None

    async with Context() as first_ctx:
        await assemble_subject(first_ctx, config, profile="full")
        project = _data(await bridge_command(first_ctx, "project_create", {"name": "legacy-e2e"}))[
            "project"
        ]
        project_id = project["project_id"]
        cluster_id = project["cluster_ids"][0]
        action_db = ProjectPaths.from_locator(project["project_root_locator"]).action_chain_db_path
        added = _data(
            await bridge_command(
                first_ctx,
                "project_add_agent",
                {
                    "project_id": project_id,
                    "agent": {"name": "legacy", "cluster_id": cluster_id},
                },
            )
        )
        old_agent_id = added["agent_id"]
        cm = _actor(first_ctx, cluster_id, "legacy")._context_manager
        cm.begin_iteration()
        cm.apply_state_changes({"legacy": True})
        old_head_id = cm.commit_iteration(ability_names=[]).id

    assert action_db is not None
    # 模拟升级前现场：ProjectSpec 无 agent_id，Core 表仍以 name 为关联键。
    with sqlite3.connect(config.persistence.db_path) as db:
        agents = json.loads(
            db.execute(
                "SELECT agents FROM subject_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        )
        agents[0]["agent_id"] = ""
        db.execute(
            "UPDATE subject_projects SET agents = ? WHERE project_id = ?",
            (json.dumps(agents), project_id),
        )
        db.commit()
    with sqlite3.connect(action_db) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        for table in ("sessions", "nodes", "chain_meta", "messages", "agents"):
            db.execute(
                f"UPDATE {table} SET agent_name = ? WHERE agent_name = ?",
                ("legacy", old_agent_id),
            )
        db.commit()

    async with Context() as restarted_ctx:
        await assemble_subject(restarted_ctx, config, profile="full")
        report = _data(await bridge_command(restarted_ctx, "reconcile_status", {}))
        assert report["success"] is True
        assert report["agents_restored"] == 1

        project = _data(
            await bridge_command(restarted_ctx, "project_get", {"project_id": project_id})
        )["project"]
        migrated_agent_id = project["agents"][0]["agent_id"]
        assert len(migrated_agent_id) == 32
        assert migrated_agent_id != old_agent_id
        cm = _actor(restarted_ctx, cluster_id, "legacy")._context_manager
        assert cm.agent_name == migrated_agent_id
        assert cm.chain.active_head is not None
        assert cm.chain.active_head.id == old_head_id
        assert cm.get_current_state() == {"legacy": True}
        assert action_db.with_name(f"{action_db.name}.pre-agent-id-migration.bak").is_file()
