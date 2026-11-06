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

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import CONTEXT_MANAGER, SUPERVISOR
from ghrah.core.room_protocol import (
    ROOM_BRIDGE_UNAVAILABLE_ERROR,
    RoomBridgeProtocol,
)
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# Operation 声明（v1）：build 从下列常量与 Input/Output 模型生成 ability.yaml 的 members。
# 旧 manifest 的 metadata.description / tags / permissions 分别落在 SUMMARY / TAGS / PERMISSIONS。
# ---------------------------------------------------------------------------
# send 当前是 registry-only（无 manifest 声明），权限事实只在部署配置里。
PERMISSIONS = {}

# --- 成员声明（build 生成 members 的唯一来源；改完须重新 build）
TITLE = "Send"
SUMMARY = (
    "Send a message to rooms. Recipients are all agent members of the given rooms plus "
    "optional direct targets. The message is appended to each room's log and delivered "
    "via the supervisor."
)
TAGS = []
EFFECTS = ["cluster:message"]
TIMEOUT_MS = 60_000
# 不进模型工具面：registry-only 成员，运行时不为它投影 manifest。
EXPOSE = False
# --- 成员声明结束


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


Input = SendInput  # v1 生成器入口别名（生成器只读 module.Input）


class SendOutput(BaseModel):
    """Send 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="Prepared persistent message id")
    recipients: list[str] = Field(description="Resolved room members and extra targets")
    status: str = Field(description="Preparation status")
    mode: str = Field(description="Delivery mode")
    recipient_names: dict[str, str] = Field(
        description="Stable recipient id to runtime display name"
    )
    delivered_to: list[str] = Field(description="Recipients after the sender is excluded")
    rooms: list[str] = Field(description="Room ids the message was addressed to")


Output = SendOutput  # v1 生成器入口别名（生成器只读 module.Output）


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

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        supervisor = services.get(SUPERVISOR)
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

        tool_args = call.arguments
        try:
            args = SendInput.model_validate(
                {k: v for k, v in tool_args.items() if k in SendInput.model_fields}
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"invalid send args: {e}"},
            )

        sender = call.identity.agent_name or "unknown"
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
        stable_id = getattr(services.get(CONTEXT_MANAGER), "agent_name", None)
        if isinstance(stable_id, str):
            sender_ids.add(stable_id)
        to_deliver = [r for r in recipients if r not in sender_ids]

        cm = services.get(CONTEXT_MANAGER)
        call_id = call.tool_call_id
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
