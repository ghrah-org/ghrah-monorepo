# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""端到端冒烟（进程内，任务 7.2）：装配态下 Observer 命令路径全链路。

不起 uvicorn/WS（WebUI 重构期）；在已装配的 ctx 上经 bridge_command
（= Observer router 的 _EngineDispatchAdapter 路径）发命令，验证：
- spawn_agent → CoreUnit 实例（registry 懒挂载）→ agent 注册
- list_agents → 进程内直达
- core:agent_spawned 事件经 ctx.emit（observer 订阅链路验证）
- terminate_agent → agent 注销 + core:agent_terminated 事件
- chain_history 读侧直连 Core sqlite 连通

真实 LLM 不参与（不跑 agent loop）；spawn 使用最小 v2 Agent 模板。
HITL 单路径（core:hitl_request）的端到端归
WebUI 联调，本文件覆盖装配闭环。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ghrah.ability.access import GrantPolicy
from ghrah.protocol.types import EventType
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import ProjectConfig, SubjectConfig
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        ability_grant_policy=GrantPolicy(mode="merge"),
        project_slice=ProjectConfig(
            bootstrap_workspace_locator=str(tmp_path / "ws/projects/default"),
            default_root_locator_template=str(tmp_path / "projects/{project_id}"),
        ),
    )


@asynccontextmanager
async def _stack(tmp_path: Path) -> AsyncIterator[Context]:
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        ctx.get("manifest_store").put_agent(
            "smoke.agent",
            """\
manifest: agent
version: "2"
metadata:
  namespace: smoke
  name: agent
model:
  endpoint_name: default
system_prompt: Test agent.
abilities:
  - ref: ghrah.core.session_info
""",
        )
        yield ctx


async def _default_project(ctx: Context) -> dict:
    result = await bridge_command(ctx, "project_list", {})
    assert result["success"]
    return result["data"]["projects"][0]


class TestEndToEndSmoke:
    async def test_same_agent_name_isolated_across_projects(self, tmp_path: Path) -> None:
        async with _stack(tmp_path) as ctx:
            first = await _default_project(ctx)
            created = await bridge_command(
                ctx,
                "project_create",
                {"name": "second", "writable_workspaces": []},
            )
            assert created["success"], created.get("error")
            second = created["data"]["project"]

            spawned = []
            for project in (first, second):
                result = await bridge_command(
                    ctx,
                    "spawn_agent",
                    {
                        "project_id": project["project_id"],
                        "manifest_ref": "smoke.agent",
                        "config": {"name": "shared-name"},
                    },
                )
                assert result["success"], result.get("error")
                spawned.append(result["data"])

            assert spawned[0]["agent_id"] != spawned[1]["agent_id"]
            assert spawned[0]["cluster_id"] != spawned[1]["cluster_id"]
            for project, expected in zip((first, second), spawned, strict=True):
                listed = await bridge_command(
                    ctx, "list_agents", {"project_id": project["project_id"]}
                )
                assert [agent["agent_id"] for agent in listed["data"]["agents"]] == [
                    expected["agent_id"]
                ]

    async def test_spawn_list_terminate_via_observer_command_path(self, tmp_path: Path) -> None:
        """spawn → list → terminate 全经 bridge_command（Observer 命令路径）。"""
        async with _stack(tmp_path) as ctx:
            project = await _default_project(ctx)
            # spawn_agent 经 Observer 命令路径 → ctx.serial → CoreUnit
            spawn = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "manifest_ref": "smoke.agent",
                    "config": {"name": "smoke-agent"},
                },
            )
            assert spawn["success"], spawn.get("error")
            assert spawn["data"]["name"] == "smoke-agent"

            # list_agents
            agent_id = spawn["data"]["agent_id"]
            listed = await bridge_command(ctx, "list_agents", {"project_id": project["project_id"]})
            assert listed["success"]
            names = [a["name"] for a in listed["data"]["agents"]]
            assert "smoke-agent" in names

            # terminate_agent
            term = await bridge_command(
                ctx,
                "terminate_agent",
                {
                    "project_id": project["project_id"],
                    "agent_id": agent_id,
                    "name": "smoke-agent",
                },
            )
            assert term["success"]

            listed_after = await bridge_command(
                ctx, "list_agents", {"project_id": project["project_id"]}
            )
            stopped = next(
                agent for agent in listed_after["data"]["agents"] if agent["agent_id"] == agent_id
            )
            assert stopped["runtime_state"] == "stopped"

    async def test_core_events_reach_ctx_emit(self, tmp_path: Path) -> None:
        """core:agent_spawned / agent_terminated 事件经 ctx.emit（observer 订阅源）。"""
        async with _stack(tmp_path) as ctx:
            project = await _default_project(ctx)
            events: list[tuple[str, dict]] = []
            for et in (EventType.AGENT_SPAWNED, EventType.AGENT_TERMINATED):
                ctx.on(
                    f"core:{et.value}",
                    lambda payload, n=et.value: events.append((n, payload)),
                )

            spawned = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "manifest_ref": "smoke.agent",
                    "config": {"name": "evt-agent"},
                },
            )
            await bridge_command(
                ctx,
                "terminate_agent",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "name": "evt-agent",
                },
            )

            event_names = [n for n, _ in events]
            assert EventType.AGENT_SPAWNED.value in event_names
            assert EventType.AGENT_TERMINATED.value in event_names
            spawn_evt = next(p for n, p in events if n == EventType.AGENT_SPAWNED.value)
            assert spawn_evt["name"] == "evt-agent"
            assert spawn_evt["project_id"] == project["project_id"]
            assert spawn_evt["agent_id"] == spawned["data"]["agent_id"]
            assert spawn_evt["cluster_id"] == project["cluster_ids"][0]

    async def test_chain_history_read_side_connected(self, tmp_path: Path) -> None:
        """chain_history 读侧直连 Core sqlite（聚合裁决 D-C 闭环）。"""
        async with _stack(tmp_path) as ctx:
            project = await _default_project(ctx)
            spawned = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "manifest_ref": "smoke.agent",
                    "config": {"name": "ledger-smoke"},
                },
            )
            sessions = await bridge_command(
                ctx,
                "session_list",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "agent_name": "ledger-smoke",
                },
            )
            session = sessions["data"]["sessions"][0]
            branches = await bridge_command(
                ctx,
                "branch_list",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "agent_name": "ledger-smoke",
                    "session_id": session["session_id"],
                },
            )
            branch = branches["data"]["branches"][0]
            # 经 Observer 命令路径查 chain_history（ledger unit 路由）
            result = await bridge_command(
                ctx,
                "get_chain_history",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "agent_name": "ledger-smoke",
                    "session_id": session["session_id"],
                    "branch_id": branch["branch_id"],
                },
            )
            assert result["success"]
            # P2a 新契约：spawn 即首次 persist——根节点当场落库可读
            nodes = result["data"]["nodes"]
            assert len(nodes) == 1
            assert nodes[0]["parent_id"] is None

    async def test_health_check_via_observer_path(self, tmp_path: Path) -> None:
        """health_check 经 Observer 命令路径直达 CoreUnit。"""
        async with _stack(tmp_path) as ctx:
            result = await bridge_command(ctx, "health_check", {})
            assert result["success"]

    async def test_session_lifecycle_via_observer_path(self, tmp_path: Path) -> None:
        """session_create/list 经 Observer 命令路径管理独立 Root。"""
        async with _stack(tmp_path) as ctx:
            project = await _default_project(ctx)
            spawned = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": project["project_id"],
                    "manifest_ref": "smoke.agent",
                    "config": {"name": "sess-agent"},
                },
            )
            create = await bridge_command(
                ctx,
                "session_create",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "agent_name": "sess-agent",
                },
            )
            assert create["success"], create.get("error")
            assert "session_id" in create["data"]

            listed = await bridge_command(
                ctx,
                "session_list",
                {
                    "project_id": project["project_id"],
                    "agent_id": spawned["data"]["agent_id"],
                    "agent_name": "sess-agent",
                },
            )
            assert listed["success"]
