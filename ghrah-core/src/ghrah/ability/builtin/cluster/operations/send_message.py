# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SendMessageAbility：向指定 Agent 发送消息，在本轮提交中准备持久请求。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

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
TITLE = "Send Message"
SUMMARY = (
    "Prepare a persistent request to another agent. Delivery starts after commit. By "
    "default replies arrive through the persistent mailbox; fire_and_forget=false waits "
    "after a durable checkpoint."
)
TAGS = [
    "cluster",
    "communication",
]
EFFECTS = ["cluster:message"]
TIMEOUT_MS = 60_000
# --- 成员声明结束


class SendMessageInput(BaseModel):
    model_config = {"extra": "forbid"}

    target: str = Field(min_length=1, description="Target agent name or stable ID")
    content: str = Field(min_length=1, description="Message content to send")
    fire_and_forget: bool = Field(
        default=True, description="If false, return the durable reply in this tool call"
    )


Input = SendMessageInput  # v1 生成器入口别名（生成器只读 module.Input）


class SendMessageAwaitReplyOutput(BaseModel):
    """点对点发送在持久检查点之后等待回复的句柄。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="Prepared message id")
    mode: str = Field(description="Wait mode")
    target: str = Field(description="Agent the reply is expected from")
    timeout: float = Field(description="Wait timeout in seconds")


class SendMessageOutput(BaseModel):
    """SendMessage 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="Prepared persistent message id")
    recipients: list[str] = Field(description="Resolved recipient ids")
    status: str = Field(description="Preparation status")
    mode: str = Field(description="persistent, or async when fire_and_forget is set")
    recipient_names: dict[str, str] = Field(
        description="Stable recipient id to runtime display name"
    )
    target: str = Field(description="Requested target name or stable id")
    await_reply: SendMessageAwaitReplyOutput | None = Field(
        default=None, description="Durable wait handle when fire_and_forget is false"
    )


Output = SendMessageOutput  # v1 生成器入口别名（生成器只读 module.Output）


class SendMessageAbility(Ability):
    """在当前持久迭代中准备请求，提交后投递；回复经持久 mailbox 返回。"""

    @property
    def name(self) -> str:
        return "send_message"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Prepare a persistent request to another agent. Delivery starts "
                "after the current action commits. Replies arrive through the persistent mailbox. "
                "By default returns a prepared message ID; fire_and_forget=false waits "
                "for the durable reply after committing a wait checkpoint.",
                "parameters": SendMessageInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return "send_message(target, content, fire_and_forget=True): durable send or reply wait"

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        supervisor = services.get(SUPERVISOR)
        error = cluster_supervisor_error(supervisor)
        if error:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": error})
        tool_args = call.arguments
        if not tool_args.get("target"):
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "target is required"})
        if not tool_args.get("content"):
            return ActionResult(
                outcome=ActionOutcome.FAILURE, data={"error": "content is required"}
            )
        try:
            args = SendMessageInput.model_validate(tool_args)
            cm = services.get(CONTEXT_MANAGER)
            call_id = call.tool_call_id
            if cm is None or not call_id:
                raise RuntimeError("Persistent sending requires an action context and tool_call_id")
            data = await supervisor.prepare_message(
                targets=(args.target,),
                content=args.content,
                sender=call.identity.agent_name,
                idempotency_key=f"tool:{cm.active_head.id}:{call_id}",
                metadata={"inline_reply": True} if not args.fire_and_forget else None,
            )
            if not args.fire_and_forget:
                data["await_reply"] = {
                    "message_id": data["message_id"],
                    "mode": "sync",
                    "target": args.target,
                    "timeout": supervisor._resolve_timeout(args.target, None),
                }
            if args.fire_and_forget:
                data["mode"] = "async"
            return ActionResult(outcome=ActionOutcome.SUCCESS, data={**data, "target": args.target})
        except Exception as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to prepare persistent message: {exc}"},
            )
