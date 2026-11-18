# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""实例生命周期命令：start / stop / update / delete 都以实例快照为配置权威。

这里只钉生命周期与快照的关系（幂等、停止保留快照、删除清快照、更新先落库）；
真正把进程拉起来的那步需要真实 supervisor 与授权关系库，属端到端范畴。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.core.agents import AgentInstanceStore, config_to_snapshot
from ghrah.core.unit import CoreUnit, CoreUnitConfig
from ghrah.types.config_types import AgentConfig

AGENT_ID = "a" * 32


class _FakeSupervisor:
    """只实现生命周期命令用到的两个动作。"""

    def __init__(self, *, online: bool = True) -> None:
        self.online = online
        self.terminated: list[str] = []

    async def list_agents(self) -> list[dict[str, Any]]:
        if not self.online:
            return []
        return [{"agent_id": AGENT_ID, "name": "weekly", "incarnation_id": "inc-1"}]

    async def terminate_agent(self, name: str) -> None:
        self.terminated.append(name)
        self.online = False


async def _unit(
    tmp_path: Path, *, online: bool = True
) -> tuple[CoreUnit, AgentInstanceStore, _FakeSupervisor]:
    unit = CoreUnit(CoreUnitConfig(project_id="project"))
    store = AgentInstanceStore(tmp_path / "db" / "agents.sqlite3")
    await store.start()
    unit._agent_instances = store  # noqa: SLF001 — 只装配命令所需的依赖
    supervisor = _FakeSupervisor(online=online)
    unit._supervisor = supervisor  # noqa: SLF001
    return unit, store, supervisor


async def _seed(store: AgentInstanceStore) -> None:
    config = AgentConfig(
        name="weekly",
        agent_id=AGENT_ID,
        endpoint_name="default-llm",
        system_prompt="先列事实",
        max_iterations=-1,
        communication_timeout=120,
        workspace_root="/tmp/ws",
    )
    await store.save(
        agent_id=AGENT_ID,
        name="weekly",
        config=config_to_snapshot(config),
        members=[{"member": "ghrah.fs.read_file"}],
    )


async def test_stop_terminates_and_keeps_snapshot(tmp_path: Path) -> None:
    unit, store, supervisor = await _unit(tmp_path)
    try:
        await _seed(store)
        result = await unit.handle_command("stop_agent_assembly", {"agent_id": AGENT_ID}, None)

        assert result is not None and result["success"] is True
        assert result["data"]["stopped"] is True
        assert supervisor.terminated == ["weekly"]
        # 快照保留：下次还能启动
        assert await store.get(AGENT_ID) is not None
    finally:
        await store.close()


async def test_stop_is_idempotent_when_offline(tmp_path: Path) -> None:
    unit, store, supervisor = await _unit(tmp_path, online=False)
    try:
        result = await unit.handle_command("stop_agent_assembly", {"agent_id": AGENT_ID}, None)

        assert result is not None and result["success"] is True
        assert result["data"]["stopped"] is False
        assert supervisor.terminated == []
    finally:
        await store.close()


async def test_delete_terminates_and_removes_snapshot(tmp_path: Path) -> None:
    unit, store, supervisor = await _unit(tmp_path)
    try:
        await _seed(store)
        result = await unit.handle_command("delete_agent_assembly", {"agent_id": AGENT_ID}, None)

        assert result is not None and result["success"] is True
        assert result["data"] == {"agent_id": AGENT_ID, "deleted": True, "stopped": True}
        assert supervisor.terminated == ["weekly"]
        assert await store.get(AGENT_ID) is None
    finally:
        await store.close()


async def test_delete_is_idempotent_when_nothing_exists(tmp_path: Path) -> None:
    unit, store, supervisor = await _unit(tmp_path, online=False)
    try:
        result = await unit.handle_command("delete_agent_assembly", {"agent_id": AGENT_ID}, None)

        assert result is not None and result["success"] is True
        assert result["data"] == {"agent_id": AGENT_ID, "deleted": False, "stopped": False}
        assert supervisor.terminated == []
    finally:
        await store.close()


async def test_start_returns_current_instance_when_already_online(tmp_path: Path) -> None:
    unit, store, supervisor = await _unit(tmp_path)
    try:
        await _seed(store)
        result = await unit.handle_command("start_agent_assembly", {"agent_id": AGENT_ID}, None)

        assert result is not None and result["success"] is True
        assert result["data"]["recovery_mode"] == "resumed"
        assert result["data"]["incarnation_id"] == "inc-1"
        assert supervisor.terminated == []
    finally:
        await store.close()


async def test_start_without_snapshot_is_explicit(tmp_path: Path) -> None:
    unit, store, _ = await _unit(tmp_path)
    try:
        result = await unit.handle_command("start_agent_assembly", {"agent_id": "missing"}, None)

        assert result is not None and result["success"] is False
        assert "snapshot not found" in str(result["error"])
    finally:
        await store.close()


async def test_update_without_snapshot_is_explicit(tmp_path: Path) -> None:
    unit, store, _ = await _unit(tmp_path)
    try:
        result = await unit.handle_command(
            "update_agent_assembly",
            {"agent_id": "missing", "spec": {"name": "weekly"}},
            None,
        )

        assert result is not None and result["success"] is False
        assert "snapshot not found" in str(result["error"])
    finally:
        await store.close()


async def test_update_model_rewrites_only_the_model_segment(tmp_path: Path) -> None:
    """``update_agent_model`` 只改模型段，其余配置保持快照现值。"""
    unit, store, _ = await _unit(tmp_path, online=False)
    try:
        await _seed(store)
        result = await unit.handle_command(
            "update_agent_model",
            {
                "agent_id": AGENT_ID,
                "endpoint_name": "glm-x",
                "model_overrides": {"temperature": 0.2, "max_tokens": 2048},
            },
            None,
        )

        assert result is not None and result["success"] is True, result
        record = await store.get(AGENT_ID)
        assert record is not None
        assert record.config["endpoint_name"] == "glm-x"
        assert record.config["model_overrides"]["temperature"] == 0.2
        # 其余配置不受影响
        assert record.config["system_prompt"] == "先列事实"
        assert record.config["max_iterations"] == -1
        assert record.members == [{"member": "ghrah.fs.read_file"}]
    finally:
        await store.close()


async def test_update_model_without_snapshot_is_explicit(tmp_path: Path) -> None:
    unit, store, _ = await _unit(tmp_path)
    try:
        result = await unit.handle_command(
            "update_agent_model",
            {"agent_id": "missing", "endpoint_name": "glm-x"},
            None,
        )

        assert result is not None and result["success"] is False
        assert "snapshot not found" in str(result["error"])
    finally:
        await store.close()


async def test_lifecycle_commands_require_agent_id(tmp_path: Path) -> None:
    unit, store, _ = await _unit(tmp_path)
    try:
        for command in (
            "start_agent_assembly",
            "stop_agent_assembly",
            "update_agent_assembly",
            "delete_agent_assembly",
        ):
            result = await unit.handle_command(command, {}, None)
            assert result is not None and result["success"] is False
            assert "agent_id is required" in str(result["error"])
    finally:
        await store.close()


async def test_lifecycle_without_store_is_explicit(tmp_path: Path) -> None:
    """未配置快照路径时显式报错，不静默当成"没有实例"。"""
    del tmp_path
    unit = CoreUnit(CoreUnitConfig(project_id="project"))
    result = await unit.handle_command("delete_agent_assembly", {"agent_id": AGENT_ID}, None)
    assert result is not None and result["success"] is False
    assert "not configured" in str(result["error"])
