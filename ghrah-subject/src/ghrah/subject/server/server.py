"""Observer WebSocket 连接处理器。

处理 Observer WebSocket 连接生命周期、消息接收分发和心跳管理。

消息分发策略：
- command_result: 忽略（Observer 不应发送 command_result）
- 事件消息: 发布到 EventBus 路由给订阅者
- 命令消息: 全部异步后台 Task 处理，避免阻塞消息循环
- 心跳消息: 直接响应 pong
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect
from ghrah.protocol.types import (
    EventType,
    Message,
    SystemType,
    create_command_result,
    create_error,
    create_pong,
    envelope_from_dict,
)

from ghrah.subject.server.config import ObserverServerConfig
from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus
from ghrah.subject.server.router import ObserverRouter

logger = logging.getLogger(__name__)


_EVENT_TYPE_VALUES = frozenset(et.value for et in EventType)


class ObserverServer:
    """Observer WebSocket 服务器。

    管理所有 Observer WebSocket 连接，维护订阅关系，
    支持按条件推送消息。

    职责：
        - 接受/拒绝 WebSocket 连接
        - 为每个连接分配唯一 session_id
        - 接收消息并路由到 ObserverRouter 处理
        - 处理心跳 ping/pong
        - 管理连接断开时的清理
        - Observer 连接时重放历史事件
    """

    def __init__(
        self,
        config: ObserverServerConfig,
        connection_manager: ConnectionManager,
        router: ObserverRouter,
        event_bus: EventBus,
    ) -> None:
        self._config = config
        self._connection_manager = connection_manager
        self._router = router
        self._event_bus = event_bus
        self._session_tasks: dict[str, set[asyncio.Task[None]]] = {}

    async def handle_connection(
        self,
        websocket: WebSocket,
    ) -> None:
        """处理新的 Observer WebSocket 连接。

        为连接分配 session_id，注册到 ConnectionManager，
        然后进入消息接收循环。

        Args:
            websocket: FastAPI WebSocket 对象
        """
        session_id = uuid.uuid4().hex[:16]

        client_id = websocket.query_params.get("client_id")

        last_seq_id = 0
        try:
            raw_seq = websocket.query_params.get("last_seq_id", "0")
            last_seq_id = int(raw_seq)
        except ValueError, TypeError:
            last_seq_id = 0

        if client_id:
            evicted = self._connection_manager.disconnect_by_client_id(client_id)
            if evicted:
                logger.info(f"Evicted stale session {evicted} for client_id={client_id}")

        try:
            await self._connection_manager.connect(session_id, websocket, client_id=client_id)
            logger.info(f"Observer WebSocket session established: {session_id}")

            welcome_msg = create_command_result(
                request_id="connect",
                success=True,
                data={
                    "session_id": session_id,
                    "message": "Connected to ghrah-subject observer server",
                },
            ).model_dump_with_timestamp()
            await self._connection_manager.send_to(session_id, welcome_msg)

            if last_seq_id > 0:
                replay_events = self._event_bus.event_store.replay_since(last_seq_id)
                if replay_events:
                    logger.info(
                        f"Replaying {len(replay_events)} events to Observer "
                        f"session {session_id} (last_seq_id={last_seq_id})"
                    )
                    for event_dict in replay_events:
                        await self._connection_manager.send_to(session_id, event_dict)

            await self._message_loop(session_id, websocket)

        except WebSocketDisconnect:
            logger.info(f"Observer WebSocket disconnected: {session_id}")
        except Exception as e:
            logger.error(f"Observer WebSocket error for session {session_id}: {e}")
        finally:
            self._cancel_session_tasks(session_id)
            self._connection_manager.disconnect(session_id)
            logger.info(f"Observer session cleaned up: {session_id}")

    async def _message_loop(
        self,
        session_id: str,
        websocket: WebSocket,
    ) -> None:
        """消息接收循环。"""
        self._session_tasks[session_id] = set()

        while True:
            try:
                raw_data = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info(f"Observer WebSocket normal close from session {session_id}")
                break
            except Exception as e:
                logger.warning(f"Failed to receive message from {session_id}: {e}")
                break

            try:
                message = envelope_from_dict(raw_data)
            except Exception as e:
                error_msg = create_error(
                    code="INVALID_MESSAGE",
                    message=f"Failed to parse message: {e}",
                )
                await self._connection_manager.send_to(
                    session_id, error_msg.model_dump_with_timestamp()
                )
                continue

            # 心跳
            if message.type == SystemType.PING.value:
                pong = create_pong()
                await self._connection_manager.send_to(session_id, pong.model_dump_with_timestamp())
                continue

            # 事件消息
            if message.type in _EVENT_TYPE_VALUES:
                await self._router.handle_event(message, session_id)
                continue

            # 命令消息：后台 Task 处理，避免阻塞消息循环
            task = asyncio.create_task(
                self._handle_command_async(message, session_id),
                name=f"obs-cmd-{session_id[:8]}-{message.type}",
            )
            self._session_tasks.setdefault(session_id, set()).add(task)
            task.add_done_callback(
                lambda t, sid=session_id: self._session_tasks.get(sid, set()).discard(t)
            )

    async def _handle_command_async(self, message: Message, session_id: str) -> None:
        """在后台 Task 中处理命令，将结果发送给客户端。"""
        try:
            result = await self._router.handle_command(message, session_id)
            if result is not None:
                await self._connection_manager.send_to(
                    session_id, result.model_dump_with_timestamp()
                )
        except Exception as e:
            logger.error(f"Error in async command from Observer {session_id}: {e}")
            error_msg = create_error(
                code="COMMAND_ERROR",
                message=str(e),
                request_id=message.request_id,
            )
            await self._connection_manager.send_to(
                session_id, error_msg.model_dump_with_timestamp()
            )

    def _cancel_session_tasks(self, session_id: str) -> None:
        """取消指定 session 的所有后台任务。"""
        tasks = self._session_tasks.pop(session_id, set())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            logger.info(f"Cancelled {len(tasks)} background task(s) for session {session_id}")
