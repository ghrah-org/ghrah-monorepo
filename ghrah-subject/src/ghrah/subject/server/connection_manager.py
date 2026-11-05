# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Observer WebSocket 连接管理器。

维护 Observer 的 WebSocket 连接，支持按条件广播消息。
仅管理 OBSERVER 类型的连接。

内存数据结构：
    - connections: session_id → WebSocket 连接
    - subscriptions: session_id → 订阅的 Agent 名称集合（"*" 表示全部）
    - event_subscriptions: session_id → 订阅的事件类型集合
    - client_ids: client_id → session_id（用于重连时清理旧 session）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Observer WebSocket 连接管理器。

    仅处理 OBSERVER 类型的连接，维护订阅关系，
    支持按条件推送消息。

    注意：此类仅适用于 asyncio 单线程环境，非线程安全。
    """

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._event_subscriptions: dict[str, set[str]] = {}
        self._client_ids: dict[str, str] = {}

    async def connect(
        self,
        session_id: str,
        websocket: WebSocket,
        client_id: str | None = None,
    ) -> None:
        """接受并注册新的 Observer WebSocket 连接。

        Args:
            session_id: 会话唯一标识
            websocket: WebSocket 连接对象
            client_id: 客户端持久标识（可选，用于重连时清理旧 session）

        Raises:
            ValueError: 如果 session_id 已存在
        """
        if session_id in self._connections:
            raise ValueError(f"Session {session_id} already connected")

        await websocket.accept()
        self._connections[session_id] = websocket
        self._subscriptions[session_id] = {"*"}
        self._event_subscriptions[session_id] = set()
        if client_id is not None:
            self._client_ids[client_id] = session_id
        logger.info(
            f"Observer session connected: {session_id}"
            f"{f', client_id={client_id}' if client_id else ''}"
        )

    def disconnect(self, session_id: str) -> None:
        """注销 WebSocket 连接。"""
        self._connections.pop(session_id, None)
        self._subscriptions.pop(session_id, None)
        self._event_subscriptions.pop(session_id, None)
        stale_cids = [cid for cid, sid in self._client_ids.items() if sid == session_id]
        for cid in stale_cids:
            del self._client_ids[cid]
        logger.info(f"Observer session disconnected: {session_id}")

    def disconnect_by_client_id(self, client_id: str) -> str | None:
        """根据 client_id 断开旧 session。

        Returns:
            被断开的 session_id，如果没有找到返回 None
        """
        old_session_id = self._client_ids.pop(client_id, None)
        if old_session_id is None:
            return None
        if old_session_id in self._connections:
            self.disconnect(old_session_id)
            logger.info(f"Evicted stale session {old_session_id} for client_id={client_id}")
        return old_session_id

    def get_connection(self, session_id: str) -> WebSocket | None:
        """获取指定 session 的 WebSocket 连接。"""
        return self._connections.get(session_id)

    @property
    def active_sessions(self) -> list[str]:
        """获取所有活跃的 session ID 列表。"""
        return list(self._connections.keys())

    @property
    def connection_count(self) -> int:
        """获取当前活跃连接数。"""
        return len(self._connections)

    def get_subscription_info(self, session_id: str) -> dict[str, list[str]]:
        """获取指定 session 的订阅信息。"""
        return {
            "subscribed_agents": list(self._subscriptions.get(session_id, set())),
            "subscribed_events": list(self._event_subscriptions.get(session_id, set())),
        }

    def subscribe(
        self,
        session_id: str,
        agent_names: list[str] | None = None,
        event_types: list[str] | None = None,
    ) -> None:
        """为指定 session 添加订阅。

        Args:
            session_id: 会话唯一标识
            agent_names: 订阅的 Agent 名称列表，None 表示订阅全部
            event_types: 订阅的事件类型列表，None 表示订阅全部

        Raises:
            ValueError: 如果 session 不存在
        """
        if session_id not in self._connections:
            raise ValueError(f"Session {session_id} not found")

        if agent_names is not None:
            if "*" in agent_names:
                self._subscriptions[session_id] = {"*"}
            else:
                current = self._subscriptions.get(session_id, set())
                if "*" in current:
                    self._subscriptions[session_id] = set(agent_names)
                else:
                    current.update(agent_names)
                    self._subscriptions[session_id] = current

        if event_types is not None:
            self._event_subscriptions[session_id] = set(event_types)

        logger.debug(
            f"Session {session_id} subscribed: "
            f"agents={self._subscriptions.get(session_id)}, "
            f"events={self._event_subscriptions.get(session_id)}"
        )

    def unsubscribe(
        self,
        session_id: str,
        agent_names: list[str] | None = None,
        event_types: list[str] | None = None,
    ) -> None:
        """为指定 session 取消订阅。

        Raises:
            ValueError: 如果 session 不存在
        """
        if session_id not in self._connections:
            raise ValueError(f"Session {session_id} not found")

        if agent_names is not None:
            current = self._subscriptions.get(session_id, set())
            current -= set(agent_names)
            self._subscriptions[session_id] = current

        if event_types is not None:
            current = self._event_subscriptions.get(session_id, set())
            current -= set(event_types)
            self._event_subscriptions[session_id] = current

        logger.debug(
            f"Session {session_id} unsubscribed: "
            f"agents={self._subscriptions.get(session_id)}, "
            f"events={self._event_subscriptions.get(session_id)}"
        )

    def get_subscribed_sessions(
        self,
        agent_name: str | None = None,
        event_type: str | None = None,
    ) -> list[str]:
        """获取匹配订阅条件的 session 列表。"""
        result: list[str] = []

        for session_id, subscribed_agents in self._subscriptions.items():
            agent_match = (
                "*" in subscribed_agents or agent_name is None or agent_name in subscribed_agents
            )

            event_subs = self._event_subscriptions.get(session_id, set())
            event_match = len(event_subs) == 0 or event_type is None or event_type in event_subs

            if agent_match and event_match:
                result.append(session_id)

        return result

    async def broadcast(
        self,
        message: dict[str, Any],
        agent_name: str | None = None,
        event_type: str | None = None,
        exclude_session: str | None = None,
    ) -> int:
        """向匹配订阅条件的所有 Observer 连接广播消息。

        Returns:
            成功推送的连接数
        """
        target_sessions = self.get_subscribed_sessions(agent_name, event_type)

        if exclude_session and exclude_session in target_sessions:
            target_sessions.remove(exclude_session)

        async def _send(session_id: str) -> str | None:
            websocket = self._connections.get(session_id)
            if websocket is None:
                return None
            try:
                await websocket.send_json(message)
                return session_id
            except Exception:
                logger.warning(f"Failed to send to session {session_id}, removing")
                return f"fail:{session_id}"

        results = await asyncio.gather(*[_send(sid) for sid in target_sessions])
        sent_count = sum(1 for r in results if r is not None and not r.startswith("fail:"))
        failed_sessions = [r[5:] for r in results if r is not None and r.startswith("fail:")]

        for session_id in failed_sessions:
            self.disconnect(session_id)

        return sent_count

    async def send_to(self, session_id: str, message: dict[str, Any]) -> bool:
        """向指定 session 发送消息。

        Returns:
            是否发送成功
        """
        websocket = self._connections.get(session_id)
        if websocket is None:
            logger.warning(f"Session {session_id} not found for send")
            return False

        try:
            await websocket.send_json(message)
            return True
        except Exception:
            logger.warning(f"Failed to send to session {session_id}")
            self.disconnect(session_id)
            return False
