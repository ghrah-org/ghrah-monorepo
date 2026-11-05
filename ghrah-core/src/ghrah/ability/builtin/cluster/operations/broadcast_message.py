# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""BroadcastMessageAbility：向集群中所有 Agent 广播消息。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.ability._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, SUPERVISOR
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
TITLE = "Broadcast Message"
SUMMARY = (
    "Broadcast a message to all agents in the cluster (excluding the sender) after a "
    "durable wait checkpoint and collect their replies. Use this for cluster-wide "
    "announcements or queries."
)
TAGS = [
    "cluster",
    "communication",
]
EFFECTS = ["cluster:message"]
TIMEOUT_MS = 60_000
# --- 成员声明结束


class BroadcastMessageInput(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(
        min_length=1,
        description="Message content to broadcast to all agents",
    )


Input = BroadcastMessageInput  # v1 生成器入口别名（生成器只读 module.Input）


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

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        supervisor = services.get(SUPERVISOR)
        error = cluster_supervisor_error(supervisor)
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = call.arguments
        content = tool_args.get("content", "")

        if not content:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "content is required"},
            )

        sender = call.identity.agent_name or "unknown"

        try:
            args = BroadcastMessageInput.model_validate(tool_args)
            cm = services.get(CONTEXT_MANAGER)
            call_id = call.tool_call_id
            if cm is None or not call_id:
                raise RuntimeError("Persistent sending requires an action context and tool_call_id")
            agents = await supervisor.list_agents()
            targets = tuple(agent["name"] for agent in agents if agent["name"] != sender)
            if not targets:
                return ActionResult(
                    outcome=ActionOutcome.SUCCESS,
                    data={"responses": [], "recipients": [], "agent_count": 0},
                )
            data = await supervisor.prepare_message(
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
