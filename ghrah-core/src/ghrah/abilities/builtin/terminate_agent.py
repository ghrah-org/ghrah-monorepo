# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""TerminateAgentAbility：终止集群中的一个 Agent。"""

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


class TerminateAgentInput(BaseModel):
    model_config = {"extra": "forbid"}

    agent_name: str = Field(
        min_length=1,
        description="Name of the agent to terminate",
    )


class TerminateAgentAbility(Ability):
    """终止集群中的一个 Agent。

    通过 SupervisorActor.terminate_agent() 从集群中移除指定 Agent。
    Agent 被终止后将无法再接收消息。仅在确定 Agent 不再需要时使用。
    """

    @property
    def name(self) -> str:
        return "terminate_agent"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "terminate_agent",
                "description": (
                    "Terminate an agent in the cluster. "
                    "The agent will be removed and cannot receive further messages. "
                    "Only use this when you are certain the agent is no longer needed. "
                    "Persistent agents can receive follow-up messages without re-initialization."
                ),
                "parameters": TerminateAgentInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return "terminate_agent(agent_name: str) -> dict: Terminate an agent in the cluster"

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
        agent_name = tool_args.get("agent_name", "")

        if not agent_name:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "agent_name is required"},
            )

        try:
            await services.get(SUPERVISOR).terminate_agent(agent_name)
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={"agent_name": agent_name, "status": "terminated"},
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to terminate agent '{agent_name}': {e}"},
            )
