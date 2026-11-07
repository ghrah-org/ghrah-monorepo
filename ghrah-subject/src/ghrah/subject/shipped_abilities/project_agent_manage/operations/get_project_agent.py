# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""查单个 Project Agent 的完整定义（读 desired-state）。"""

from __future__ import annotations

from typing import Any

from _shared import (  # type: ignore[import-not-found]
    port_or_failure,
    project_from_get_receipt,
    project_id_or_failure,
    receipt_to_result,
)
from ghrah.protocol.enums import CommandType
from ghrah.types.results import ActionOutcome, ActionResult
from pydantic import BaseModel, ConfigDict, Field

PERMISSIONS: dict[str, object] = {}
TITLE = "Get Project Agent"
SUMMARY = "按 agent_id 或名称查询当前 Project 名册中单个 Agent 的完整定义。"
TAGS = ["project", "agent", "discovery"]
EFFECTS: list[str] = []
TIMEOUT_MS = 60_000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(default="", description="按显示名查找")
    agent_id: str = Field(default="", description="按稳定标识查找；优先于名称")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: dict[str, Any] = Field(description="Agent 完整定义（Project 名册形状）")
    project_id: str = Field(description="所属 Project 标识")


async def execute(call: Any, services: Any) -> ActionResult:
    project_id = project_id_or_failure(call)
    if isinstance(project_id, ActionResult):
        return project_id
    port = port_or_failure(services)
    if isinstance(port, ActionResult):
        return port
    args = dict(call.arguments)
    agent_id = str(args.get("agent_id") or "")
    agent_name = str(args.get("agent_name") or "")
    if not agent_id and not agent_name:
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": "agent_id or agent_name is required"},
        )
    receipt = await port.dispatch(
        CommandType.PROJECT_GET.value,
        {"project_id": project_id},
    )
    if not isinstance(receipt, dict) or not receipt.get("success"):
        return receipt_to_result(receipt if isinstance(receipt, dict) else {})
    project = project_from_get_receipt(receipt)
    agents = project.get("agents") if isinstance(project, dict) else None
    if not isinstance(agents, list):
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": "project_get returned no agents list"},
        )
    match = next(
        (
            item
            for item in agents
            if isinstance(item, dict)
            and (
                (agent_id and item.get("agent_id") == agent_id)
                or (not agent_id and agent_name and item.get("name") == agent_name)
            )
        ),
        None,
    )
    if match is None:
        identity = agent_id or agent_name or "<unknown>"
        return ActionResult(
            outcome=ActionOutcome.FAILURE,
            data={"error": f"agent_not_found: {identity}"},
        )
    return receipt_to_result(
        receipt,
        ok_data={"agent": match, "project_id": project_id},
    )
