# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""CoreUnitConfig.llm_factory 注入链测试（与 persistence_factory 同范式）。

覆盖两条 spawn 路径（直传 abilities / manifest_ref）经 CoreUnit →
SupervisorActor → AgentBuilder 的完整透传，并以真实 agent loop 收发
验证注入的 LLM 实例被实际使用（非仅构造成功）。
"""

from __future__ import annotations

from typing import Any

from _helpers import ScriptedLLM
from test_core_unit import FakeCtx

from ghrah.core.message import AgentMessage, MessageType
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.scheme.ability.core.operations.conversation import ConversationAbility
from ghrah.types.config_types import AgentConfig

_AGENT_YAML = """\
manifest: agent
version: "1"
metadata:
  namespace: smoke
  name: coder
  description: coder
model:
  agent_config_name: default
system_prompt: You code.
max_iterations: 5
abilities:
  - type: conversation
  - type: end_task
"""


class _SingleAgentStore:
    """最小 ManifestStoreProtocol stub（仅 load_agent 被消费）。"""

    def __init__(self) -> None:
        from ghrah.scheme.loading.manifest_io import parse_agent_manifest

        self._agents = {"smoke.coder": parse_agent_manifest(_AGENT_YAML)}

    def load_ability(self, full_name: str) -> Any:
        raise KeyError(full_name)

    def load_agent(self, full_name: str) -> Any:
        return self._agents[full_name]

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        return []

    def list_agents(self, namespace: str | None = None) -> list[str]:
        return list(self._agents)


class TestLLMFactoryInjection:
    async def test_direct_spawn_uses_injected_llm_factory(self) -> None:
        """直传 abilities 路径：注入 factory 被 agent loop 实际调用。"""
        scripted = ScriptedLLM(replies=["预设回复-直传"])
        unit = create_core_unit(
            CoreUnitConfig(
                project_id="default",
                default_abilities=("conversation", "end_task"),
                llm_factory=lambda _config: scripted,
            )
        )
        ctx = FakeCtx()
        await unit.init(ctx)

        spawn = await unit.handle_command(
            "spawn_agent",
            {"project_id": "default", "config": {"name": "agent-a"}},
            None,
        )
        assert spawn["success"], spawn.get("error")

        handle = await unit.supervisor.get_agent_handle("agent-a")
        reply = await handle.receive(
            AgentMessage(
                sender="user",
                recipient="agent-a",
                content="你好",
                type=MessageType.CHAT,
            )
        )
        assert reply.type == MessageType.RESULT
        assert reply.content == "预设回复-直传"
        assert len(scripted.calls) == 1

    async def test_manifest_ref_spawn_uses_injected_llm_factory(self) -> None:
        """manifest_ref 路径：解析 → 物化 → spawn 同样消费注入 factory。"""
        scripted = ScriptedLLM(replies=["预设回复-manifest"])
        unit = create_core_unit(
            CoreUnitConfig(
                project_id="default",
                manifest_store=_SingleAgentStore(),
                llm_factory=lambda _config: scripted,
            )
        )
        ctx = FakeCtx()
        await unit.init(ctx)

        spawn = await unit.handle_command(
            "spawn_agent",
            {
                "project_id": "default",
                "config": {"name": "coder"},
                "manifest_ref": "smoke.coder",
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

    async def test_supervisor_spawn_accepts_llm_factory_directly(self) -> None:
        """SupervisorActor.spawn_agent kwarg 直达 AgentBuilder（不经 CoreUnit）。"""
        scripted = ScriptedLLM(replies=["预设回复-supervisor"])
        unit = create_core_unit(CoreUnitConfig(project_id="default"))
        ctx = FakeCtx()
        await unit.init(ctx)

        await unit.supervisor.spawn_agent(
            AgentConfig(name="agent-b", system_prompt="x"),
            abilities=[ConversationAbility()],
            llm_factory=lambda _config: scripted,
        )
        handle = await unit.supervisor.get_agent_handle("agent-b")
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

    async def test_factory_receives_agent_config(self) -> None:
        """factory 收到 spawn 的 AgentConfig（per-agent 路由依据可用）。

        factory 在首次 _ensure_llm（即 receive）时才被调用（惰性初始化），
        spawn 后需驱动一次对话再断言。
        """
        seen: list[str] = []
        scripted = ScriptedLLM(replies=["ok"])
        unit = create_core_unit(
            CoreUnitConfig(
                project_id="default",
                default_abilities=("conversation", "end_task"),
                llm_factory=lambda config: (seen.append(config.name), scripted)[1],
            )
        )
        ctx = FakeCtx()
        await unit.init(ctx)
        spawn = await unit.handle_command(
            "spawn_agent", {"project_id": "default", "config": {"name": "routed"}}, None
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
