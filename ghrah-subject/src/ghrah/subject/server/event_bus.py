# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""事件总线。

管理事件向 Observer 连接的推送，支持按 Agent 和事件类型过滤订阅。
包含 EventStore 用于新连接的事件重放。
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from ghrah.protocol.types import EventType, Message, payload_agent_name

from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_attribution import (
    AGENT_SCOPED_EVENT_TYPES,
    fill_attribution,
)

logger = logging.getLogger(__name__)


class EventStore:
    """环形事件缓冲区，支持按 seq_id 重放。"""

    def __init__(self, capacity: int = 1000) -> None:
        self._capacity = capacity
        self._buffer: deque[tuple[int, dict[str, Any]]] = deque()
        self._seq_counter: int = 0

    def store(self, event_dict: dict[str, Any]) -> int:
        self._seq_counter += 1
        self._buffer.append((self._seq_counter, event_dict))
        while len(self._buffer) > self._capacity:
            self._buffer.popleft()
        return self._seq_counter

    def replay_since(self, last_seq_id: int) -> list[dict[str, Any]]:
        return [evt for seq, evt in self._buffer if seq > last_seq_id]


class EventBus:
    """Observer 事件总线。

    负责：
        - 接收来自 Core 转发的事件和本地事件
        - 根据订阅关系过滤并推送给 Observer 连接
        - 支持事件重放（新 Observer 连接后获取历史事件）
    """

    def __init__(
        self,
        connection_manager: ConnectionManager,
        event_store_capacity: int = 1000,
        *,
        agent_project_resolver: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        """初始化事件总线。

        Args:
            connection_manager: Observer 连接管理器
            event_store_capacity: 重放缓冲区容量
            agent_project_resolver: ``agent_id`` → ``project_id`` 的归属解析器。
                Core 事件不再携带 Project 归属（M1），转发前由 Subject 用自身
                权威映射补齐；None 表示无从归属（缺失归属的 Agent 作用域事件
                将被丢弃并计数）。
        """
        self._connection_manager = connection_manager
        self._running = False
        self.event_store = EventStore(capacity=event_store_capacity)
        self._agent_project_resolver = agent_project_resolver
        # Agent 作用域事件缺失 project_id/agent_id 被丢弃的计数（诊断口径，
        # 供健康检查/metrics 消费；正常应为 0，增长说明存在漏归属的生产者）。
        self.dropped_unattributed_events = 0

    async def start(self) -> None:
        self._running = True
        logger.info("Observer EventBus started")

    async def stop(self) -> None:
        self._running = False
        logger.info("Observer EventBus stopped")

    def subscribe(
        self,
        session_id: str,
        agent_names: list[str] | None = None,
        event_types: list[str] | None = None,
    ) -> None:
        """为指定 session 订阅事件（委托 ConnectionManager）。"""
        self._connection_manager.subscribe(
            session_id=session_id,
            agent_names=agent_names,
            event_types=event_types,
        )

    def unsubscribe(
        self,
        session_id: str,
        agent_names: list[str] | None = None,
        event_types: list[str] | None = None,
    ) -> None:
        """为指定 session 取消订阅事件（委托 ConnectionManager）。"""
        self._connection_manager.unsubscribe(
            session_id=session_id,
            agent_names=agent_names,
            event_types=event_types,
        )

    async def publish(self, event: Message) -> int:
        """发布事件到所有订阅的 Observer 连接。

        Args:
            event: 事件消息

        Returns:
            成功推送的连接数
        """
        event_type = event.type
        agent_name = payload_agent_name(event.payload)

        message_dict = event.model_dump_with_timestamp()

        seq_id = self.event_store.store(message_dict)
        message_dict["seq_id"] = seq_id

        sent_count = await self._connection_manager.broadcast(
            message=message_dict,
            agent_name=agent_name,
            event_type=event_type,
        )

        logger.info(
            "Event '%s' published to %d Observer sessions (agent=%s)",
            event_type,
            sent_count,
            agent_name,
        )
        return sent_count

    async def emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> int:
        """便捷方法：创建并发布事件。"""
        event = Message(
            type=event_type.value,
            payload=payload,
        )
        return await self.publish(event)

    async def emit_hitl_request(
        self,
        promise_id: str,
        agent_name: str,
        ability_name: str,
        tool_args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> int:
        """发布 hitl_request 事件到 Observer。"""
        from ghrah.protocol.types import HITLRequestPayload

        event = Message(
            type=EventType.HITL_REQUEST.value,
            payload=HITLRequestPayload(
                promise_id=promise_id,
                agent_name=agent_name,
                ability_name=ability_name,
                tool_args=tool_args or {},
                context=context or {},
            ),
        )
        return await self.publish(event)

    async def _attribute(self, payload: dict[str, Any]) -> str:
        """解析并补齐事件归属，返回可用的 ``project_id``（无法归属时为空串）。

        Core 事件只保证 ``agent_id``；Project 归属由 Subject 的权威映射补齐
        （``agent_project_resolver``，通常来自 ProjectStore）。payload 已带非空
        ``project_id`` 时直接采用（迁移期兼容），但仍会用它补齐嵌套 ``session``。
        """
        project_id = str(payload.get("project_id") or "")
        if not project_id:
            agent_id = str(payload.get("agent_id") or "")
            resolver = self._agent_project_resolver
            if agent_id and resolver is not None:
                try:
                    project_id = str(await resolver(agent_id) or "")
                except Exception:
                    logger.warning(
                        "agent→project 归属解析失败: agent_id=%s", agent_id, exc_info=True
                    )
                    project_id = ""
        if project_id:
            fill_attribution(payload, project_id)
        return project_id

    async def emit_core_event(self, event: Message) -> int:
        """转发来自 Core 的事件到 Observer。

        用于将从 Core 连接收到的事件（agent_spawned 等）转发给 Observer。
        将 payload 中 "name" 键映射为 "agent_name" 以确保订阅过滤正常工作。

        Agent 作用域事件（AGENT_SCOPED_EVENT_TYPES）由 Subject 补齐
        ``project_id``（Core 不再携带归属）；补齐失败或缺失 ``agent_id`` 时丢弃
        并计数——不猜测归属，防止污染 Observer 侧的 Project 分桶。
        """
        from ghrah.protocol.types import BaseModel

        payload = event.payload
        if isinstance(payload, BaseModel):
            payload = payload.model_dump()
        if not isinstance(payload, dict):
            payload = {}
        else:
            payload = dict(payload)
        if event.type in AGENT_SCOPED_EVENT_TYPES:
            project_id = await self._attribute(payload)
            if not project_id or not str(payload.get("agent_id") or ""):
                self.dropped_unattributed_events += 1
                logger.warning(
                    "Dropped unattributed core event '%s' (missing project_id/agent_id)",
                    event.type,
                )
                return 0
        if "agent_name" not in payload and "name" in payload:
            payload["agent_name"] = payload["name"]
        event = Message(type=event.type, payload=payload)
        logger.info(
            "Emitting core event to Observer: type=%s agent=%s",
            event.type,
            payload.get("agent_name") or payload.get("name", ""),
        )
        return await self.publish(event)
