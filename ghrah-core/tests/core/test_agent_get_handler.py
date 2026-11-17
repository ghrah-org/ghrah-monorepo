# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``agents.get`` 处理器：在线 Agent 的 ``get_agent_info`` 字段须完整透传。

C3 端口化后观察端仍消费 ``context_usage``（用量快照恢复）与
``available_ability_types``（能力弹窗）。CoreUnit ``get_agent_info`` 本来
就返回这两个字段，处理器不得再把它们丢掉。
"""

from __future__ import annotations

from typing import Any

import pytest
from ghrah.protocol.problem import ProblemCode

from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.handlers.agents import make_agent_handlers
from ghrah.core.api.interfaces import AgentGetRequest

AGENT_ID = "a" * 32


class _FakeRegistry:
    """只实现 ``agents.get`` 用到的两条命令。"""

    scope = "project-1"

    def __init__(self, *, online: bool, info: dict[str, Any] | None = None) -> None:
        self._online = online
        self._info = info or {}
        self.commands: list[str] = []

    async def handle_command(self, *, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append(command)
        if command == "list_agents":
            agents = [{"agent_id": AGENT_ID, "name": "requirement"}] if self._online else []
            return {"success": True, "data": {"agents": agents}}
        if command == "get_agent_info":
            return {"success": True, "data": self._info}
        raise AssertionError(f"unexpected command: {command}")


async def test_get_agent_passes_through_context_usage_and_ability_types() -> None:
    """在线回执保留 ``get_agent_info`` 的全部既有字段。"""
    registry = _FakeRegistry(
        online=True,
        info={
            "name": "requirement",
            "state": {"name": "requirement", "initialized": True},
            "abilities": ["conversation"],
            "available_ability_types": ["read_file"],
            "context_usage": {"budget_tokens": 2048, "occupied_tokens": 256},
        },
    )
    handler = make_agent_handlers(registry)["agents.get"]  # type: ignore[arg-type]

    view = await handler(AgentGetRequest(agent_id=AGENT_ID))

    assert view.agent_id == AGENT_ID
    assert view.project_id == "project-1"
    assert view.name == "requirement"
    assert view.state == {"name": "requirement", "initialized": True}
    assert view.abilities == ["conversation"]
    assert view.available_ability_types == ["read_file"]
    assert view.context_usage == {"budget_tokens": 2048, "occupied_tokens": 256}


async def test_get_agent_offline_still_not_found() -> None:
    """离线 Agent 仍由 Core 报 NOT_FOUND（Subject 负责定义态兜底，不在此伪造）。"""
    registry = _FakeRegistry(online=False)
    handler = make_agent_handlers(registry)["agents.get"]  # type: ignore[arg-type]

    with pytest.raises(CoreApiError) as exc_info:
        await handler(AgentGetRequest(agent_id=AGENT_ID))

    assert exc_info.value.problem.code is ProblemCode.NOT_FOUND
    assert "not online" in exc_info.value.problem.detail
