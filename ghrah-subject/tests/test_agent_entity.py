"""Agent 实体端到端验收：真实 Core/ProjectStore，使用临时目录，不调用 LLM。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from ghrah.chat.message import ChatMessage
from ghrah.protocol.payloads.entity import AgentEntityCreatePayload
from ouroboros import Context
from test_restart_recovery_e2e import _actor, _config, _data

from ghrah.subject.errors import StableError
from ghrah.subject.project.paths import ProjectPaths
from ghrah.subject.runtime.assembly import assemble_subject
from ghrah.subject.runtime.ouroboros_bridge import bridge_command


@pytest.fixture(autouse=True)
def model_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """实体验收使用独立配置库，避免依赖用户的模型配置。"""
    from agentconf import AgentsConfig

    monkeypatch.setattr(AgentsConfig, "DEFAULT_DB_PATH", str(tmp_path / "models.db"))
    with AgentsConfig() as config:
        config.create_provider(
            name="test", provider_type="openai", base_url="http://localhost:1/v1", api_key="test"
        )
        config.create_model(provider_name="test", name="test", model_name="test", model_type="llm")
        for name in ("entity", "second", "worker", "a", "b", "one", "two"):
            config.create_agent(name=name, model_name="test")
    yield


async def test_entity_survives_stop_start_and_subject_restart(tmp_path: Path) -> None:
    config = _config(tmp_path)
    async with Context() as ctx:
        await assemble_subject(ctx, config, profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "entity"}))["project"]
        pid, cluster = project["project_id"], project["cluster_ids"][0]
        manager = ctx.get("project_manager")
        agent = await manager.agents.create(
            AgentEntityCreatePayload(
                project_id=pid,
                cluster_id=cluster,
                name="entity",
                start=False,
                ability_types=["send_message"],
            )
        )
        initial = await agent.snapshot()
        assert initial.runtime.availability == "offline"
        assert initial.context["session_count"] == 0
        assert (await agent.context.list_sessions())["sessions"] == []
        assert initial.specification.desired_state == "stopped"
        assert "send_message" in initial.abilities["available_types"]
        entity_list = _data(await bridge_command(ctx, "agent_entity_list", {"project_id": pid}))
        assert "send_message" in entity_list["available_ability_types"]
        assert (await manager.agents.list(pid))[0]["identity"] == agent.identity.model_dump()
        started = await agent.start()
        assert started["runtime"]["availability"] == "online", started
        incarnation = started["runtime"]["incarnation_id"]
        assert "send_message" in (await agent.abilities.list())["loaded_names"]
        actor = _actor(ctx, cluster, "entity")
        cm = actor._context_manager
        cm.begin_iteration()
        cm.add_messages([ChatMessage.user(text_or_blocks="durable entity history")])
        node = cm.commit_iteration(ability_names=["send_message"])
        catalog = await agent.context.list_sessions()
        sid = catalog["active_session_id"]
        bid = catalog["sessions"][0]["active_branch_id"]
        before = await agent.context.get_history(sid, bid)
        assert node.id in {n["id"] for n in before["nodes"]}
        stopped = await agent.stop(expected_incarnation_id=incarnation)
        assert stopped["runtime"]["availability"] == "offline"
        assert stopped["specification"]["desired_state"] == "stopped"
        assert await agent.context.get_history(sid, bid) == before
        assert await agent.context.list_sessions() == catalog
        assert (await agent.abilities.list())["loaded_names"] is None
        mm = ctx.get("core_cluster_registry").get_message_manager(pid)
        receipt = await mm.send_external(
            sender="tester",
            recipient_ids=[agent.identity.agent_id],
            content="offline mailbox",
            idempotency_key="entity-one",
        )
        inbox = await agent.mailbox.list_inbox()
        assert inbox["items"][0]["message"]["message_id"] == receipt.message_id
        # 重读不新增 run、不写入业务表；只读查询不能重新绑定收件 worker。
        db = ProjectPaths.from_locator(project["project_root_locator"]).action_chain_db_path
        with sqlite3.connect(db) as conn:
            runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        await agent.snapshot()
        await agent.context.get_history(sid, bid)
        await agent.mailbox.list_inbox()
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs
        # 恢复请求必须重读持久停止意图，不重新启动实体。
        async with manager._lifecycle_lock(pid):
            pending = asyncio.create_task(
                manager.restore_agent_if_desired(pid, agent.identity.agent_id, "entity")
            )
            await asyncio.sleep(0)
        assert await pending == ("skipped", None)
        result = _data(
            await bridge_command(
                ctx,
                "agent_entity",
                {
                    **agent.identity.model_dump(),
                    "operation": {"type": "snapshot"},
                },
            )
        )
        assert result["identity"] == agent.identity.model_dump()
        # 带待处理消息的实体保持停止状态；另一实体验证重启后的实例与会话。
        second = await manager.agents.create(
            AgentEntityCreatePayload(
                project_id=pid,
                cluster_id=cluster,
                name="second",
                start=True,
                ability_types=["send_message"],
            )
        )
        second_snapshot = await second.snapshot()
        second_catalog = await second.context.list_sessions()
        await second.stop()
        restarted = await second.start()
        assert restarted["runtime"]["incarnation_id"] != second_snapshot.runtime.incarnation_id
        assert await second.context.list_sessions() == second_catalog
        await second.stop()
        for malformed in (None, [], "stop"):
            invalid = await bridge_command(
                ctx,
                "agent_entity",
                {
                    **agent.identity.model_dump(),
                    "operation": malformed,
                },
            )
            assert invalid["success"] is False
        identity = agent.identity
    async with Context() as ctx:
        await assemble_subject(ctx, config, profile="full")
        entity = ctx.get("project_manager").agents.get(identity.project_id, identity.agent_id)
        assert (await entity.snapshot()).runtime.availability == "offline"
        assert await entity.context.get_history(sid, bid) == before
        assert (await entity.mailbox.list_inbox())["items"] == inbox["items"]
        assert (await entity.abilities.list())["configured_types"] == ["send_message"]


async def test_entity_scope_guards_and_stopped_configuration(tmp_path: Path) -> None:
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "scope"}))["project"]
        manager = ctx.get("project_manager")
        entities = []
        for name in ("a", "b"):
            entities.append(
                await manager.agents.create(
                    AgentEntityCreatePayload(
                        project_id=project["project_id"],
                        cluster_id=project["cluster_ids"][0],
                        name=name,
                        start=True,
                        ability_types=["send_message"],
                    )
                )
            )
        a, b = entities
        catalog = await b.context.list_sessions()
        with pytest.raises(ValueError):
            await a.context.get_history(
                catalog["active_session_id"], catalog["sessions"][0]["active_branch_id"]
            )
        a_snapshot = await a.snapshot()
        with pytest.raises(StableError, match="agent_must_be_stopped"):
            await a.abilities.configure(
                ["send_message"], expected_revision=a_snapshot.specification_revision
            )
        actor = _actor(ctx, project["cluster_ids"][0], "a")
        actor._context_manager.begin_iteration()
        with pytest.raises(ValueError, match="agent_busy"):
            await a.context.create_session(name="busy")
        actor._context_manager.commit_iteration(ability_names=[])
        await a.stop()
        with pytest.raises(ValueError):
            await a.context.activate_session(catalog["active_session_id"])
        with pytest.raises(StableError, match="agent_specification_conflict"):
            await a.abilities.configure(["send_message"], expected_revision=1)
        snap = await a.snapshot()
        configured = await a.abilities.configure(
            ["send_message", "conversation"], expected_revision=snap.specification_revision
        )
        assert configured["abilities"]["configured_types"] == ["send_message", "conversation"]
        await b.stop()
        mm = ctx.get("core_cluster_registry").get_message_manager(project["project_id"])
        receipt = await mm.send_external(
            sender="tester",
            recipient_ids=[b.identity.agent_id],
            content="b only",
            idempotency_key="b-only",
        )
        with pytest.raises(ValueError, match="message_not_found"):
            await a.mailbox.get_message(receipt.message_id)
        await a.start()
        assert set((await a.abilities.list())["loaded_names"]) == {"send_message", "conversation"}
        delivery = (await b.mailbox.get_message(receipt.message_id))["deliveries"][0]
        with pytest.raises(StableError, match="delivery_not_found"):
            await a.mailbox.retry(delivery["delivery_id"])
        with pytest.raises(StableError, match="agent_incarnation_conflict"):
            await a.stop(expected_incarnation_id="stale-incarnation")
        assert (await a.snapshot()).runtime.availability == "online"
        other = manager.agents.get("missing-project", a.identity.agent_id)
        with pytest.raises(StableError, match="project_unavailable"):
            await other.snapshot()


@pytest.mark.parametrize("raise_error", [False, True])
async def test_failed_stop_preserves_intent_and_serializes_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raise_error: bool
) -> None:
    """终止失败时保留停止意图；并发恢复必须等待并重读该意图。"""
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "stop-failure"}))[
            "project"
        ]
        pid, cluster = project["project_id"], project["cluster_ids"][0]
        manager = ctx.get("project_manager")
        agent = await manager.agents.create(
            AgentEntityCreatePayload(
                project_id=pid,
                cluster_id=cluster,
                name="worker",
                start=True,
                ability_types=["send_message"],
            )
        )
        handle = ctx.get("core_cluster_registry").get_handle(cluster)
        original_terminate = handle.terminate_agent
        original_snapshot = await agent.snapshot()
        entered, release = asyncio.Event(), asyncio.Event()

        async def fail_terminate(agent_id: str, agent_name: str) -> dict:
            entered.set()
            await release.wait()
            if raise_error:
                raise RuntimeError("termination transport unavailable")
            return {"success": False, "error": "termination rejected"}

        monkeypatch.setattr(handle, "terminate_agent", fail_terminate)
        stop = asyncio.create_task(
            bridge_command(
                ctx,
                "agent_entity",
                {
                    **agent.identity.model_dump(),
                    "operation": {"type": "stop"},
                },
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        recovery = asyncio.create_task(
            manager.restore_agent_if_desired(pid, agent.identity.agent_id, "worker")
        )
        try:
            await asyncio.sleep(0)
            assert not recovery.done()
        finally:
            release.set()
        result = await asyncio.wait_for(stop, timeout=5)
        assert result["success"] is False
        assert result["error"] == "agent_stop_failed"
        assert await asyncio.wait_for(recovery, timeout=5) == ("skipped", None)
        snapshot = await agent.snapshot()
        assert snapshot.specification.desired_state == "stopped"
        assert snapshot.runtime.incarnation_id == original_snapshot.runtime.incarnation_id
        assert snapshot.runtime.availability == "online"
        # 失败回执没有伪装离线，能力变更仍被拒绝；重试 stop 可完成终止。
        with pytest.raises(StableError, match="agent_must_be_stopped"):
            await agent.abilities.configure(
                ["send_message"], expected_revision=snapshot.specification_revision
            )
        monkeypatch.setattr(handle, "terminate_agent", original_terminate)
        assert (await agent.stop())["runtime"]["availability"] == "offline"


async def test_failed_start_is_queryable_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动失败保留身份与错误诊断，再次启动复用同一实体。"""
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "start-failure"}))[
            "project"
        ]
        pid, cluster = project["project_id"], project["cluster_ids"][0]
        manager = ctx.get("project_manager")
        created = _data(
            await bridge_command(
                ctx,
                "agent_entity_create",
                {
                    "project_id": pid,
                    "cluster_id": cluster,
                    "name": "worker",
                    "start": False,
                    "ability_types": ["send_message"],
                },
            )
        )
        agent = manager.agents.get(pid, created["identity"]["agent_id"])
        handle = ctx.get("core_cluster_registry").get_handle(cluster)
        original_spawn = handle.spawn_agent

        async def fail_spawn(payload: object) -> dict:
            return {"success": False, "error": "test spawn failure"}

        monkeypatch.setattr(handle, "spawn_agent", fail_spawn)
        with pytest.raises(StableError, match="agent_start_failed"):
            await agent.start()
        snapshot = await agent.snapshot()
        assert snapshot.identity == agent.identity
        assert snapshot.runtime.availability == "offline"
        assert snapshot.specification.desired_state == "running"
        assert snapshot.specification.runtime_error == "test spawn failure"
        monkeypatch.setattr(handle, "spawn_agent", original_spawn)
        assert (await agent.start())["runtime"]["availability"] == "online"
        assert (await agent.snapshot()).specification.runtime_error == ""


async def test_model_binding_preflight_and_room_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实实体和 Room/MM 链路：缺配置拒绝启动，绑定后能生成并投影回复。"""
    from ghrah.agents.builder import LLMFactory
    from test_project_message_manager import EchoLLM

    resolved_names = []

    def create_llm(resolved):
        resolved_names.append(resolved.agent_name)
        return EchoLLM()

    monkeypatch.setattr(LLMFactory, "create", create_llm)
    async with Context() as ctx:
        await assemble_subject(ctx, _config(tmp_path), profile="full")
        project = _data(await bridge_command(ctx, "project_create", {"name": "binding"}))["project"]
        pid = project["project_id"]
        entity = await ctx.get("project_manager").agents.create(
            AgentEntityCreatePayload(
                project_id=pid,
                cluster_id=project["cluster_ids"][0],
                name="display-name",
                start=False,
                ability_types=["conversation"],
            )
        )
        with pytest.raises(StableError, match="model_configuration_invalid"):
            await entity.start()
        assert (await entity.snapshot()).runtime.availability == "offline"
        await entity.stop()
        revision = (await entity.snapshot()).specification_revision
        with pytest.raises(StableError, match="model_configuration_invalid"):
            await entity.configure_model("missing", expected_revision=revision)
        await entity.configure_model("worker", expected_revision=revision)
        await entity.start()
        with pytest.raises(StableError, match="agent_must_be_stopped"):
            await entity.configure_model(
                "entity", expected_revision=(await entity.snapshot()).specification_revision
            )
        room = _data(await bridge_command(ctx, "room_create", {"project_id": pid, "name": "chat"}))[
            "room"
        ]
        rid = room["room_id"]
        _data(
            await bridge_command(
                ctx,
                "room_join",
                {"room_id": rid, "subject": entity.identity.agent_id, "subject_type": "agent"},
            )
        )
        _data(
            await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": rid,
                    "author": "user",
                    "author_type": "human",
                    "data": {
                        "message": "What is civil law?",
                        "targets": [entity.identity.agent_id],
                    },
                },
            )
        )
        mm = ctx.get("core_cluster_registry").get_message_manager(pid)
        await mm.dispatch_pending()
        await mm.recover_room_projections()
        logs = _data(await bridge_command(ctx, "room_get_log", {"room_id": rid}))["entries"]
        assert [e["data"]["message"] for e in logs] == ["What is civil law?", "ready"]
        assert logs[0]["data"]["deliveries"][0]["state"] == "completed"
        assert resolved_names == ["worker"]

        async def fail_generate(self, messages, tools=None):
            raise RuntimeError("provider temporarily unavailable")

        monkeypatch.setattr(EchoLLM, "generate", fail_generate)
        _data(
            await bridge_command(
                ctx,
                "room_send",
                {
                    "room_id": rid,
                    "author": "user",
                    "author_type": "human",
                    "data": {"message": "second question", "targets": [entity.identity.agent_id]},
                },
            )
        )
        await mm.dispatch_pending()
        logs = _data(await bridge_command(ctx, "room_get_log", {"room_id": rid}))["entries"]
        failed = next(e for e in logs if e["data"].get("message") == "second question")
        assert failed["data"]["deliveries"][0]["state"] == "failed"
        assert "provider temporarily unavailable" in failed["data"]["deliveries"][0]["error"]
        stored = await ctx.get("room_store").get_log(rid)
        assert all("deliveries" not in e.data for e in stored)
        identity = entity.identity
        await entity.stop()
        await entity.start()
        assert entity.identity == identity
        assert (await entity.snapshot()).specification.agent_config_name == "worker"
