# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""QueryAgentsAbility：查询集群中已注册的 Agent 信息。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.abilities.builtin._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.execution_services import SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.hooks import Hook
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall


class QueryAgentsInput(BaseModel):
    model_config = {"extra": "forbid"}

    filter: str | None = Field(
        default=None,
        description="Optional substring to filter agent names",
    )


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
        error = cluster_supervisor_error(services.get(SUPERVISOR))
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = call.arguments
        name_filter = tool_args.get("filter")

        try:
            agents = await services.get(SUPERVISOR).list_agents()
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
