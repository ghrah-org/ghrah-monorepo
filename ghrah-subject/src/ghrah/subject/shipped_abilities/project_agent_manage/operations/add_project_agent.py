# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""把 Agent 定义加入当前 Project，并让运行时按定义拉起。"""

from __future__ import annotations

from typing import Any, Literal

from _shared import (  # type: ignore[import-not-found]
    port_or_failure,
    project_id_or_failure,
    receipt_to_result,
)
from ghrah.protocol.enums import CommandType
from ghrah.protocol.payloads.project import AgentSpecSchema, ProjectAddAgentPayload
from ghrah.types.results import ActionResult
from pydantic import BaseModel, ConfigDict, Field

PERMISSIONS = {"require_hitl": True}
TITLE = "Add Project Agent"
SUMMARY = "把一个 Agent 定义加入当前 Project，并让运行时按定义拉起。"
TAGS = ["project", "agent", "lifecycle"]
EFFECTS = ["project:agent-write"]
TIMEOUT_MS = 120_000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="新 Agent 的显示名")
    manifest_ref: str = Field(default="", description="Agent manifest 引用；可空")
    endpoint_name: str | None = Field(default=None, description="可选 endpoint 名")
    system_prompt: str = Field(default="", description="可选系统提示词覆盖")
    abilities: list[str] | None = Field(default=None, description="可选能力名列表")
    desired_state: Literal["running", "stopped"] = Field(
        default="running", description="期望运行态"
    )


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="稳定 Agent 标识")
    agent_name: str = Field(description="Agent 显示名")
    runtime_pending: bool = Field(default=False, description="运行时是否仍在等待")
    runtime_error: str = Field(default="", description="运行时错误（若有）")


async def execute(call: Any, services: Any) -> ActionResult:
    project_id = project_id_or_failure(call)
    if isinstance(project_id, ActionResult):
        return project_id
    port = port_or_failure(services)
    if isinstance(port, ActionResult):
        return port
    args = dict(call.arguments)
    agent = AgentSpecSchema(
        name=str(args.get("name") or ""),
        manifest_ref=str(args.get("manifest_ref") or ""),
        endpoint_name=args.get("endpoint_name"),
        system_prompt=str(args.get("system_prompt") or ""),
        abilities=args.get("abilities"),
        desired_state=args.get("desired_state") or "running",
    )
    payload = ProjectAddAgentPayload(project_id=project_id, agent=agent)
    receipt = await port.dispatch(
        CommandType.PROJECT_ADD_AGENT.value,
        payload.model_dump(mode="json"),
    )
    if not isinstance(receipt, dict) or not receipt.get("success"):
        return receipt_to_result(receipt if isinstance(receipt, dict) else {})
    data = receipt.get("data") or {}
    return receipt_to_result(
        receipt,
        ok_data={
            "agent_id": str(data.get("agent_id") or ""),
            "agent_name": str(data.get("agent_name") or agent.name),
            "runtime_pending": bool(data.get("runtime_pending")),
            "runtime_error": str(data.get("runtime_error") or ""),
        },
    )
