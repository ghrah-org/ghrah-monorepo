# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""从当前 Project 移除 Agent 定义（同时收敛运行时）。"""

from __future__ import annotations

from typing import Any

from _shared import (  # type: ignore[import-not-found]
    port_or_failure,
    project_id_or_failure,
    receipt_to_result,
)
from ghrah.protocol.enums import CommandType
from ghrah.protocol.payloads.project import ProjectRemoveAgentPayload
from ghrah.types.results import ActionResult
from pydantic import BaseModel, ConfigDict, Field

PERMISSIONS = {"require_hitl": True}
TITLE = "Remove Project Agent"
SUMMARY = "从当前 Project 移除一个 Agent 定义，并让运行时按定义收敛。"
TAGS = ["project", "agent", "lifecycle"]
EFFECTS = ["project:agent-write"]
TIMEOUT_MS = 120_000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, description="要移除的 Agent 显示名")
    agent_id: str = Field(default="", description="稳定 Agent 标识；优先于名称匹配")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="已移除的稳定 Agent 标识")
    agent_name: str = Field(description="已移除的 Agent 显示名")


async def execute(call: Any, services: Any) -> ActionResult:
    project_id = project_id_or_failure(call)
    if isinstance(project_id, ActionResult):
        return project_id
    port = port_or_failure(services)
    if isinstance(port, ActionResult):
        return port
    args = dict(call.arguments)
    agent_name = str(args.get("agent_name") or "")
    agent_id = str(args.get("agent_id") or "")
    payload = ProjectRemoveAgentPayload(
        project_id=project_id,
        agent_name=agent_name,
        agent_id=agent_id,
    )
    receipt = await port.dispatch(
        CommandType.PROJECT_REMOVE_AGENT.value,
        payload.model_dump(mode="json"),
    )
    if not isinstance(receipt, dict) or not receipt.get("success"):
        return receipt_to_result(receipt if isinstance(receipt, dict) else {})
    data = receipt.get("data") or {}
    return receipt_to_result(
        receipt,
        ok_data={
            "agent_id": str(data.get("agent_id") or agent_id),
            "agent_name": str(data.get("agent_name") or agent_name),
        },
    )
