# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""QueryAgentsAbility：查询集群中已注册的 Agent 信息。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.scheme_ability._cluster_common import cluster_supervisor_error
from ghrah.scheme_ability.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext
    from ghrah.scheme_ability.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/query_agents.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = (
    "Query registered agents in the cluster. Returns a list of agent names and "
    "descriptions. Optionally filter by a substring match on agent name."
)
TAGS = [
    "cluster",
    "discovery",
]
# --- catalogue 声明结束


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

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        error = cluster_supervisor_error(context.supervisor)
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        name_filter = tool_args.get("filter")

        try:
            agents = await context.supervisor.list_agents()
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
