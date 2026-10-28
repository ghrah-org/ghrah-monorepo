# SPDX-FileCopyrightText: 2026 chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SessionInfoAbility 测试（008 T-2 工具形态）。

- 静态快照：构造注入 / set_environment_info 回填
- 动态段：agent_name + session（context_manager）/ cluster（supervisor）
- 接线缺失不整体失败，降级为指路文案
"""

from __future__ import annotations

from typing import Any

import pytest

from ghrah.scheme.ability.core.operations.session_info import SessionInfoAbility
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.types.results import ActionOutcome


class _FakeSession:
    def __init__(self) -> None:
        self.session_id = "sess-1"
        self.active_branch_id = "branch-1"


class _FakeCM:
    def get_active_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeSupervisor:
    async def list_agents(self) -> list[dict[str, Any]]:
        return [{"name": "planner"}, {"name": "coder"}]


class TestSessionInfo:
    async def test_static_snapshot_from_constructor(self) -> None:
        ability = SessionInfoAbility(
            environment_info={"deployment": "subject-mounted", "workspace_root": "/ws"}
        )
        result = await ability.execute(AbilityExecutionContext())
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["environment"] == {
            "deployment": "subject-mounted",
            "workspace_root": "/ws",
        }
        assert result.data["environment_source"] == "spawn-time snapshot"

    async def test_no_snapshot_degrades_with_pointer(self) -> None:
        """无快照 → environment=None + 指路文案，不整体失败。"""
        ability = SessionInfoAbility()
        result = await ability.execute(AbilityExecutionContext())
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["environment"] is None
        assert "unavailable" in result.data["environment_source"]

    async def test_set_environment_info_backfill(self) -> None:
        """spawn 装配期回填快照（CoreUnit duck-typed 注入路径）。"""
        ability = SessionInfoAbility()
        ability.set_environment_info({"deployment": "standalone"})
        result = await ability.execute(AbilityExecutionContext())
        assert result.data["environment"] == {"deployment": "standalone"}

    async def test_agent_and_session_sections(self) -> None:
        ability = SessionInfoAbility()
        ctx = AbilityExecutionContext(agent_name="worker-1", context_manager=_FakeCM())
        result = await ability.execute(ctx)
        assert result.data["agent"]["name"] == "worker-1"
        assert result.data["agent"]["session_id"] == "sess-1"
        assert result.data["agent"]["active_branch_id"] == "branch-1"

    async def test_context_manager_missing_session_pointer(self) -> None:
        ability = SessionInfoAbility()
        result = await ability.execute(AbilityExecutionContext(agent_name="w"))
        assert result.data["agent"]["name"] == "w"
        assert "not wired" in result.data["agent"]["session"]

    async def test_cluster_section_with_supervisor(self) -> None:
        ability = SessionInfoAbility()
        ctx = AbilityExecutionContext(supervisor=_FakeSupervisor())
        result = await ability.execute(ctx)
        assert result.data["cluster"]["count"] == 2
        assert result.data["cluster"]["members"][0] == {"name": "planner"}

    async def test_cluster_none_without_supervisor(self) -> None:
        ability = SessionInfoAbility()
        result = await ability.execute(AbilityExecutionContext())
        assert result.data["cluster"] is None

    def test_bind_tool_shape(self) -> None:
        tool = SessionInfoAbility().bind_tool()
        fn = tool["function"]
        assert fn["name"] == "session_info"
        assert fn["parameters"] == {"type": "object", "properties": {}, "required": []}

    @pytest.mark.parametrize("method", ["bind_tool", "to_prompt_description", "get_hooks"])
    def test_interface_methods(self, method: str) -> None:
        ability = SessionInfoAbility()
        assert getattr(ability, method)() is not None or method == "get_hooks"
