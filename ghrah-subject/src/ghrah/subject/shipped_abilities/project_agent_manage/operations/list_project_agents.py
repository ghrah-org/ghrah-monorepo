# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""列出当前 Project 全部 Agent 及运行状态（读 desired-state）。"""

from __future__ import annotations

from typing import Any

from _shared import (  # type: ignore[import-not-found]
    port_or_failure,
    project_from_get_receipt,
    project_id_or_failure,
    receipt_to_result,
)
from ghrah.protocol.enums import CommandType
from ghrah.types.results import ActionResult
from pydantic import BaseModel, ConfigDict, Field

PERMISSIONS: dict[str, object] = {}
TITLE = "List Project Agents"
SUMMARY = (
    "列出当前 Project 名册中的全部 Agent 及运行状态。"
    "读的是 Project desired-state，不是集群运行时注册表。"
)
TAGS = ["project", "agent", "discovery"]
EFFECTS: list[str] = []
TIMEOUT_MS = 60_000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="稳定 Agent 标识")
    name: str = Field(description="显示名")
    desired_state: str = Field(description="期望运行态")
    runtime_status: str = Field(description="运行诊断状态")
    runtime_error: str = Field(default="", description="运行错误（若有）")
    manifest_ref: str = Field(default="", description="manifest 引用")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[AgentSummary] = Field(description="Project 名册中的 Agent")
    count: int = Field(description="数量")


async def execute(call: Any, services: Any) -> ActionResult:
    project_id = project_id_or_failure(call)
    if isinstance(project_id, ActionResult):
        return project_id
    port = port_or_failure(services)
    if isinstance(port, ActionResult):
        return port
    receipt = await port.dispatch(
        CommandType.PROJECT_GET.value,
        {"project_id": project_id},
    )
    if not isinstance(receipt, dict) or not receipt.get("success"):
        return receipt_to_result(receipt if isinstance(receipt, dict) else {})
    project = project_from_get_receipt(receipt)
    raw_agents = project.get("agents") or []
    agents: list[dict[str, Any]] = []
    if isinstance(raw_agents, list):
        for item in raw_agents:
            if not isinstance(item, dict):
                continue
            agents.append(
                {
                    "agent_id": str(item.get("agent_id") or ""),
                    "name": str(item.get("name") or ""),
                    "desired_state": str(item.get("desired_state") or ""),
                    "runtime_status": str(item.get("runtime_status") or ""),
                    "runtime_error": str(item.get("runtime_error") or ""),
                    "manifest_ref": str(item.get("manifest_ref") or ""),
                }
            )
    return receipt_to_result(receipt, ok_data={"agents": agents, "count": len(agents)})
