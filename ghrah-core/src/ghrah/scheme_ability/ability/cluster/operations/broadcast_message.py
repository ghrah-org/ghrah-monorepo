# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""BroadcastMessageAbility：向集群中所有 Agent 广播消息。"""

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
#   来源：ghrah/abilities/builtin/broadcast_message.py
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
    "Broadcast a message to all agents in the cluster (excluding the sender) after a "
    "durable wait checkpoint and collect their replies. Use this for cluster-wide "
    "announcements or queries."
)
TAGS = [
    "cluster",
    "communication",
]
# --- catalogue 声明结束


class BroadcastMessageInput(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(
        min_length=1,
        description="Message content to broadcast to all agents",
    )


class BroadcastMessageAbility(Ability):
    """向集群中所有 Agent 广播消息。

    向当前集群已注册 Agent 准备一对多持久请求（排除自身），提交后投递。
    """

    @property
    def name(self) -> str:
        return "broadcast_message"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "broadcast_message",
                "description": (
                    "Broadcast a message to all agents in the cluster "
                    "Exclude the sender; commit a durable wait checkpoint, then collect replies. "
                    "Use this for cluster-wide announcements or queries."
                ),
                "parameters": BroadcastMessageInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "broadcast_message(content: str) -> dict: "
            "Send one persistent request to all peers and collect replies"
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
        content = tool_args.get("content", "")

        if not content:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "content is required"},
            )

        sender = context.agent_name or "unknown"

        try:
            args = BroadcastMessageInput.model_validate(tool_args)
            cm = context.context_manager
            call_id = context.invocation.tool_call_id
            if cm is None or not call_id:
                raise RuntimeError("Persistent sending requires an action context and tool_call_id")
            agents = await context.supervisor.list_agents()
            targets = tuple(agent["name"] for agent in agents if agent["name"] != sender)
            if not targets:
                return ActionResult(
                    outcome=ActionOutcome.SUCCESS,
                    data={"responses": [], "recipients": [], "agent_count": 0},
                )
            data = await context.supervisor.prepare_message(
                targets=targets,
                content=args.content,
                sender=sender,
                idempotency_key=f"tool:{cm.active_head.id}:{call_id}",
                metadata={"inline_reply": True, "message_type": "broadcast"},
            )
            data["await_reply"] = {
                "message_id": data["message_id"],
                "mode": "broadcast",
                "timeout": -1,
                "targets": list(targets),
            }
            return ActionResult(
                outcome=ActionOutcome.SUCCESS, data={**data, "agent_count": len(data["recipients"])}
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to broadcast message: {e}"},
            )
