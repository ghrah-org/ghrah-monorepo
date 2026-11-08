# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""生产项目消息装配：多集群共享、项目隔离和租约释放。"""

from __future__ import annotations

from ability_testutil import EchoLLM, put_agent_template
from ghrah.ability.access import GrantPolicy
from ghrah.protocol.payloads.agent import AgentConfigPayload, SpawnAgentPayload
from ouroboros import Context

from ghrah.subject.config import SubjectConfig
from ghrah.subject.core_cluster.registry import CoreClusterRegistry, default_core_unit_factory
from ghrah.subject.manifest_store.store import ManifestStore

SOURCE_ID = "a" * 32
TARGET_ID = "b" * 32


def _factory(tmp_path):
    config = SubjectConfig(
        workspace_root=str(tmp_path / "ws"),
        db_path=str(tmp_path / "subject.sqlite"),
        ability_grant_policy=GrantPolicy(mode="merge"),
    )
    store = ManifestStore(tmp_path / "manifests")
    store.ensure_dirs()
    put_agent_template(store)
    put_agent_template(store, "sender", members=("ghrah.cluster.send_message",))
    return default_core_unit_factory(config, store, llm_factory=lambda config: EchoLLM())


async def _spawn(handle, project_id, name, *, agent_id="", manifest_ref="tests.agent"):
    result = await handle.spawn_agent(
        SpawnAgentPayload(
            project_id=project_id,
            config=AgentConfigPayload(name=name, agent_id=agent_id),
            manifest_ref=manifest_ref,
        )
    )
    assert result["success"], result


async def test_project_clusters_share_manager_and_last_shutdown_releases_owner(tmp_path):
    registry = CoreClusterRegistry(unit_factory=_factory(tmp_path))
    async with Context() as ctx:
        registry.bind(ctx)
        root = str(tmp_path / "project")
        try:
            first = await registry.ensure_cluster("c1", project_id="p", project_root_locator=root)
            second = await registry.ensure_cluster("c2", project_id="p", project_root_locator=root)
            manager = registry.get_message_manager("p")
            assert first._unit.supervisor.message_manager is second._unit.supervisor.message_manager
            await _spawn(first, "p", "a")
            await _spawn(second, "p", "b", agent_id=TARGET_ID)
            await registry.shutdown_cluster("c1")
            assert registry.get_message_manager("p") is manager
            assert await second._unit.supervisor.send("b", "start") == "ready"
            await registry.shutdown_cluster("c2")
            assert manager._db is None
            third = await registry.ensure_cluster("c3", project_id="p", project_root_locator=root)
            assert third._unit.supervisor.message_manager is not manager
            await _spawn(third, "p", "b", agent_id=TARGET_ID)
            assert third._unit.supervisor.recovery_mode("b") == "restored"
        finally:
            await registry.stop()


async def test_projects_with_same_agent_name_have_independent_message_authorities(tmp_path):
    registry = CoreClusterRegistry(unit_factory=_factory(tmp_path))
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            one = await registry.ensure_cluster(
                "c1", project_id="p1", project_root_locator=str(tmp_path / "p1")
            )
            two = await registry.ensure_cluster(
                "c2", project_id="p2", project_root_locator=str(tmp_path / "p2")
            )
            for project_id, handle in (("p1", one), ("p2", two)):
                await _spawn(handle, project_id, "worker")
                assert await handle._unit.supervisor.send("worker", "start") == "ready"
            assert registry.get_message_manager("p1") is not registry.get_message_manager("p2")
            assert (
                registry.get_message_manager("p1").db_path
                != registry.get_message_manager("p2").db_path
            )
        finally:
            await registry.stop()


async def test_room_send_delivers_and_reference_projection_recovers_without_duplicate(tmp_path):
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
        ability_grant_policy=GrantPolicy(mode="merge"),
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
        ref = put_agent_template(ctx.get("manifest_store"))
        agents = {}
        for name in ("a", "b"):
            result = await bridge_command(
                ctx,
                "spawn_agent",
                {
                    "project_id": pid,
                    "config": {"name": name},
                    "manifest_ref": ref,
                },
            )
            assert result["success"] and not result["data"]["runtime_pending"], result
            agents[name] = result["data"]["agent_id"]
        room_result = await bridge_command(ctx, "room_create", {"project_id": pid, "name": "room"})
        room_id = room_result["data"]["room"]["room_id"]
        for name in agents:
            joined = await bridge_command(
                ctx, "room_join", {"room_id": room_id, "subject": name, "subject_type": "agent"}
            )
            assert joined["success"], joined
        registry = ctx.get("core_cluster_registry")
        result = await bridge_command(
            ctx,
            "room_send",
            {
                "room_id": room_id,
                "author": "tester",
                "author_type": "human",
                "data": {"message": "work", "targets": [agents["b"]]},
            },
        )
        assert result["success"], result
        mm = registry.get_message_manager(pid)
        await mm.dispatch_pending()
        await mm.recover_room_projections()
        logs = await bridge_command(ctx, "room_get_log", {"room_id": room_id})
        entries = logs["data"]["entries"]
        assert [(e["author"], e["data"]["message"]) for e in entries] == [
            ("tester", "work"),
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


async def test_observer_explicit_send_ability_cross_cluster(tmp_path):
    """观察端显式能力信封真实经过源 agent 原子提交和跨 cluster MM。"""
    import json

    registry = CoreClusterRegistry(unit_factory=_factory(tmp_path))
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            first = await registry.ensure_cluster(
                "c1", project_id="p", project_root_locator=str(tmp_path / "project")
            )
            second = await registry.ensure_cluster(
                "c2", project_id="p", project_root_locator=str(tmp_path / "project")
            )
            await _spawn(first, "p", "source", agent_id=SOURCE_ID, manifest_ref="tests.sender")
            await _spawn(second, "p", "target", agent_id=TARGET_ID)
            for async_mode in (False, True):
                response = await first.dispatch(
                    "send_message",
                    {
                        "project_id": "p",
                        "agent_id": SOURCE_ID,
                        "target": "source",
                        "sender": "user",
                        "content": "verify",
                        "metadata": {
                            "execute_ability": {
                                "name": "send_message",
                                "args": {
                                    "target": TARGET_ID,
                                    "content": "verify",
                                    "fire_and_forget": async_mode,
                                },
                                "call_id": f"verify-{async_mode}",
                            }
                        },
                    },
                )
                assert response["success"], response
                action = json.loads(response["data"]["content"])
                assert action["outcome"] == "success", action
                manager = registry.get_message_manager("p")
                message = await manager.get_message(action["data"]["message_id"])
                assert message.sender_id == SOURCE_ID
                assert message.origin_node_id
                deliveries = await manager.get_deliveries(message.message_id)
                assert [delivery.recipient_id for delivery in deliveries] == [TARGET_ID]
                if async_mode:
                    assert action["data"]["status"] == "prepared"
                else:
                    assert "ready" in json.dumps(action["data"])
        finally:
            await registry.stop()


async def test_missing_observer_ability_is_rejected_before_enqueue(tmp_path):
    """显式能力缺失立即返回可诊断错误，不制造阻塞同会话的失败消息。"""
    import aiosqlite

    registry = CoreClusterRegistry(unit_factory=_factory(tmp_path))
    async with Context() as ctx:
        registry.bind(ctx)
        try:
            handle = await registry.ensure_cluster(
                "c1", project_id="p", project_root_locator=str(tmp_path / "project")
            )
            await _spawn(handle, "p", "source", agent_id=SOURCE_ID)
            for _ in range(2):
                response = await handle.dispatch(
                    "send_message",
                    {
                        "project_id": "p",
                        "agent_id": SOURCE_ID,
                        "target": "source",
                        "content": "verify",
                        "metadata": {
                            "execute_ability": {
                                "name": "send_message",
                                "args": {"target": TARGET_ID, "content": "verify"},
                                "call_id": "check-missing",
                            }
                        },
                    },
                )
                assert response["success"] is False
                assert response["error"] == "source_ability_missing"
                assert "source" in response["error_detail"]
            manager = registry.get_message_manager("p")
            async with aiosqlite.connect(manager.db_path) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM mm_messages")
                assert (await cursor.fetchone())[0] == 0
            registered = await handle.dispatch(
                "register_ability",
                {
                    "project_id": "p",
                    "agent_id": SOURCE_ID,
                    "agent_name": "source",
                    "ability": {"ability_type": "send_message", "params": {}},
                },
            )
            assert registered["success"] is False, registered
            info = await handle.dispatch(
                "get_agent_info",
                {"project_id": "p", "agent_id": SOURCE_ID, "name": "source"},
            )
            assert info["success"], info
            assert "send_message" not in info["data"]["abilities"]
            assert info["data"]["available_ability_types"] == []
        finally:
            await registry.stop()
