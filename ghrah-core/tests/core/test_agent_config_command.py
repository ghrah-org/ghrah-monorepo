# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``get_agent_config`` 命令：按 ``agent_id`` 读 Core 的实例配置事实。

只测读面：创建/恢复的完整链路需要真实 supervisor 与授权关系库，留给 T2 的
端到端用例；这里钉住「配置来自快照、不来自模板」这条口径。
"""

from __future__ import annotations

from pathlib import Path

from ghrah.core.agents import AgentInstanceStore, config_to_snapshot
from ghrah.core.unit import CoreUnit, CoreUnitConfig
from ghrah.types.config_types import AgentConfig, ContextConfig, ModelOverrides


async def _unit(tmp_path: Path) -> tuple[CoreUnit, AgentInstanceStore]:
    unit = CoreUnit(CoreUnitConfig(project_id="project"))
    store = AgentInstanceStore(tmp_path / "db" / "agents.sqlite3")
    await store.start()
    unit._agent_instances = store  # noqa: SLF001 — 只装配读面所需的依赖
    return unit, store


async def test_config_reads_snapshot_facts(tmp_path: Path) -> None:
    unit, store = await _unit(tmp_path)
    try:
        config = AgentConfig(
            name="weekly",
            agent_id="a" * 32,
            endpoint_name="default-llm",
            description="周报",
            system_prompt="先列事实",
            max_iterations=-1,
            communication_timeout=120,
            context=ContextConfig(
                persistence_type="json_file",
                persistence_root_dir="/tmp/ctx",
                persistence_run_id="run-1",
            ),
            model_overrides=ModelOverrides(temperature=0.3),
            workspace_root="/tmp/ws",
        )
        await store.save(
            agent_id="a" * 32,
            name="weekly",
            config=config_to_snapshot(config),
            members=[{"member": "ghrah.fs.read_file", "permissions": {"fs_write": True}}],
            mcp_refs=["search"],
        )

        result = await unit.handle_command("get_agent_config", {"agent_id": "a" * 32}, None)

        assert result is not None and result["success"] is True
        data = result["data"]
        assert data["agent_id"] == "a" * 32
        assert data["model"]["endpoint_name"] == "default-llm"
        assert data["model"]["model_overrides"]["temperature"] == 0.3
        assert data["prompt"]["system_prompt"] == "先列事实"
        assert data["context"]["max_iterations"] == -1
        assert data["context"]["communication_timeout"] == 120
        assert data["context"]["persistence"]["type"] == "json_file"
        assert data["context"]["persistence"]["root_dir"] == "/tmp/ctx"
        assert data["context"]["persistence"]["run_id"] == "run-1"
        assert data["core_only"]["workspace_root"] == "/tmp/ws"
        assert "project_id" not in data
    finally:
        await store.close()


async def test_config_reports_missing_snapshot(tmp_path: Path) -> None:
    unit, store = await _unit(tmp_path)
    try:
        result = await unit.handle_command("get_agent_config", {"agent_id": "missing"}, None)
        assert result is not None and result["success"] is False
        assert "snapshot not found" in str(result["error"])
    finally:
        await store.close()


async def test_config_without_store_is_explicit(tmp_path: Path) -> None:
    """未配置快照路径时显式报错，不静默返回空配置。"""
    del tmp_path
    unit = CoreUnit(CoreUnitConfig(project_id="project"))
    result = await unit.handle_command("get_agent_config", {"agent_id": "a"}, None)
    assert result is not None and result["success"] is False
    assert "not configured" in str(result["error"])
