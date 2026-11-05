# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Observer 命令路由器。

解析 Observer WebSocket 消息类型，路由到对应的处理器。

路由规则（Ouroboros 装配形态）：
    - subscribe/unsubscribe → 本地 ConnectionManager（不进分发）
    - 其余所有 Observer 命令 → 统一委派 engine.dispatch_observer_command
      （observer unit 的 _EngineDispatchAdapter → bridge_command/ctx.serial）
    - event → 本地 EventBus 发布（handle_event）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ghrah.protocol.types import (
    BaseModel,
    CommandType,
    EventType,
    Message,
    SubscribePayload,
    UnsubscribePayload,
    create_command_result,
    expect_payload,
    generate_request_id,
    payload_agent_name,
)

from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus

if TYPE_CHECKING:
    # duck-typed engine 形状：仅消费 dispatch_observer_command（实际传入
    # observer unit 的 _EngineDispatchAdapter → bridge_command/ctx.serial）
    from typing import Protocol

    class SubjectEngine(Protocol):
        async def dispatch_observer_command(
            self,
            command: str,
            payload: dict[str, Any],
            *,
            request_id: str | None = None,
            session_id: str | None = None,
        ) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    """将 payload（dict 或 BaseModel 实例）归一为 dict，供 engine 分发使用。"""
    if isinstance(payload, BaseModel):
        return payload.model_dump()
    if isinstance(payload, dict):
        return payload
    return {}


class ObserverRouter:
    """Observer 消息路由器。

    subscribe/unsubscribe 在本地经 ConnectionManager 处理（连接订阅
    状态属于 Observer server 所有权，不进入 Subject runtime）。其余
    所有 Observer 命令统一经 ``engine.dispatch_observer_command`` 路由到
    注册了对应 RouteSpec 的 Subject unit。
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        event_bus: EventBus,
        *,
        engine: SubjectEngine,
    ) -> None:
        self._connection_manager = connection_manager
        self._event_bus = event_bus
        self._engine = engine

    async def handle_command(
        self,
        message: Message,
        session_id: str,
    ) -> Message | None:
        """处理来自 Observer 的命令消息。

        Args:
            message: 收到的 WebSocket 消息
            session_id: 发送者的 session ID

        Returns:
            命令执行结果消息，或 None（subscribe/unsubscribe 已本地处理）
        """
        request_id = message.request_id or generate_request_id()

        # 订阅/取消订阅：本地处理（ConnectionManager 所有权，不进 engine）
        if message.type == CommandType.SUBSCRIBE.value:
            return await self._handle_subscribe(message, session_id, request_id)

        if message.type == CommandType.UNSUBSCRIBE.value:
            return await self._handle_unsubscribe(message, session_id, request_id)

        # 其余所有 Observer 命令统一走 engine
        result = await self._engine.dispatch_observer_command(
            message.type,
            _payload_to_dict(message.payload),
            request_id=request_id,
            session_id=session_id,
        )
        return create_command_result(
            request_id=request_id,
            success=bool(result.get("success", False)),
            data=result.get("data"),
            error=result.get("error"),
            error_detail=result.get("error_detail"),
        )

    async def handle_event(
        self,
        message: Message,
        session_id: str,
    ) -> None:
        """处理事件消息，发布到 EventBus。"""
        try:
            event_type = EventType(message.type)
        except ValueError:
            logger.warning(f"Unknown event type: {message.type}")
            return

        agent_name = payload_agent_name(message.payload)
        logger.info(
            "handle_event: event_type=%s session_id=%s agent=%s publishing via EventBus",
            event_type.value,
            session_id,
            agent_name,
        )
        await self._event_bus.publish(message)

    # ─── 本地处理 ───

    async def _handle_subscribe(
        self, message: Message, session_id: str, request_id: str
    ) -> Message:
        payload = expect_payload(message, SubscribePayload)
        self._connection_manager.subscribe(
            session_id=session_id,
            agent_names=payload.agent_names,
            event_types=payload.event_types,
        )
        sub_info = self._connection_manager.get_subscription_info(session_id)
        return create_command_result(
            request_id=request_id,
            success=True,
            data=sub_info,
        )

    async def _handle_unsubscribe(
        self, message: Message, session_id: str, request_id: str
    ) -> Message:
        payload = expect_payload(message, UnsubscribePayload)
        self._connection_manager.unsubscribe(
            session_id=session_id,
            agent_names=payload.agent_names,
            event_types=payload.event_types,
        )
        sub_info = self._connection_manager.get_subscription_info(session_id)
        return create_command_result(
            request_id=request_id,
            success=True,
            data=sub_info,
        )
