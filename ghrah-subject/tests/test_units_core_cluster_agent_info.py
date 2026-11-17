# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``get_agent_info`` 读侧语义：定义态是骨架，runtime 只补充。

背景（C3 端口化回归）：``get_agent_info`` 从「CoreUnit 命令」改为
``PortClusterHandle.dispatch`` → ``port.agents.get``（Core API ``agents.get``）。
后者先查在线注册表，查不到即 ``NOT_FOUND: agent '<id>' is not online``。
而 ``_ensure_agent_spawned`` 在 spawn **之前**就 ``ensure_cluster`` 登记了
cluster，因此「Project 名册存在、runtime 离线（含 spawn 失败）」的 Agent
不再命中 ``_no_mount_result`` 的定义态兜底，WS 回执直接变成 NOT_FOUND。

前端消费方（ghrah-observer-webui）：
- ``observer-core/src/bind.ts``：仅当 ``data.context_usage`` 非空才恢复用量快照；
- ``observer-web/src/components/agent-abilities-dialog.vue``：
  读 ``data.abilities`` / ``data.runtime_state`` / ``data.available_ability_types``，
  ``runtime_state === "stopped"`` 时把 abilities 当定义清单。

本文件锁定两条语义：
1. 名册存在但 runtime 离线 → **成功**回执 + 定义态（``runtime_state="stopped"``），
   绝不因离线返回 NOT_FOUND；
2. runtime 在线 → 保留既有回执字段（``available_ability_types`` /
   ``context_usage`` / ``runtime_state``），端口视图 ``AgentView`` 缺失的字段
   由 Subject 侧补齐。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import AgentRuntimeView, AgentView

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.port_transport import PortClusterTransport
from ghrah.subject.unit.base import CommandContext
from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit

PROJECT_ID = "project-1"
AGENT_ID = "a" * 32
AGENT_NAME = "requirement"
SYSTEM_PROMPT = "You are the requirement agent."


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
    )


def _project() -> dict[str, Any]:
    """``project_get`` 回执里的 Project（``ProjectRecord.to_wire()`` 子集）。"""
    return {
        "project_id": PROJECT_ID,
        "version": 3,
        "name": "demo",
        "status": "active",
        "project_root_locator": str(Path("/tmp") / PROJECT_ID),
        "archived_at": None,
        "deleted_at": None,
        "agents": [
            {
                "name": AGENT_NAME,
                "agent_id": AGENT_ID,
                "endpoint_name": "requirement-agent-multimodal",
                "manifest_ref": "",
                "instance_manifest_path": "",
                "system_prompt": SYSTEM_PROMPT,
                "abilities": ["conversation"],
                "desired_state": "running",
                "path_grants": [],
                "runtime_status": "error",
                "runtime_error": "model_configuration_invalid",
            }
        ],
    }


class _FakeProjectManager:
    """只实现 ``_project()`` 用到的 ``project_get``。"""

    def __init__(self, project: dict[str, Any]) -> None:
        self._project = project

    async def handle_command(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert command == "project_get", command
        return {"success": True, "data": {"project": self._project}}


class _FakeCtx:
    """单元 ``init`` 会被真实挂载路径替换；测试直接注入 ctx 替身。"""

    def __init__(self, project_manager: _FakeProjectManager) -> None:
        self._values = {"project_manager": project_manager}

    def get(self, key: str) -> Any:
        return self._values[key]


class _FakeAgents:
    """Core 端口 ``agents`` 分组替身：运行态探测 + 在线视图。"""

    def __init__(
        self,
        *,
        availability: str,
        view: AgentView | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self.availability = availability
        self.view = view
        self.get_error = get_error
        self.runtime_calls: list[str] = []
        self.get_calls: list[str] = []

    async def runtime(self, *, agent_id: str) -> AgentRuntimeView:
        self.runtime_calls.append(agent_id)
        return AgentRuntimeView(
            agent_id=agent_id,
            availability=self.availability,
            incarnation_id="inc-1" if self.availability == "online" else None,
            busy=False if self.availability == "online" else None,
            loaded_abilities=["conversation"] if self.availability == "online" else None,
        )

    async def get(self, *, agent_id: str) -> AgentView:
        self.get_calls.append(agent_id)
        if self.get_error is not None:
            raise self.get_error
        assert self.view is not None
        return self.view


class _FakePort:
    """项目作用域端口替身：只实现本链路用到的 ``agents``。"""

    def __init__(self, agents: _FakeAgents) -> None:
        self.agents = agents


class _FakePorts:
    """``CorePorts`` 替身：单项目固定端口 + 定位符登记。"""

    def __init__(self, port: _FakePort) -> None:
        self._port = port
        self.locators: dict[str, str] = {}

    def for_project(self, project_id: str) -> _FakePort:
        assert project_id == PROJECT_ID, project_id
        return self._port

    def remember_locator(self, project_id: str, locator: str) -> None:
        self.locators[project_id] = locator


def _unit(
    tmp_path: Path, agents: _FakeAgents
) -> tuple[CoreClusterRegistryUnit, PortClusterTransport]:
    """装配被测单元：真实 ``PortClusterTransport`` + 假 Core 端口。"""
    unit = CoreClusterRegistryUnit(_config(tmp_path))
    transport = PortClusterTransport(_FakePorts(_FakePort(agents)))  # type: ignore[arg-type]
    unit._transport = transport  # type: ignore[assignment]
    unit._ctx = _FakeCtx(_FakeProjectManager(_project()))  # type: ignore[assignment]
    return unit, transport


def _info_payload() -> dict[str, Any]:
    return {"project_id": PROJECT_ID, "agent_id": AGENT_ID, "name": AGENT_NAME}


def _not_online_error() -> ApiError:
    """复刻 Core ``agents.get`` 对离线 Agent 的失败载荷。"""
    return ApiError(
        Problem.make(
            ProblemCode.NOT_FOUND,
            capability="agents.get",
            detail=f"agent {AGENT_ID!r} is not online",
        )
    )


async def test_offline_agent_returns_definition_state(tmp_path: Path) -> None:
    """名册存在、runtime 离线（spawn 失败后 cluster 已登记）→ 定义态成功回执。

    红：C3 之前端口化把该情形推给 ``port.agents.get``，回执是
    ``NOT_FOUND: agent '...' is not online``，前端能力弹窗直接报错。
    """
    agents = _FakeAgents(availability="offline", get_error=_not_online_error())
    unit, transport = _unit(tmp_path, agents)
    # 现场关键条件：_ensure_agent_spawned 先 ensure_cluster 再 spawn，
    # spawn 失败后 cluster 已登记，但没有任何在线实例。
    await transport.ensure_cluster(
        PROJECT_ID,
        project_id=PROJECT_ID,
        project_root_locator=str(Path("/tmp") / PROJECT_ID),
    )
    assert transport.has_cluster(PROJECT_ID) is True

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is True, result
    data = result["data"]
    assert data["runtime_state"] == "stopped"
    assert data["name"] == AGENT_NAME
    assert data["system_prompt"] == SYSTEM_PROMPT
    assert data["abilities"] == ["conversation"]
    assert data["context_usage"] is None
    assert data["project_id"] == PROJECT_ID
    assert data["agent_id"] == AGENT_ID
    assert data["agent_name"] == AGENT_NAME
    # 离线分支不触碰在线视图能力（避免再次吃到 NOT_FOUND）。
    assert agents.get_calls == []


async def test_online_agent_keeps_legacy_receipt_fields(tmp_path: Path) -> None:
    """在线分支保留 C3 前的回执字段（前端 bind/dialog 的消费面）。"""
    view = AgentView(
        agent_id=AGENT_ID,
        project_id=PROJECT_ID,
        name=AGENT_NAME,
        state={"name": AGENT_NAME, "initialized": True},
        abilities=["conversation"],
    )
    agents = _FakeAgents(availability="online", view=view)
    unit, transport = _unit(tmp_path, agents)
    await transport.ensure_cluster(
        PROJECT_ID,
        project_id=PROJECT_ID,
        project_root_locator=str(Path("/tmp") / PROJECT_ID),
    )

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is True, result
    data = result["data"]
    assert data["name"] == AGENT_NAME
    assert data["state"] == {"name": AGENT_NAME, "initialized": True}
    assert data["abilities"] == ["conversation"]
    # 端口视图未携带的字段由 Subject 侧兜底（空类型目录 / 无用量快照）。
    assert data["available_ability_types"] == []
    assert data["context_usage"] is None
    assert data["runtime_state"] == "running"
    assert data["project_id"] == PROJECT_ID
    assert data["agent_id"] == AGENT_ID
    assert data["agent_name"] == AGENT_NAME


async def test_online_agent_passes_through_core_receipt_fields(tmp_path: Path) -> None:
    """端口视图若已承载 ``context_usage`` / ``available_ability_types``，原样透传。"""
    view = AgentView(
        agent_id=AGENT_ID,
        project_id=PROJECT_ID,
        name=AGENT_NAME,
        state={"name": AGENT_NAME, "initialized": True},
        abilities=["conversation"],
        available_ability_types=["read_file"],
        context_usage={"budget_tokens": 1024, "occupied_tokens": 128},
    )
    agents = _FakeAgents(availability="online", view=view)
    unit, transport = _unit(tmp_path, agents)
    await transport.ensure_cluster(
        PROJECT_ID,
        project_id=PROJECT_ID,
        project_root_locator=str(Path("/tmp") / PROJECT_ID),
    )

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is True, result
    data = result["data"]
    assert data["available_ability_types"] == ["read_file"]
    assert data["context_usage"] == {"budget_tokens": 1024, "occupied_tokens": 128}
    assert data["runtime_state"] == "running"


async def test_unmounted_cluster_reads_definition_without_port(tmp_path: Path) -> None:
    """cluster 未登记（stopped/archived 后 dispose）→ 既有定义态兜底不变。"""
    agents = _FakeAgents(availability="offline", get_error=_not_online_error())
    unit, transport = _unit(tmp_path, agents)
    assert transport.has_cluster(PROJECT_ID) is False

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is True, result
    assert result["data"]["runtime_state"] == "stopped"
    # 未登记 cluster 时不得为读命令挂载/探测端口。
    assert agents.runtime_calls == []
    assert agents.get_calls == []


async def test_vanished_agent_race_falls_back_to_definition_state(tmp_path: Path) -> None:
    """探测在线但取视图时 Agent 已消失（竞态）→ 仍回定义态而非失败。"""
    agents = _FakeAgents(availability="online", get_error=_not_online_error())
    unit, transport = _unit(tmp_path, agents)
    await transport.ensure_cluster(
        PROJECT_ID,
        project_id=PROJECT_ID,
        project_root_locator=str(Path("/tmp") / PROJECT_ID),
    )

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is True, result
    assert result["data"]["runtime_state"] == "stopped"
    assert agents.get_calls == [AGENT_ID]


async def test_non_offline_error_is_not_masked(tmp_path: Path) -> None:
    """非「不在线」的端口失败原样透传，不伪装成定义态。"""
    agents = _FakeAgents(
        availability="online",
        get_error=ApiError(
            Problem.make(
                ProblemCode.INVALID_ARGUMENT,
                capability="agents.get",
                detail="bad agent id",
            )
        ),
    )
    unit, transport = _unit(tmp_path, agents)
    await transport.ensure_cluster(
        PROJECT_ID,
        project_id=PROJECT_ID,
        project_root_locator=str(Path("/tmp") / PROJECT_ID),
    )

    result = await unit.handle_command("get_agent_info", _info_payload(), CommandContext.internal())

    assert result["success"] is False, result
    assert "INVALID_ARGUMENT" in str(result["error"])
