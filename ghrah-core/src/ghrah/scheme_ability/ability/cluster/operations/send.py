# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SendAbility：向 room（及可选定向 targets）发消息的 send 工具。

send 回路（agent 自觉调用路径）：
- 入参 ``{room_ids, targets?, message}``；
- recipients = Σ room 的 agent 成员 ∪ targets 去重（RoomBridge 解析）；
- Supervisor 准备持久出站草稿（排除发送者自身）；
- 草稿与 action 节点同事务提交后，由 MM 投递并重放 Room 引用日志。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.core.room_protocol import (
    ROOM_BRIDGE_UNAVAILABLE_ERROR,
    RoomBridgeProtocol,
)
from ghrah.scheme_ability._cluster_common import cluster_supervisor_error
from ghrah.scheme_ability.execution.base import Ability
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.scheme_ability.execution.context import AbilityExecutionContext
    from ghrah.scheme_ability.execution.hooks import Hook


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/send.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
#   HOOKS       <- 旧 manifest 的 hooks
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这两项不是权威。
# ---------------------------------------------------------------------------
# send 当前是 registry-only（无 manifest 声明），权限事实只在部署配置里。
PERMISSIONS = {}
HOOKS = {}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = ""
TAGS = []
# --- catalogue 声明结束


class SendInput(BaseModel):
    model_config = {"extra": "forbid"}

    room_ids: list[str] = Field(
        min_length=1,
        description="Room ids to send the message to (members are resolved as recipients)",
    )
    message: str = Field(
        min_length=1,
        description="Message content to send",
    )
    targets: list[str] | None = Field(
        default=None,
        description=("Optional extra direct targets (agent names) in addition to the room members"),
    )


class SendAbility(Ability):
    """send 工具：room 维度发信（成员展开 + Supervisor 投递 + RoomLog 落账）。"""

    @property
    def name(self) -> str:
        return "send"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "send",
                "description": (
                    "Send a message to rooms. Recipients are all agent members "
                    "of the given rooms plus optional direct targets. The "
                    "message is appended to each room's log and delivered via "
                    "the supervisor."
                ),
                "parameters": SendInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "send(room_ids: list[str], message: str, targets: list[str] | None = None) -> dict: "
            "Send a message to rooms (members resolved as recipients) with room log append"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        supervisor = context.supervisor
        error = cluster_supervisor_error(supervisor)
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )
        bridge: RoomBridgeProtocol | None = getattr(supervisor, "room_bridge", None)
        if bridge is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": ROOM_BRIDGE_UNAVAILABLE_ERROR},
            )

        tool_args = context.tool_args or context.accumulated_data.get("tool_args", {})
        try:
            args = SendInput.model_validate(
                {k: v for k, v in tool_args.items() if k in SendInput.model_fields}
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"invalid send args: {e}"},
            )

        sender = context.agent_name or "unknown"
        targets = args.targets or []

        resolved = await bridge.resolve_recipients(args.room_ids, targets)
        if not resolved.get("success"):
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": resolved.get("error") or "failed to resolve recipients"},
            )
        resolved_data = resolved.get("data") or {}
        recipients: list[str] = list(resolved_data.get("recipients", []))
        # Room 成员可能使用稳定 ID；排除自身同时比较展示名和 Core 身份。
        sender_ids = {sender}
        stable_id = getattr(context.context_manager, "agent_name", None)
        if isinstance(stable_id, str):
            sender_ids.add(stable_id)
        to_deliver = [r for r in recipients if r not in sender_ids]

        cm = context.context_manager
        call_id = context.invocation.tool_call_id
        if cm is None or not call_id:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "Room sending requires a persistent action and tool_call_id"},
            )
        try:
            data = await supervisor.prepare_message(
                targets=to_deliver,
                content=args.message,
                sender=sender,
                idempotency_key=f"tool:{cm.active_head.id}:{call_id}",
                metadata={"room_ids": args.room_ids, "room_projection": True, "targets": targets},
            )
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={
                    **data,
                    "recipients": recipients,
                    "delivered_to": to_deliver,
                    "rooms": args.room_ids,
                },
            )
        except Exception as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to prepare Room message: {exc}"},
            )
