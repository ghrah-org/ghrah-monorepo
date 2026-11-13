# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``messages`` 分组的处理器。

发送/广播/委派都是单元的既有命令；本层负责把端口形状翻译成命令载荷，
并按 ``agent_id`` 解析单元使用的名字。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.common import DeliveryIdView, MessageIdView
from ghrah.protocol.views.core import MessageIdListView

from ghrah.core.api.capabilities import (
    MessagesAppendRequest,
    MessagesBroadcastRequest,
    MessagesDelegateRequest,
)
from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.handlers.session_write import _ok_or_raise, resolve_agent_name
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_message_handlers"]


def _pick(data: dict[str, Any], *keys: str) -> str:
    """从结果里按候选键取第一个非空字符串。"""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def make_message_handlers(registry: RuntimeRegistry) -> dict[str, Handler]:
    """构造 ``messages`` 分组处理器。

    Args:
        registry: 运行时注册表

    Returns:
        能力名到处理器的映射
    """

    async def append(request: MessagesAppendRequest) -> MessageIdView:
        """以某 Agent 身份发送一条消息。"""
        sender = await resolve_agent_name(registry, request.project_id, request.agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="send_message",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "sender": sender,
                "target": request.target,
                "content": request.content,
                "metadata": request.metadata,
            },
        )
        data = _ok_or_raise(result, "messages.append")
        message_id = _pick(data, "message_id", "id")
        if not message_id:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.INTERNAL,
                    capability="messages.append",
                    detail="unit returned no message_id",
                ),
            )
        return MessageIdView(message_id=message_id)

    async def broadcast(request: MessagesBroadcastRequest) -> MessageIdListView:
        """向项目内全部 Agent 广播。"""
        result = await registry.handle_command(
            project_id=request.project_id,
            command="broadcast_message",
            payload={
                "project_id": request.project_id,
                "content": request.content,
                "sender": request.sender,
            },
        )
        data = _ok_or_raise(result, "messages.broadcast")
        raw = data.get("message_ids") or data.get("deliveries") or []
        ids = [str(item) for item in raw if str(item)]
        return MessageIdListView(message_ids=ids)

    async def delegate(request: MessagesDelegateRequest) -> DeliveryIdView:
        """把一个任务委派给另一个 Agent。"""
        from_name = await resolve_agent_name(registry, request.project_id, request.from_agent_id)
        to_name = await resolve_agent_name(registry, request.project_id, request.to_agent_id)
        result = await registry.handle_command(
            project_id=request.project_id,
            command="delegate",
            payload={
                "project_id": request.project_id,
                "from_agent_id": request.from_agent_id,
                "to_agent_id": request.to_agent_id,
                "from_agent": from_name,
                "to_agent": to_name,
                "content": request.content,
                "timeout": request.timeout,
            },
        )
        data = _ok_or_raise(result, "messages.delegate")
        delivery_id = _pick(data, "delivery_id", "id")
        if not delivery_id:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.INTERNAL,
                    capability="messages.delegate",
                    detail="unit returned no delivery_id",
                ),
            )
        return DeliveryIdView(delivery_id=delivery_id)

    return {
        "messages.append": append,
        "messages.broadcast": broadcast,
        "messages.delegate": delegate,
    }
