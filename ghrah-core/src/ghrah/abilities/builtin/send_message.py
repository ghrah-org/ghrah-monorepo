# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SendMessageAbility：向指定 Agent 发送消息，在本轮提交中准备持久请求。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.abilities.builtin._cluster_common import cluster_supervisor_error
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.context import AbilityExecutionContext
    from ghrah.abilities.hooks import Hook


class SendMessageInput(BaseModel):
    model_config = {"extra": "forbid"}

    target: str = Field(min_length=1, description="Target agent name or stable ID")
    content: str = Field(min_length=1, description="Message content to send")
    fire_and_forget: bool = Field(
        default=True, description="If false, return the durable reply in this tool call"
    )


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

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        error = cluster_supervisor_error(context.supervisor)
        if error:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": error})
        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        if not tool_args.get("target"):
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": "target is required"})
        if not tool_args.get("content"):
            return ActionResult(
                outcome=ActionOutcome.FAILURE, data={"error": "content is required"}
            )
        try:
            args = SendMessageInput.model_validate(tool_args)
            cm = context.context_manager
            call_id = context.invocation.tool_call_id
            if cm is None or not call_id:
                raise RuntimeError("Persistent sending requires an action context and tool_call_id")
            data = await context.supervisor.prepare_message(
                targets=(args.target,),
                content=args.content,
                sender=context.agent_name,
                idempotency_key=f"tool:{cm.active_head.id}:{call_id}",
                metadata={"inline_reply": True} if not args.fire_and_forget else None,
            )
            if not args.fire_and_forget:
                data["await_reply"] = {
                    "message_id": data["message_id"],
                    "mode": "sync",
                    "target": args.target,
                    "timeout": context.supervisor._resolve_timeout(args.target, None),
                }
            if args.fire_and_forget:
                data["mode"] = "async"
            return ActionResult(outcome=ActionOutcome.SUCCESS, data={**data, "target": args.target})
        except Exception as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to prepare persistent message: {exc}"},
            )
