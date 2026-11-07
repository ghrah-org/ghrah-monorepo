# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""PAM 切片：预装 → 来源登记 → 经 HostCommandPort add（mock Subject 命令面）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ghrah.ability.execution.execution_services import (
    HOST_COMMAND_PORT,
    SUPERVISOR,
    ExecutionServices,
)
from ghrah.ability.execution.invocation import InvocationIdentity, OperationCall
from ghrah.ability.package.provider import InstalledAbilityProvider
from ghrah.ability.source import BuiltinAbilitySource
from ghrah.core.host_command_port import HostCommandPort
from ghrah.core.subject_bridge import SubjectCommandBridge
from ghrah.protocol.enums import CommandType
from ghrah.types.results import ActionOutcome

from ghrah.subject.config import AbilityConfig, SubjectConfig
from ghrah.subject.core_cluster.registry import (
    _PROJECT_AGENT_MANAGE_HOST_COMMAND_GRANTS,
    PROJECT_AGENT_MANAGE_INSTANCE_ID,
)
from ghrah.subject.units.shipped_abilities import ShippedAbilityUnit


def _write_distro(distro_dir: Path) -> Path:
    distro_dir.mkdir(parents=True, exist_ok=True)
    (distro_dir / "distro.toml").write_text(
        "[distro]\n"
        'name = "pam"\n'
        "[distro.abilities]\n"
        "required = [\n"
        '  { name = "ghrah.project_agent_manage", version = "0.1.0" },\n'
        "]\n",
        encoding="utf-8",
    )
    return distro_dir / "ability"


class _RecordingSerial:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, payload))
        assert name == f"command/{CommandType.PROJECT_ADD_AGENT.value}"
        return {
            "success": True,
            "data": {
                "agent_id": "abc123",
                "agent_name": payload["agent"]["name"],
                "runtime_pending": False,
                "runtime_error": "",
            },
        }


class _ClusterSupervisor:
    def get_cluster_context(self) -> dict[str, Any]:
        return {"cluster_id": "cluster-main", "members": []}


def _pam_services(port: Any) -> ExecutionServices:
    services = ExecutionServices()
    services.set(HOST_COMMAND_PORT, port)
    services.set(SUPERVISOR, _ClusterSupervisor())
    return services


@pytest.mark.asyncio
async def test_preinstall_capture_and_add_via_host_port(tmp_path: Path) -> None:
    install_root = _write_distro(tmp_path / "distro")
    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    await ShippedAbilityUnit(config).init(None)

    source = BuiltinAbilitySource(install_root=install_root)
    assert source.origins["ghrah.project_agent_manage"].kind == "installed"
    captured = source.capture_package("ghrah.project_agent_manage")
    assert captured.definition().identity.version == "0.1.0"

    provider = InstalledAbilityProvider(install_root)
    provider_captured = provider.capture("ghrah.project_agent_manage", version="0.1.0")
    operation = provider.create_operation(
        "ghrah.project_agent_manage",
        "add_project_agent",
        provider_captured.digest,
    )

    serial = _RecordingSerial()
    grant_key = f"{PROJECT_AGENT_MANAGE_INSTANCE_ID}/add_project_agent"
    allowed = _PROJECT_AGENT_MANAGE_HOST_COMMAND_GRANTS[grant_key]
    port = HostCommandPort(SubjectCommandBridge(serial)).scoped(allowed)
    services = _pam_services(port)

    call = OperationCall(
        arguments={"name": "worker-one", "manifest_ref": "agents.coder"},
        identity=InvocationIdentity(project_id="proj-1", agent="leader", run="r1"),
    )
    result = await operation.execute(call, services)
    assert result.outcome is ActionOutcome.SUCCESS
    assert result.data["agent_name"] == "worker-one"
    assert result.data["agent_id"] == "abc123"
    assert serial.calls[0][1]["project_id"] == "proj-1"
    assert serial.calls[0][1]["agent"]["cluster_id"] == "cluster-main"
    assert serial.calls[0][1]["agent"]["name"] == "worker-one"


@pytest.mark.asyncio
async def test_add_without_project_identity_fails(tmp_path: Path) -> None:
    install_root = _write_distro(tmp_path / "distro")
    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    await ShippedAbilityUnit(config).init(None)
    provider = InstalledAbilityProvider(install_root)
    captured = provider.capture("ghrah.project_agent_manage", version="0.1.0")
    operation = provider.create_operation(
        "ghrah.project_agent_manage",
        "add_project_agent",
        captured.digest,
    )
    services = _pam_services(HostCommandPort(None).scoped(frozenset({"project_add_agent"})))
    call = OperationCall(
        arguments={"name": "x"},
        identity=InvocationIdentity(project_id="", agent="a", run="r"),
    )
    result = await operation.execute(call, services)
    assert result.outcome is ActionOutcome.FAILURE
    assert "trusted project identity is not bound" in result.data["error"]


@pytest.mark.asyncio
async def test_add_without_wired_port_fails(tmp_path: Path) -> None:
    """standalone / 服务表未注入端口 → port_or_failure 显式 FAILURE。"""
    install_root = _write_distro(tmp_path / "distro")
    config = SubjectConfig(
        ability_source="builtin",
        ability=AbilityConfig(install_root=str(install_root)),
    )
    await ShippedAbilityUnit(config).init(None)
    provider = InstalledAbilityProvider(install_root)
    captured = provider.capture("ghrah.project_agent_manage", version="0.1.0")
    operation = provider.create_operation(
        "ghrah.project_agent_manage",
        "add_project_agent",
        captured.digest,
    )
    call = OperationCall(
        arguments={"name": "x"},
        identity=InvocationIdentity(project_id="proj-1", agent="a", run="r"),
    )
    result = await operation.execute(call, ExecutionServices())
    assert result.outcome is ActionOutcome.FAILURE
    assert "host command port is not wired" in result.data["error"]
