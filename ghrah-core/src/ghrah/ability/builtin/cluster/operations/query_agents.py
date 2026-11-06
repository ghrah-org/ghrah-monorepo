# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""QueryAgentsAbility：查询集群中已注册的 Agent 信息。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# Operation 声明（v1）：build 从下列常量与 Input/Output 模型生成 ability.yaml 的 members。
# 旧 manifest 的 metadata.description / tags / permissions 分别落在 SUMMARY / TAGS / PERMISSIONS。
# ---------------------------------------------------------------------------
PERMISSIONS = {}

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
TITLE = "Query Agents"
SUMMARY = (
    "Query registered agents in the cluster. Returns a list of agent names and "
    "descriptions. Optionally filter by a substring match on agent name."
)
TAGS = [
    "cluster",
    "discovery",
]
EFFECTS = []
TIMEOUT_MS = 60_000
# --- 成员声明结束


class QueryAgentsInput(BaseModel):
    model_config = {"extra": "forbid"}

    filter: str | None = Field(
        default=None,
        description="Optional substring to filter agent names",
    )


Input = QueryAgentsInput  # v1 生成器入口别名（生成器只读 module.Input）


class AgentRecordOutput(BaseModel):
    """集群里一个已注册 Agent 的公开记录。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(description="Stable agent id")
    incarnation_id: str = Field(description="Incarnation id of the running process")
    name: str = Field(description="Runtime display name")
    config: dict[str, Any] | None = Field(description="Serializable agent configuration")
    description: str = Field(description="Agent description")
    tags: list[str] = Field(description="Agent tags")
    created_at: float = Field(description="Registration time as a Unix timestamp")


class QueryAgentsOutput(BaseModel):
    """QueryAgents 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    agents: list[AgentRecordOutput] = Field(description="Registered agents matching the filter")
    count: int = Field(description="Number of agents returned")


Output = QueryAgentsOutput  # v1 生成器入口别名（生成器只读 module.Output）


class QueryAgentsAbility(Ability):
    """查询集群中已注册的 Agent 信息。

    通过 SupervisorActor.list_agents() 获取所有已注册 Agent 的信息，
    支持按名称子串过滤。

    无 Supervisor 时返回 FAILURE。
    """

    @property
    def name(self) -> str:
        return "query_agents"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "query_agents",
                "description": (
                    "Query registered agents in the cluster. "
                    "Returns a list of agent names and descriptions. "
                    "Optionally filter by a substring match on agent name."
                ),
                "parameters": QueryAgentsInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "query_agents(filter: str | None = None) -> dict: "
            "Query registered agents in the cluster"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        supervisor = services.get(SUPERVISOR)
        error = cluster_supervisor_error(supervisor)
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = call.arguments
        name_filter = tool_args.get("filter")

        try:
            agents = await supervisor.list_agents()
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to query agents: {e}"},
            )

        if name_filter:
            name_lower = name_filter.lower()
            agents = [a for a in agents if name_lower in a.get("name", "").lower()]

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={"agents": agents, "count": len(agents)},
        )
