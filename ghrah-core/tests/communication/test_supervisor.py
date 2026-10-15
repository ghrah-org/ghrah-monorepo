# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SupervisorActor 单元测试。

通过 mock actor handle 和 ActorAgent 来测试 Supervisor 的逻辑。
直接实例化 SupervisorActor。
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ghrah.abilities.builtin.conversation import ConversationAbility
from ghrah.abilities.builtin.end_task import EndTaskAbility
from ghrah.chat.message import ChatMessage
from ghrah.communication.errors import AgentNotFoundError, RegistryError
from ghrah.communication.supervisor import SupervisorActor
from ghrah.context.manager import ContextManager
from ghrah.context.persistence.memory import InMemoryBackend
from ghrah.core.config import AgentConfig


@pytest.fixture
def supervisor() -> SupervisorActor:
    """创建 SupervisorActor 实例（直接实例化）。"""
    sv = SupervisorActor()
    return sv


class TestSupervisorActor:
    """SupervisorActor 测试套件。"""

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, supervisor: SupervisorActor) -> None:
        """空 Supervisor 列出 Agent。"""
        agents = await supervisor.list_agents()
        assert agents == []

    @pytest.mark.asyncio
    async def test_health_check_empty(self, supervisor: SupervisorActor) -> None:
        """空 Supervisor 健康检查。"""
        health = await supervisor.health_check()
        assert health == {}

    @pytest.mark.asyncio
    async def test_send_agent_not_found(self, supervisor: SupervisorActor) -> None:
        """发送消息到不存在的 Agent 抛出 AgentNotFoundError。"""
        with pytest.raises(AgentNotFoundError, match="nonexistent"):
            await supervisor.send("nonexistent", "Hello")

    @pytest.mark.asyncio
    async def test_delegate_agent_not_found(self, supervisor: SupervisorActor) -> None:
        """委托到不存在的 Agent 抛出 AgentNotFoundError。"""
        with pytest.raises(AgentNotFoundError, match="agent-a"):
            await supervisor.delegate("agent-a", "agent-b", "Do something")

    @pytest.mark.asyncio
    async def test_broadcast_empty(self, supervisor: SupervisorActor) -> None:
        """空 Supervisor 广播返回空列表。"""
        responses = await supervisor.broadcast("Hello everyone")
        assert responses == []

    @pytest.mark.asyncio
    async def test_spawn_duplicate_raises(
        self, supervisor: SupervisorActor, sample_config: AgentConfig
    ) -> None:
        """重复 spawn 同名 Agent 抛出 RegistryError。"""
        # 手动注册一个 Agent（直接操作注册表）
        mock_handle = MagicMock()
        supervisor._registry.register("test-agent", sample_config, mock_handle)

        with pytest.raises(RegistryError, match="already registered"):
            await supervisor.spawn_agent(
                sample_config, abilities=[ConversationAbility(), EndTaskAbility()]
            )

    # ----------------------------------------------------------------
    # register_ability 命令路径守卫
    # ----------------------------------------------------------------

    @pytest.fixture
    def registered_agent(self, supervisor: SupervisorActor, sample_config: AgentConfig):
        """预注册一个 Agent（mock handle），返回 handle。"""
        mock_handle = MagicMock()
        mock_handle.register_ability.return_value = "conversation"
        supervisor._registry.register("guarded-agent", sample_config, mock_handle)
        return mock_handle

    @pytest.mark.asyncio
    async def test_register_ability_rejects_hooks_param(
        self, supervisor: SupervisorActor, registered_agent
    ) -> None:
        """params 含 hooks → 拒绝（hook 注入通道收窄）。"""
        with pytest.raises(ValueError, match="kernel materialization keys"):
            await supervisor.register_ability_for_agent(
                "guarded-agent", "conversation", {"hooks": []}
            )
        registered_agent.register_ability.assert_not_called()

    @pytest.mark.asyncio
    async def test_register_ability_rejects_checker_params(
        self, supervisor: SupervisorActor, registered_agent
    ) -> None:
        """params 含 permission_checker/command_checker/command_runner → 拒绝。"""
        with pytest.raises(ValueError, match="kernel materialization keys"):
            await supervisor.register_ability_for_agent(
                "guarded-agent", "read_file", {"permission_checker": object()}
            )
        with pytest.raises(ValueError, match="kernel materialization keys"):
            await supervisor.register_ability_for_agent(
                "guarded-agent", "execute_command", {"command_runner": object()}
            )

    @pytest.mark.asyncio
    async def test_register_ability_allows_plain_params(
        self, supervisor: SupervisorActor, registered_agent
    ) -> None:
        """普通构造参数照常透传（守卫只拦内核物化保留键）。"""
        result = await supervisor.register_ability_for_agent("guarded-agent", "conversation", {})
        assert result == "conversation"
        registered_agent.register_ability.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_ability_instance_channel(
        self, supervisor: SupervisorActor, registered_agent
    ) -> None:
        """实例通道：预构造 Ability 直接注册（command_proxy 物化路径）。"""
        ability = ConversationAbility()
        registered_agent.register_ability.return_value = "conversation"

        result = await supervisor.register_ability_instance_for_agent("guarded-agent", ability)

        assert result == "conversation"
        registered_agent.register_ability.assert_called_once_with(ability)

    @pytest.mark.asyncio
    async def test_terminate_agent(self, supervisor: SupervisorActor) -> None:
        """终止已注册的 Agent。"""
        config = AgentConfig(name="to-terminate")
        mock_handle = MagicMock()
        mock_handle.stop_receiving = AsyncMock()
        supervisor._registry.register("to-terminate", config, mock_handle)

        assert supervisor._registry.exists("to-terminate")

        await supervisor.terminate_agent("to-terminate")

        assert not supervisor._registry.exists("to-terminate")
        mock_handle.stop_receiving.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminate_not_found(self, supervisor: SupervisorActor) -> None:
        """终止未注册 Agent 抛出 AgentNotFoundError。"""
        with pytest.raises(AgentNotFoundError, match="nonexistent"):
            await supervisor.terminate_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_list_agents_with_data(self, supervisor: SupervisorActor) -> None:
        """列出已注册的 Agent。"""
        config_a = AgentConfig(name="agent-a", description="Agent A")
        config_b = AgentConfig(name="agent-b", description="Agent B")

        mock_handle = MagicMock()
        supervisor._registry.register("agent-a", config_a, mock_handle)
        supervisor._registry.register("agent-b", config_b, mock_handle)

        agents = await supervisor.list_agents()
        assert len(agents) == 2

        names = {a["name"] for a in agents}
        assert names == {"agent-a", "agent-b"}

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, supervisor: SupervisorActor) -> None:
        """健康检查 - 所有 Agent 健康。"""
        config = AgentConfig(name="healthy-agent")
        mock_handle = MagicMock()

        mock_handle.get_state = MagicMock(
            return_value={"name": "healthy-agent", "initialized": True}
        )
        supervisor._registry.register("healthy-agent", config, mock_handle)

        health = await supervisor.health_check()
        assert health == {"healthy-agent": True}

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, supervisor: SupervisorActor) -> None:
        """健康检查 - Agent 不健康。"""
        config = AgentConfig(name="sick-agent")
        mock_handle = MagicMock()

        def _fail():
            raise RuntimeError("Agent crashed")

        mock_handle.get_state = MagicMock(side_effect=_fail)
        supervisor._registry.register("sick-agent", config, mock_handle)

        health = await supervisor.health_check()
        assert health == {"sick-agent": False}

    def test_resolve_timeout_uses_agent_config(self, supervisor: SupervisorActor) -> None:
        """_resolve_timeout 使用目标 Agent 的 communication_timeout 配置。"""
        config = AgentConfig(name="slow-agent", communication_timeout=600.0)
        mock_handle = MagicMock()
        supervisor._registry.register("slow-agent", config, mock_handle)

        result = supervisor._resolve_timeout("slow-agent", None)
        assert result == 600.0

    def test_resolve_timeout_explicit_overrides_config(self, supervisor: SupervisorActor) -> None:
        """显式 timeout 参数优先于 Agent 配置。"""
        config = AgentConfig(name="slow-agent", communication_timeout=600.0)
        mock_handle = MagicMock()
        supervisor._registry.register("slow-agent", config, mock_handle)

        result = supervisor._resolve_timeout("slow-agent", -1)
        assert result == -1

    def test_resolve_timeout_fallback_to_default(self, supervisor: SupervisorActor) -> None:
        """未注册的 Agent 回退到 router 的 default_timeout。"""
        result = supervisor._resolve_timeout("nonexistent", None)
        assert result == supervisor._router._default_timeout

    def test_resolve_timeout_infinite(self, supervisor: SupervisorActor) -> None:
        """_resolve_timeout 支持 -1 表示无限等待。"""
        config = AgentConfig(name="infinite-agent", communication_timeout=-1)
        mock_handle = MagicMock()
        supervisor._registry.register("infinite-agent", config, mock_handle)

        result = supervisor._resolve_timeout("infinite-agent", None)
        assert result == -1

    # ----------------------------------------------------------------
    # fail-closed 能力语义（四态收敛）
    # ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_spawn_empty_abilities_raises(self) -> None:
        """[] -> raise：零能力 agent 是僵尸（无法回复，烧满 max_iterations）。"""
        supervisor = SupervisorActor()
        with pytest.raises(RegistryError, match="zero abilities"):
            await supervisor.spawn_agent(AgentConfig(name="zombie"), abilities=[])

    @pytest.mark.asyncio
    async def test_spawn_none_without_default_raises(self) -> None:
        """None + 未配置 default_abilities -> raise，指路显式通道。"""
        supervisor = SupervisorActor()
        with pytest.raises(RegistryError, match="no default_abilities configured"):
            await supervisor.spawn_agent(AgentConfig(name="orphan"))

    @pytest.mark.asyncio
    async def test_spawn_none_with_default_abilities_injects(self) -> None:
        """None + 已配置 default_abilities -> 注入配置集（部署方显式声明）。"""
        supervisor = SupervisorActor(
            default_abilities=["conversation", "end_task"],
            max_cluster_members=10,
        )
        await supervisor.spawn_agent(AgentConfig(name="configured"))

        handle = await supervisor.get_agent_handle("configured")
        ability_names = set(handle._abilities.keys())
        assert {"conversation", "end_task"} <= ability_names

    @pytest.mark.asyncio
    async def test_spawn_none_with_unknown_default_ability_raises(self) -> None:
        """配置集含 unknown 名 -> spawn 期 fail-closed（装配错误启动期暴露）。"""
        supervisor = SupervisorActor(default_abilities=["conversation", "no_such_ability"])
        with pytest.raises(RegistryError, match="unknown ability type 'no_such_ability'"):
            await supervisor.spawn_agent(AgentConfig(name="broken-assembly"))

    @pytest.mark.asyncio
    async def test_spawn_respects_max_cluster_members(self) -> None:
        """超限 raise：集群宽度硬阻断。"""
        supervisor = SupervisorActor(max_cluster_members=1)

        async def _spawn(name: str) -> None:
            await supervisor.spawn_agent(
                AgentConfig(name=name),
                abilities=[ConversationAbility(), EndTaskAbility()],
            )

        await _spawn("first")
        with pytest.raises(RegistryError, match="member limit reached"):
            await _spawn("second")


class TestGetClusterContext:
    """get_cluster_context：集群身份注入的数据源（C2c）。"""

    def test_context_shape_and_tags(self) -> None:
        """cluster_id + 成员清单（含 manifest tags 透传）。"""
        supervisor = SupervisorActor(cluster_id="cluster-x")
        supervisor._registry.register(
            "planner",
            AgentConfig(name="planner", description="任务规划"),
            MagicMock(),
            tags=["planning", "core"],
        )
        supervisor._registry.register(
            "coder",
            AgentConfig(name="coder", description="代码编写"),
            MagicMock(),
        )

        ctx = supervisor.get_cluster_context()
        assert ctx["cluster_id"] == "cluster-x"
        assert len(ctx["members"]) == 2
        planner = next(m for m in ctx["members"] if m["name"] == "planner")
        assert planner["description"] == "任务规划"
        assert planner["tags"] == ["planning", "core"]
        coder = next(m for m in ctx["members"] if m["name"] == "coder")
        assert coder["tags"] == []

    def test_member_cap_truncates(self) -> None:
        """截断上限：成员 ≤ 20（prompt 固定开销守恒）。"""
        from ghrah.communication.supervisor import _MAX_CONTEXT_MEMBERS

        supervisor = SupervisorActor(cluster_id="c")
        for i in range(_MAX_CONTEXT_MEMBERS + 5):
            supervisor._registry.register(f"agent-{i}", AgentConfig(name=f"agent-{i}"), MagicMock())
        assert len(supervisor.get_cluster_context()["members"]) == _MAX_CONTEXT_MEMBERS

    def test_empty_cluster(self) -> None:
        supervisor = SupervisorActor()
        ctx = supervisor.get_cluster_context()
        assert ctx["cluster_id"] == ""
        assert ctx["members"] == []


class TestSupervisorPersistenceRecovery:
    """spawn 必须 restore-first，且恢复失败不能注册空白 Agent。"""

    @staticmethod
    def _patch_builder(monkeypatch: pytest.MonkeyPatch) -> None:
        def build_actor(**kwargs: object) -> SimpleNamespace:
            from ghrah.chat.factory import ChatMessageFactory

            config = kwargs["config"]
            assert isinstance(config, AgentConfig)
            persistence_factory = kwargs.get("persistence_factory")
            backend = persistence_factory(config) if callable(persistence_factory) else None
            cm = ContextManager(
                agent_name=config.effective_agent_id,
                initial_state={},
                system_prompt=config.system_prompt,
                persistence=backend,
                auto_persist=backend is not None,
                message_factory=ChatMessageFactory(),
            )
            return SimpleNamespace(_context_manager=cm, stop_receiving=AsyncMock())

        monkeypatch.setattr(
            "ghrah.communication.supervisor.AgentBuilder.from_config",
            build_actor,
        )

    @pytest.mark.asyncio
    async def test_respawn_restores_existing_chain_without_overwrite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_builder(monkeypatch)
        backend = InMemoryBackend()
        config = AgentConfig(name="recoverable", system_prompt="stable")

        first = SupervisorActor()
        await first.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: backend,
        )
        first_info = first._registry.get_info("recoverable")
        first_cm = first_info.actor_handle._context_manager
        assert first.recovery_mode("recoverable") == "initialized"

        first_cm.begin_iteration()
        first_cm.apply_state_changes({"phase": "waiting"})
        committed = first_cm.commit_iteration(ability_names=["conversation"])
        await first.terminate_agent("recoverable")

        checkpoint = await backend.load_checkpoint("recoverable")
        assert checkpoint is not None
        old_node_ids = {node.id for node in checkpoint.nodes}
        assert committed.id in old_node_ids

        restarted = SupervisorActor()
        await restarted.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: backend,
        )
        restored_cm = restarted._registry.get_info("recoverable").actor_handle._context_manager

        assert restarted.recovery_mode("recoverable") == "restored"
        assert restored_cm.active_head.id == committed.id
        assert restored_cm.get_current_state() == {"phase": "waiting"}
        restored_checkpoint = await backend.load_checkpoint("recoverable")
        assert restored_checkpoint is not None
        assert old_node_ids.issubset({node.id for node in restored_checkpoint.nodes})

    @pytest.mark.asyncio
    async def test_checkpoint_rebuilds_messages_from_nodes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """checkpoint 以节点快照与 delta 为消息事实源。"""
        self._patch_builder(monkeypatch)
        backend = InMemoryBackend()
        config = AgentConfig(name="recoverable", system_prompt="stable")

        first = SupervisorActor()
        await first.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: backend,
        )
        cm = first._registry.get_info("recoverable").actor_handle._context_manager
        for iteration in range(1, 6):
            cm.begin_iteration()
            cm.add_messages(
                [
                    ChatMessage.user(text_or_blocks=f"u{iteration}"),
                    ChatMessage.ai(text=f"a{iteration}"),
                ]
            )
            cm.commit_iteration(ability_names=["conversation"])
        await first.terminate_agent("recoverable")

        restarted = SupervisorActor()
        await restarted.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: backend,
        )
        restored = restarted._registry.get_info("recoverable").actor_handle._context_manager
        texts = [message.text for message in restored.message_store.current_messages]

        assert "ghost" not in texts
        assert texts == [
            "stable",
            "u1",
            "a1",
            "u2",
            "a2",
            "u3",
            "a3",
            "u4",
            "a4",
            "u5",
            "a5",
        ]
        await restarted.terminate_agent("recoverable")

    @pytest.mark.asyncio
    async def test_corrupt_snapshot_fails_closed_without_overwrite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_builder(monkeypatch)
        backend = InMemoryBackend()

        async def load_corrupt_checkpoint(agent_name: str) -> None:
            raise ValueError(f"corrupt checkpoint for {agent_name}")

        monkeypatch.setattr(backend, "load_checkpoint", load_corrupt_checkpoint)
        supervisor = SupervisorActor()

        with pytest.raises(RegistryError, match="persistence recovery failed"):
            await supervisor.spawn_agent(
                AgentConfig(name="broken"),
                abilities=[ConversationAbility(), EndTaskAbility()],
                persistence_factory=lambda _: backend,
            )

        assert not supervisor._registry.exists("broken")

    @pytest.mark.asyncio
    async def test_restore_first_across_fresh_sqlite_backends(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from ghrah.context.persistence.sqlite_backend import SqliteBackend

        self._patch_builder(monkeypatch)
        db_path = tmp_path / "action-chains.sqlite3"
        config = AgentConfig(name="sqlite-agent")

        first = SupervisorActor()
        await first.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: SqliteBackend(db_path=db_path, run_id="run-1"),
        )
        first_cm = first._registry.get_info("sqlite-agent").actor_handle._context_manager
        first_cm.begin_iteration()
        first_cm.apply_state_changes({"checkpoint": 1})
        committed = first_cm.commit_iteration(ability_names=["conversation"])
        await first.terminate_agent("sqlite-agent")

        restarted = SupervisorActor()
        await restarted.spawn_agent(
            config,
            abilities=[ConversationAbility(), EndTaskAbility()],
            persistence_factory=lambda _: SqliteBackend(db_path=db_path, run_id="run-2"),
        )
        restored_cm = restarted._registry.get_info("sqlite-agent").actor_handle._context_manager

        assert restarted.recovery_mode("sqlite-agent") == "restored"
        assert restored_cm.active_head.id == committed.id
        assert restored_cm.get_current_state() == {"checkpoint": 1}
        await restarted.terminate_agent("sqlite-agent")
