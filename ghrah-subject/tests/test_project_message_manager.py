# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""生产项目消息装配：多集群共享、项目隔离和租约释放。"""

from __future__ import annotations

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.chat.content import TextBlock
from ghrah.chat.format import LLMResponse
from ghrah.core.config import AgentConfig
from ouroboros import Context

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry, default_core_unit_factory


class EchoLLM:
    model = "project-message-test"

    def configure_tools(self, tools):
        pass

    def apply_model_overrides(self, overrides):
        pass

    async def generate(self, messages, tools=None):
        return LLMResponse(content_blocks=[TextBlock(text="ready")])


async def test_project_clusters_share_manager_and_last_shutdown_releases_owner(tmp_path):
    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"), db_path=str(tmp_path / "subject.sqlite")
    )
    factory = default_core_unit_factory(config, llm_factory=lambda config: EchoLLM())
    registry = CoreClusterRegistry(unit_factory=factory)
    async with Context() as ctx:
        registry.bind(ctx)
        root = str(tmp_path / "project")
        try:
            first = await registry.ensure_cluster("c1", project_id="p", project_root_locator=root)
            second = await registry.ensure_cluster("c2", project_id="p", project_root_locator=root)
            manager = registry.get_message_manager("p")
            assert first._unit.supervisor.message_manager is second._unit.supervisor.message_manager
            await first._unit.supervisor.spawn_agent(
                AgentConfig(name="a"), abilities=[ConversationAbility()]
            )
            await second._unit.supervisor.spawn_agent(
                AgentConfig(name="b"), abilities=[ConversationAbility()]
            )
            await registry.shutdown_cluster("c1")
            assert registry.get_message_manager("p") is manager
            assert await second._unit.supervisor.send("b", "start") == "ready"
            await registry.shutdown_cluster("c2")
            assert manager._db is None
            third = await registry.ensure_cluster("c3", project_id="p", project_root_locator=root)
            assert third._unit.supervisor.message_manager is not manager
            await third._unit.supervisor.spawn_agent(
                AgentConfig(name="b"), abilities=[ConversationAbility()]
            )
            assert third._unit.supervisor.recovery_mode("b") == "restored"
        finally:
            await registry.stop()


async def test_projects_with_same_agent_name_have_independent_message_authorities(tmp_path):
    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"), db_path=str(tmp_path / "subject.sqlite")
    )
    registry = CoreClusterRegistry(
        unit_factory=default_core_unit_factory(config, llm_factory=lambda config: EchoLLM())
    )
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            one = await registry.ensure_cluster(
                "c1", project_id="p1", project_root_locator=str(tmp_path / "p1")
            )
            two = await registry.ensure_cluster(
                "c2", project_id="p2", project_root_locator=str(tmp_path / "p2")
            )
            for handle in (one, two):
                await handle._unit.supervisor.spawn_agent(
                    AgentConfig(name="worker"), abilities=[ConversationAbility()]
                )
                assert await handle._unit.supervisor.send("worker", "start") == "ready"
            assert registry.get_message_manager("p1") is not registry.get_message_manager("p2")
            assert (
                registry.get_message_manager("p1").db_path
                != registry.get_message_manager("p2").db_path
            )
        finally:
            await registry.stop()


async def test_room_tool_delivers_and_reference_projection_recovers_without_duplicate(tmp_path):
    import json

    import aiosqlite

    from ghrah.subject.config import RecoveryConfig
    from ghrah.subject.runtime.assembly import assemble_subject
    from ghrah.subject.runtime.ouroboros_bridge import bridge_command
    from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.sqlite"),
        manifest_root=str(tmp_path / "manifests"),
        recovery_slice=RecoveryConfig(
            enabled=True, reconcile_on_start=False, bootstrap_default_project=False
        ),
    )
    async with Context() as ctx:
        await assemble_subject(
            ctx,
            config,
            profile="full",
            core_cluster_unit=CoreClusterRegistryUnit(config, llm_factory=lambda config: EchoLLM()),
        )
        project_result = await bridge_command(ctx, "project_create", {"name": "demo"})
        assert project_result["success"], project_result
        project = project_result["data"]["project"]
        pid = project["project_id"]
        agents = {}
        for name in ("a", "b"):
            result = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": pid,
                    "config": {"name": name},
                    "abilities": [
                        {"ability_type": "conversation", "params": {}},
                        {"ability_type": "send", "params": {}},
                    ],
                },
            )
            assert result["success"], result
            agents[name] = result["data"]["agent_id"]
        room_result = await bridge_command(ctx, "room_create", {"project_id": pid, "name": "room"})
        room_id = room_result["data"]["room"]["room_id"]
        for name in agents:
            joined = await bridge_command(
                ctx, "room_join", {"room_id": room_id, "subject": name, "subject_type": "agent"}
            )
            assert joined["success"], joined
        registry = ctx.get("core_cluster_registry")
        unit = registry.get_handle(project["cluster_ids"][0])._unit
        result = await unit.handle_command(
            "execute_ability",
            {
                "agent_name": "a",
                "ability_name": "send",
                "request_id": "room-tool-1",
                "tool_args": {"room_ids": [room_id], "message": "work", "targets": ["b"]},
            },
            None,
        )
        assert result["success"] and result["data"]["success"], result
        mm = registry.get_message_manager(pid)
        await mm.dispatch_pending()
        await mm.recover_room_projections()
        logs = await bridge_command(ctx, "room_get_log", {"room_id": room_id})
        entries = logs["data"]["entries"]
        assert [(e["author"], e["data"]["message"]) for e in entries] == [
            (agents["a"], "work"),
            (agents["b"], "ready"),
        ]
        stored = await ctx.get("room_store").get_log(room_id)
        assert all("message" not in entry.data for entry in stored)
        assert all(entry.data["mm_message_id"] for entry in stored)
        # 投影写成功但确认前退出的等价故障：仅重放投影，不重跑 agent。
        async with aiosqlite.connect(mm.db_path) as db:
            await db.execute("UPDATE mm_room_projections SET completed=0")
            await db.commit()
        await mm.recover_room_projections()
        after = await bridge_command(ctx, "room_get_log", {"room_id": room_id})
        assert after["data"]["count"] == 2
        assert [e["id"] for e in after["data"]["entries"]] == [e["id"] for e in entries]
        async with aiosqlite.connect(mm.db_path) as db:
            bodies = await (
                await db.execute(
                    "SELECT content, metadata FROM mm_messages WHERE message_id=?",
                    (stored[0].data["mm_message_id"],),
                )
            ).fetchone()
        assert bodies[0] == "work"
        assert json.loads(bodies[1])["room_ids"] == [room_id]
