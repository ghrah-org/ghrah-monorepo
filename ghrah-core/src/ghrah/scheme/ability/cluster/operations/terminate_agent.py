# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""TerminateAgentAbility：终止集群中的一个 Agent。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.scheme._cluster_common import cluster_supervisor_error
from ghrah.scheme.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme.execution.context import AbilityExecutionContext
    from ghrah.scheme.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/terminate_agent.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "require_hitl": True,
}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Terminate an agent in the cluster. The agent will be removed and cannot receive "
    "further messages. Only use this when you are certain the agent is no longer needed."
)
TAGS = [
    "cluster",
    "lifecycle",
]
# --- catalogue 声明结束


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

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        error = cluster_supervisor_error(context.supervisor)
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        agent_name = tool_args.get("agent_name", "")

        if not agent_name:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "agent_name is required"},
            )

        try:
            await context.supervisor.terminate_agent(agent_name)
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={"agent_name": agent_name, "status": "terminated"},
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to terminate agent '{agent_name}': {e}"},
            )
