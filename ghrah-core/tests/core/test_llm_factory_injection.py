# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CoreUnitConfig.llm_factory 注入链测试（与 persistence_factory 同范式）。

覆盖 CoreUnit 模板装配和独立 Supervisor SDK 到 AgentBuilder 的 LLM 注入。
"""

from __future__ import annotations

from collections.abc import Callable

from _helpers import ScriptedLLM
from test_core_unit import FakeCtx

from ghrah.ability.builtin.core.operations.conversation import ConversationAbility
from ghrah.communication.supervisor import SupervisorActor
from ghrah.core.message import AgentMessage, MessageType
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.types.config_types import AgentConfig


class TestLLMFactoryInjection:
    async def test_manifest_ref_spawn_uses_injected_llm_factory(
        self, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """manifest_ref 路径：解析 → 物化 → spawn 同样消费注入 factory。"""
        scripted = ScriptedLLM(replies=["预设回复-manifest"])
        unit = create_core_unit(ability_core_config(llm_factory=lambda _config: scripted))
        ctx = FakeCtx()
        await unit.init(ctx)

        spawn = await unit.handle_command(
            "spawn_agent",
            {
                "project_id": "default",
                "config": {"name": "coder"},
                "manifest_ref": "tests.agent",
            },
            None,
        )
        assert spawn["success"], spawn.get("error")

        handle = await unit.supervisor.get_agent_handle("coder")
        reply = await handle.receive(
            AgentMessage(
                sender="user",
                recipient="coder",
                content="你好",
                type=MessageType.CHAT,
            )
        )
        assert reply.type == MessageType.RESULT
        assert reply.content == "预设回复-manifest"
        assert len(scripted.calls) == 1
        await unit.stop()

    async def test_supervisor_spawn_accepts_llm_factory_directly(self) -> None:
        """SupervisorActor.spawn_agent kwarg 直达 AgentBuilder（不经 CoreUnit）。"""
        scripted = ScriptedLLM(replies=["预设回复-supervisor"])
        supervisor = SupervisorActor()
        await supervisor.spawn_agent(
            AgentConfig(name="agent-b", system_prompt="x"),
            abilities=[ConversationAbility()],
            llm_factory=lambda _config: scripted,
        )
        handle = await supervisor.get_agent_handle("agent-b")
        reply = await handle.receive(
            AgentMessage(
                sender="user",
                recipient="agent-b",
                content="hi",
                type=MessageType.CHAT,
            )
        )
        assert reply.content == "预设回复-supervisor"
        assert len(scripted.calls) == 1
        await supervisor.shutdown()

    async def test_factory_receives_agent_config(
        self, ability_core_config: Callable[..., CoreUnitConfig]
    ) -> None:
        """factory 收到 spawn 的 AgentConfig（per-agent 路由依据可用）。

        factory 在首次 _ensure_llm（即 receive）时才被调用（惰性初始化），
        spawn 后需驱动一次对话再断言。
        """
        seen: list[str] = []
        scripted = ScriptedLLM(replies=["ok"])
        unit = create_core_unit(
            ability_core_config(llm_factory=lambda config: (seen.append(config.name), scripted)[1])
        )
        ctx = FakeCtx()
        await unit.init(ctx)
        spawn = await unit.handle_command(
            "spawn_agent",
            {"project_id": "default", "config": {"name": "routed"}, "manifest_ref": "tests.agent"},
            None,
        )
        assert spawn["success"], spawn.get("error")

        handle = await unit.supervisor.get_agent_handle("routed")
        await handle.receive(
            AgentMessage(
                sender="user",
                recipient="routed",
                content="hi",
                type=MessageType.CHAT,
            )
        )
        assert seen == ["routed"]
        await unit.stop()
