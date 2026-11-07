# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""集群通信 Ability 测试：QueryAgentsAbility, SendMessageAbility,
BroadcastMessageAbility, SpawnAgentAbility, TerminateAgentAbility。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _helpers import make_call, make_services

from ghrah.ability.builtin.cluster.operations.broadcast_message import BroadcastMessageAbility
from ghrah.ability.builtin.cluster.operations.query_agents import QueryAgentsAbility
from ghrah.ability.builtin.cluster.operations.send_message import SendMessageAbility
from ghrah.ability.builtin.cluster.operations.spawn_agent import SpawnAgentAbility
from ghrah.ability.execution.base import ActionOutcome
from ghrah.ability.execution.execution_services import ExecutionServices
from ghrah.ability.execution.invocation import OperationCall
from ghrah.core.config import AgentConfig


def _make_context(
    supervisor: Any = None,
    agent_name: str = "test-agent",
    tool_args: dict[str, Any] | None = None,
    **overrides: Any,
) -> tuple[OperationCall, ExecutionServices]:
    """创建测试用 (OperationCall, ExecutionServices)。"""
    return (
        make_call(
            arguments=tool_args or {},
            agent_name=agent_name,
            tool_call_id="call-1",
        ),
        make_services(supervisor=supervisor, context_manager=MagicMock(), **overrides),
    )


def _make_supervisor(
    list_agents_return: list[dict[str, Any]] | None = None,
    send_return: str = "response from target",
    broadcast_return: list[dict[str, str]] | None = None,
    spawn_agent_return: str = "new-agent",
) -> MagicMock:
    supervisor = MagicMock()
    supervisor.list_agents = AsyncMock(return_value=list_agents_return or [])
    supervisor.send = AsyncMock(return_value=send_return)
    supervisor.broadcast = AsyncMock(
        return_value=broadcast_return or [{"responder": "mock-agent", "content": "ok"}]
    )
    supervisor.prepare_message = AsyncMock(
        return_value={
            "message_id": "message-1",
            "recipients": ["agent-b"],
            "status": "prepared",
            "mode": "persistent",
        }
    )
    supervisor.spawn_agent = AsyncMock(return_value=spawn_agent_return)
    return supervisor


# ── QueryAgentsAbility 测试 ──


class TestQueryAgentsAbility:
    def test_name(self) -> None:
        ability = QueryAgentsAbility()
        assert ability.name == "query_agents"

    def test_bind_tool(self) -> None:
        ability = QueryAgentsAbility()
        schema = ability.bind_tool()
        assert schema is not None
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "query_agents"
        assert "filter" in func["parameters"]["properties"]

    def test_get_hooks_empty(self) -> None:
        ability = QueryAgentsAbility()
        assert ability.get_hooks() == []

    async def test_execute_no_supervisor(self) -> None:
        call, services = _make_context(supervisor=None)
        ability = QueryAgentsAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "No supervisor" in result.data["error"]

    async def test_execute_returns_agents(self) -> None:
        agents = [
            {"name": "planner", "description": "Task planner"},
            {"name": "coder", "description": "Code writer"},
        ]
        supervisor = _make_supervisor(list_agents_return=agents)
        call, services = _make_context(supervisor=supervisor)
        ability = QueryAgentsAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["count"] == 2
        assert result.data["agents"] == agents

    async def test_execute_with_filter(self) -> None:
        agents = [
            {"name": "planner", "description": "Task planner"},
            {"name": "coder", "description": "Code writer"},
            {"name": "code-reviewer", "description": "Code reviewer"},
        ]
        supervisor = _make_supervisor(list_agents_return=agents)
        call, services = _make_context(supervisor=supervisor, tool_args={"filter": "code"})
        ability = QueryAgentsAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["count"] == 2
        names = [a["name"] for a in result.data["agents"]]
        assert "coder" in names
        assert "code-reviewer" in names
        assert "planner" not in names

    async def test_execute_list_agents_exception(self) -> None:
        supervisor = _make_supervisor()
        supervisor.list_agents = AsyncMock(side_effect=RuntimeError("registry error"))
        call, services = _make_context(supervisor=supervisor)
        ability = QueryAgentsAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "registry error" in result.data["error"]


# ── SendMessageAbility 测试 ──


class TestSendMessageAbility:
    def test_name(self) -> None:
        ability = SendMessageAbility()
        assert ability.name == "send_message"

    def test_bind_tool(self) -> None:
        ability = SendMessageAbility()
        schema = ability.bind_tool()
        assert schema is not None
        func = schema["function"]
        assert func["name"] == "send_message"
        params = func["parameters"]["properties"]
        assert "target" in params
        assert "content" in params
        required = func["parameters"].get("required", [])
        assert "target" in required
        assert "content" in required

    async def test_execute_no_supervisor(self) -> None:
        call, services = _make_context(
            supervisor=None, tool_args={"target": "agent-b", "content": "hello"}
        )
        ability = SendMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "No supervisor" in result.data["error"]

    async def test_execute_prepares_persistent_message(self) -> None:
        supervisor = _make_supervisor(send_return="done!")
        call, services = _make_context(
            supervisor=supervisor,
            agent_name="agent-a",
            tool_args={"target": "agent-b", "content": "hello"},
        )
        ability = SendMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["message_id"] == "message-1"
        assert result.data["target"] == "agent-b"
        assert result.data["status"] == "prepared"
        supervisor.prepare_message.assert_awaited_once()
        supervisor.send.assert_not_awaited()

    async def test_execute_missing_target(self) -> None:
        supervisor = _make_supervisor()
        call, services = _make_context(supervisor=supervisor, tool_args={"content": "hello"})
        ability = SendMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "target is required" in result.data["error"]

    async def test_execute_missing_content(self) -> None:
        supervisor = _make_supervisor()
        call, services = _make_context(supervisor=supervisor, tool_args={"target": "agent-b"})
        ability = SendMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "content is required" in result.data["error"]

    async def test_execute_prepare_failure(self) -> None:
        supervisor = _make_supervisor()
        supervisor.prepare_message = AsyncMock(side_effect=RuntimeError("write failed"))
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"target": "agent-b", "content": "hello"},
        )
        ability = SendMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "write failed" in result.data["error"]


# ── BroadcastMessageAbility 测试 ──


class TestBroadcastMessageAbility:
    def test_name(self) -> None:
        ability = BroadcastMessageAbility()
        assert ability.name == "broadcast_message"

    def test_bind_tool(self) -> None:
        ability = BroadcastMessageAbility()
        schema = ability.bind_tool()
        assert schema is not None
        func = schema["function"]
        assert func["name"] == "broadcast_message"
        params = func["parameters"]["properties"]
        assert "content" in params

    async def test_execute_no_supervisor(self) -> None:
        call, services = _make_context(supervisor=None, tool_args={"content": "hello all"})
        ability = BroadcastMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "No supervisor" in result.data["error"]

    async def test_execute_success(self) -> None:
        supervisor = _make_supervisor(
            list_agents_return=[{"name": "agent-a"}, {"name": "b"}, {"name": "c"}]
        )
        supervisor.prepare_message.return_value["recipients"] = ["b", "c"]
        call, services = _make_context(
            supervisor=supervisor,
            agent_name="agent-a",
            tool_args={"content": "hello all"},
        )
        ability = BroadcastMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["status"] == "prepared"
        assert result.data["recipients"] == ["b", "c"]
        assert result.data["agent_count"] == 2
        assert supervisor.prepare_message.call_args.kwargs["targets"] == ("b", "c")
        supervisor.broadcast.assert_not_awaited()

    async def test_execute_missing_content(self) -> None:
        supervisor = _make_supervisor()
        call, services = _make_context(supervisor=supervisor, tool_args={})
        ability = BroadcastMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "content is required" in result.data["error"]

    async def test_execute_broadcast_exception(self) -> None:
        supervisor = _make_supervisor(list_agents_return=[{"name": "b"}])
        supervisor.prepare_message = AsyncMock(side_effect=RuntimeError("network error"))
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"content": "hello all"},
        )
        ability = BroadcastMessageAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "network error" in result.data["error"]


# ── SpawnAgentAbility 测试（manifest_ref 主路径，能力面冻结） ──


class _FakeManifest:
    """最小 agent manifest stub（duck-typed，供 resolver 消费）。"""

    def __init__(self, name: str) -> None:
        self.metadata = type(
            "M",
            (),
            {"name": name, "description": f"manifest {name}", "tags": ["test"]},
        )()
        self.config = {
            "name": name,
            "description": f"manifest {name}",
            "system_prompt": f"You are {name}.",
        }
        self.abilities = [
            type(
                "A",
                (),
                {
                    "type": "builtin",
                    "ability_name": "conversation",
                    "handler": "conversation",
                    "params": {},
                    "permissions": None,
                },
            )()
        ]


class _FakeStore:
    """最小 ManifestStore stub：load_agent/list_agents。"""

    def __init__(self, agents: dict[str, _FakeManifest]) -> None:
        self._agents = agents

    def load_agent(self, ref: str) -> _FakeManifest:
        if ref not in self._agents:
            raise KeyError(f"agent manifest not found: {ref}")
        return self._agents[ref]

    def list_agents(self) -> list[str]:
        return list(self._agents)


def _make_store() -> _FakeStore:
    return _FakeStore({"ghrah.designer": _FakeManifest("designer")})


def _patch_resolver(monkeypatch: Any, store: _FakeStore) -> None:
    """把 ManifestResolver.resolve 替换为消费 _FakeManifest 的 stub。"""

    from ghrah.manifest import resolver as resolver_mod

    class _ResolvedAbility:
        def __init__(self, manifest_ref: str) -> None:
            self.ability_name = manifest_ref
            self.implementation = type("I", (), {"type": "builtin", "handler": "conversation"})()
            self.permissions = type("P", (), {"require_hitl": False})()

    class _ResolvedAgent:
        def __init__(self, config: Any, abilities: list[Any]) -> None:
            self.config = config
            self.abilities = abilities

    def fake_resolve(self: Any, manifest: Any, runtime_name: str | None = None) -> Any:
        return _ResolvedAgent(
            config=AgentConfig(
                name=runtime_name or manifest.config["name"],
                description=manifest.config["description"],
                system_prompt=manifest.config["system_prompt"],
            ),
            abilities=[_ResolvedAbility("conversation")],
        )

    monkeypatch.setattr(resolver_mod.ManifestResolver, "resolve", fake_resolve)


class TestSpawnAgentAbility:
    def test_name(self) -> None:
        ability = SpawnAgentAbility()
        assert ability.name == "spawn_agent"

    def test_bind_tool_schema_has_no_free_abilities(self) -> None:
        """LLM 工具面无自由 abilities 参数——manifest_ref 唯一主路径。"""
        ability = SpawnAgentAbility()
        schema = ability.bind_tool()
        assert schema is not None
        func = schema["function"]
        assert func["name"] == "spawn_agent"
        params = func["parameters"]["properties"]
        assert "manifest_ref" in params
        assert "name" in params
        assert "description" in params
        assert "system_prompt" in params
        assert "abilities" not in params
        required = func["parameters"].get("required", [])
        assert "manifest_ref" in required

    async def test_execute_no_supervisor(self) -> None:
        call, services = _make_context(
            supervisor=None,
            tool_args={"manifest_ref": "ghrah.designer"},
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "No supervisor" in result.data["error"]

    async def test_execute_missing_manifest_ref(self) -> None:
        supervisor = _make_supervisor()
        call, services = _make_context(supervisor=supervisor, tool_args={})
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "manifest_ref is required" in result.data["error"]

    async def test_execute_store_not_wired(self) -> None:
        """manifest_store 未接线 → 显式 FAILURE 指路（零隐式）。"""
        supervisor = _make_supervisor()
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"manifest_ref": "ghrah.designer"},
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "manifest_store is not wired" in result.data["error"]
        supervisor.spawn_agent.assert_not_awaited()

    async def test_execute_manifest_ref_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        supervisor = _make_supervisor(spawn_agent_return="designer")
        store = _make_store()
        _patch_resolver(monkeypatch, store)
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"manifest_ref": "ghrah.designer"},
            manifest_store=store,
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["agent_name"] == "designer"
        assert result.data["manifest_ref"] == "ghrah.designer"
        supervisor.spawn_agent.assert_awaited_once()
        call_args = supervisor.spawn_agent.call_args
        config = call_args[0][0]
        assert isinstance(config, AgentConfig)
        assert config.name == "designer"
        abilities = call_args[1].get("abilities")
        assert abilities is not None and len(abilities) > 0

    async def test_execute_persona_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """name/description/system_prompt 覆写经 resolved_config 生效。"""
        supervisor = _make_supervisor(spawn_agent_return="designer-x")
        store = _make_store()
        _patch_resolver(monkeypatch, store)
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={
                "manifest_ref": "ghrah.designer",
                "name": "designer-x",
                "description": "Custom role",
                "system_prompt": "Custom persona.",
            },
            manifest_store=store,
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.SUCCESS
        call_args = supervisor.spawn_agent.call_args
        config = call_args[0][0]
        assert config.name == "designer-x"
        assert config.description == "Custom role"
        assert config.system_prompt == "Custom persona."

    async def test_execute_manifest_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        supervisor = _make_supervisor()
        store = _make_store()
        _patch_resolver(monkeypatch, store)
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"manifest_ref": "ghrah.nonexistent"},
            manifest_store=store,
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "ghrah.nonexistent" in result.data["error"]
        supervisor.spawn_agent.assert_not_awaited()

    async def test_execute_spawn_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        supervisor = _make_supervisor()
        supervisor.spawn_agent = AsyncMock(side_effect=RuntimeError("name conflict"))
        store = _make_store()
        _patch_resolver(monkeypatch, store)
        call, services = _make_context(
            supervisor=supervisor,
            tool_args={"manifest_ref": "ghrah.designer"},
            manifest_store=store,
        )
        ability = SpawnAgentAbility()
        result = await ability.execute(call, services)
        assert result.outcome == ActionOutcome.FAILURE
        assert "name conflict" in result.data["error"]
