# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""HostCommandPort：通用宿主命令端口与 grants 注入验收。"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.ability.execution.execution_services import HOST_COMMAND_PORT
from ghrah.ability.execution.invocation import MemberReference
from ghrah.core.host_command_port import (
    HOST_COMMAND_PORT_UNAVAILABLE_ERROR,
    HostCommandPort,
)


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((command, payload))
        return {"success": True, "data": {"command": command, **payload}}


@pytest.mark.asyncio
async def test_scoped_port_rejects_command_outside_allowlist() -> None:
    fake = _FakeBridge()
    port = HostCommandPort(fake).scoped(frozenset({"alpha"}))  # type: ignore[arg-type]
    result = await port.dispatch("beta", {"x": 1})
    assert result["success"] is False
    assert "not allowed" in result["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_scoped_port_dispatches_allowed_command() -> None:
    fake = _FakeBridge()
    port = HostCommandPort(fake).scoped(frozenset({"alpha", "gamma"}))  # type: ignore[arg-type]
    result = await port.dispatch("alpha", {"k": "v"})
    assert result["success"] is True
    assert fake.calls == [("alpha", {"k": "v"})]


@pytest.mark.asyncio
async def test_standalone_port_returns_explicit_failure() -> None:
    port = HostCommandPort(None).scoped(frozenset({"alpha"}))
    result = await port.dispatch("alpha", {})
    assert result["success"] is False
    assert HOST_COMMAND_PORT_UNAVAILABLE_ERROR in result["error"]


class _NamedAbility:
    def __init__(self, name: str, *, instance_id: str = "") -> None:
        self._name = name
        self.member_reference = MemberReference(instance_id=instance_id, member=name)

    @property
    def name(self) -> str:
        return self._name


class _FakeSupervisor:
    def __init__(
        self,
        port: HostCommandPort | None,
        grants: dict[str, frozenset[str]],
    ) -> None:
        self.host_command_port = port
        self.host_command_grants = grants
        self.manifest_store = None
        self.skill_provider = None


def _agent_with_grants(
    port: HostCommandPort | None,
    grants: dict[str, frozenset[str]],
) -> Any:
    from ghrah.agents.builder import AgentBuilder
    from ghrah.core.config import AgentConfig

    agent = AgentBuilder.from_config(AgentConfig(name="host-port-gate"))
    agent._supervisor = _FakeSupervisor(port, grants)
    return agent


def test_build_ability_call_hides_port_from_ungranted_instance() -> None:
    port = HostCommandPort(_FakeBridge())  # type: ignore[arg-type]
    grants = {"pkg-a/op": frozenset({"cmd_a"})}
    agent = _agent_with_grants(port, grants)
    foreign = _NamedAbility("other", instance_id="pkg-b")
    _call, services = agent._build_ability_call(foreign, {}, {})
    assert services.get(HOST_COMMAND_PORT) is None


def test_build_ability_call_injects_scoped_port_for_granted_member() -> None:
    fake = _FakeBridge()
    port = HostCommandPort(fake)  # type: ignore[arg-type]
    grants = {"pkg-a/op": frozenset({"cmd_a"})}
    agent = _agent_with_grants(port, grants)
    own = _NamedAbility("op", instance_id="pkg-a")
    _call, services = agent._build_ability_call(own, {}, {})
    injected = services.get(HOST_COMMAND_PORT)
    assert injected is not None
    assert injected.allowed == frozenset({"cmd_a"})


def test_build_ability_call_falls_back_to_instance_level_grant() -> None:
    port = HostCommandPort(_FakeBridge())  # type: ignore[arg-type]
    grants = {"pkg-a": frozenset({"cmd_x", "cmd_y"})}
    agent = _agent_with_grants(port, grants)
    own = _NamedAbility("any_member", instance_id="pkg-a")
    _call, services = agent._build_ability_call(own, {}, {})
    injected = services.get(HOST_COMMAND_PORT)
    assert injected is not None
    assert injected.allowed == frozenset({"cmd_x", "cmd_y"})


def test_build_hook_call_never_exposes_port() -> None:
    port = HostCommandPort(_FakeBridge())  # type: ignore[arg-type]
    grants = {"pkg-a/op": frozenset({"cmd_a"})}
    agent = _agent_with_grants(port, grants)
    _call, services = agent._build_hook_call({})
    assert services.get(HOST_COMMAND_PORT) is None


def test_missing_host_port_even_with_grants_injects_nothing() -> None:
    """standalone：grants 在但 port 为 None → 服务表不注入（operation 显式 FAILURE）。"""
    grants = {"pkg-a/op": frozenset({"cmd_a"})}
    agent = _agent_with_grants(None, grants)
    own = _NamedAbility("op", instance_id="pkg-a")
    _call, services = agent._build_ability_call(own, {}, {})
    assert services.get(HOST_COMMAND_PORT) is None
