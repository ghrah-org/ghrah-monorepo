# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""``messages`` 分组的处理器。

发送/广播/委派都是单元的既有命令；本层负责把端口形状翻译成命令载荷，
并按 ``agent_id`` 解析单元使用的名字。
"""

from __future__ import annotations

from ghrah.protocol.capability import Handler
from ghrah.protocol.problem import Problem, ProblemCode
from ghrah.protocol.views.core import BroadcastRepliesView, MessageReplyView

from ghrah.core.api.capabilities import (
    MessagesAppendRequest,
    MessagesBroadcastRequest,
    MessagesDelegateRequest,
)
from ghrah.core.api.errors import CoreApiError
from ghrah.core.api.handlers.session_write import _ok_or_raise, resolve_agent_name
from ghrah.core.units.registry import RuntimeRegistry

__all__ = ["make_message_handlers"]


def make_message_handlers(registry: RuntimeRegistry) -> dict[str, Handler]:
    """构造 ``messages`` 分组处理器。

    Args:
        registry: 运行时注册表

    Returns:
        能力名到处理器的映射
    """

    async def append(request: MessagesAppendRequest) -> MessageReplyView:
        """将请求投递给指定 Agent，保留调用者身份。"""
        result = await registry.handle_command(
            project_id=request.project_id,
            command="send_message",
            payload={
                "project_id": request.project_id,
                "agent_id": request.agent_id,
                "sender": request.sender,
                "timeout": request.timeout,
                "target": request.target,
                "content": request.content,
                "metadata": request.metadata,
            },
        )
        if not result.get("success") and result.get("error") == "source_ability_missing":
            raise CoreApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="messages.append",
                    detail=result["error_detail"],
                    details={"reason": "source_ability_missing", "data": result.get("data")},
                )
            )
        return MessageReplyView.model_validate(_ok_or_raise(result, "messages.append"))

    async def broadcast(request: MessagesBroadcastRequest) -> BroadcastRepliesView:
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
        replies = BroadcastRepliesView.model_validate(data)
        if not replies.responses:
            raise CoreApiError(
                Problem.make(
                    ProblemCode.FAILED_PRECONDITION,
                    capability="messages.broadcast",
                    detail="no Agent replied to broadcast",
                )
            )
        return replies

    async def delegate(request: MessagesDelegateRequest) -> MessageReplyView:
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
        return MessageReplyView.model_validate(_ok_or_raise(result, "messages.delegate"))

    return {
        "messages.append": append,
        "messages.broadcast": broadcast,
        "messages.delegate": delegate,
    }
