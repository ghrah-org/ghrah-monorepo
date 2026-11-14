# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""WebSocket Observer endpoint built-in Subject unit."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import FastAPI, WebSocket
from ghrah.protocol.types import EventType, Message

from ghrah.subject.config import SubjectConfig
from ghrah.subject.runtime.ouroboros_bridge import bridge_command
from ghrah.subject.runtime.service_keys import OBSERVER_ENDPOINT, OBSERVER_EVENT_BUS
from ghrah.subject.server.config import ObserverServerConfig
from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus
from ghrah.subject.server.router import ObserverRouter
from ghrah.subject.server.server import ObserverServer
from ghrah.subject.unit.base import SubjectUnit, UnitMeta

__all__ = ["WebSocketObserverEndpointUnit"]

logger = logging.getLogger(__name__)


class _ObserverEventBusAdapter:
    """Service adapter exposing the Observer event bus as a typed bridge."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    async def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        return await self._event_bus.emit_core_event(Message(type=event_type, payload=payload))

    def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        return self._event_bus.subscribe(*args, **kwargs)


class _EngineDispatchAdapter:
    """Minimal engine-shaped adapter: routes Observer commands via Ouroboros.

    ObserverRouter 仅消费 ``dispatch_observer_command``；鸭子适配零改
    ``server/``。``request_id``/``session_id`` 以 payload 副本合并注入
    （不改原 dict；``setdefault`` 尊重 payload 已有值，unit 层
    ``payload.get("request_id")`` 优先）。
    """

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def dispatch_observer_command(
        self,
        command: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        enriched = dict(payload)
        if request_id is not None:
            enriched.setdefault("request_id", request_id)
        if session_id is not None:
            enriched.setdefault("session_id", session_id)
        return await bridge_command(self._ctx, command, enriched)


class WebSocketObserverEndpointUnit(SubjectUnit):
    """Composes the Observer WebSocket server components."""

    def __init__(
        self,
        config: SubjectConfig,
        observer_config: ObserverServerConfig | None = None,
    ) -> None:
        self._config = config
        self._observer_config = observer_config
        self._connection_manager: ConnectionManager | None = None
        self._observer_event_bus: EventBus | None = None
        self._observer_event_bus_adapter: _ObserverEventBusAdapter | None = None
        self._router: ObserverRouter | None = None
        self._server: ObserverServer | None = None
        self._app: FastAPI | None = None
        self._meta = UnitMeta(
            name="websocket_observer_endpoint",
            provides=frozenset({OBSERVER_ENDPOINT, OBSERVER_EVENT_BUS}),
        )

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    @property
    def app(self) -> FastAPI:
        if self._app is None:
            raise RuntimeError("WebSocketObserverEndpointUnit has not been initialized.")
        return self._app

    @property
    def connection_manager(self) -> ConnectionManager:
        if self._connection_manager is None:
            raise RuntimeError("WebSocketObserverEndpointUnit has not been initialized.")
        return self._connection_manager

    @property
    def observer_event_bus(self) -> EventBus:
        if self._observer_event_bus is None:
            raise RuntimeError("WebSocketObserverEndpointUnit has not been initialized.")
        return self._observer_event_bus

    @property
    def router(self) -> ObserverRouter:
        if self._router is None:
            raise RuntimeError("WebSocketObserverEndpointUnit has not been initialized.")
        return self._router

    async def init(self, ctx: Any) -> None:
        config = self._observer_config or ObserverServerConfig(log_level=self._config.log_level)
        connection_manager = ConnectionManager()
        observer_event_bus = EventBus(
            connection_manager,
            event_store_capacity=config.event_replay_capacity,
        )
        router = ObserverRouter(
            connection_manager,
            observer_event_bus,
            engine=_EngineDispatchAdapter(ctx),
        )
        server = ObserverServer(config, connection_manager, router, observer_event_bus)

        self._connection_manager = connection_manager
        self._observer_event_bus = observer_event_bus
        self._observer_event_bus_adapter = _ObserverEventBusAdapter(observer_event_bus)
        self._router = router
        self._server = server
        self._app = _create_observer_app(config, server, connection_manager)

        ctx.provide(OBSERVER_ENDPOINT.name, self)
        ctx.provide(OBSERVER_EVENT_BUS.name, self._observer_event_bus_adapter)
        # 事件统一（无通配订阅）：对 EventType 全集显式订阅两种前缀——
        # core 域（CoreUnit ``core:{type}``）与 subject 域（``event/{type}``），
        # 转发直接以订阅闭包事件名构造 WS Message（无信封解包）。
        for event_type in EventType:
            ctx.on(f"core:{event_type.value}", self._make_forwarder(event_type.value))
            ctx.on(f"event/{event_type.value}", self._make_forwarder(event_type.value))

    def _make_forwarder(self, wire_type: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """构造单事件转发闭包（wire_type = 协议层事件名字符串）。"""

        async def forward(payload: Any) -> None:
            await self.observer_event_bus.emit_core_event(
                Message(type=wire_type, payload=_payload_as_dict(payload))
            )

        return forward

    async def start(self) -> None:
        """Start the Observer event bus.

        WebSocket 命令路由已在 ``init`` 经 ``_EngineDispatchAdapter`` 绑定
        到宿主 ctx（ctx.serial 分发）。
        """

        await self.observer_event_bus.start()

    async def stop(self) -> None:
        if self._observer_event_bus is not None:
            await self._observer_event_bus.stop()

    async def send_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        await self.connection_manager.send_to(session_id, message)

    async def broadcast(self, message: dict[str, Any]) -> int:
        return await self.connection_manager.broadcast(message)


def _create_observer_app(
    config: ObserverServerConfig,
    server: ObserverServer,
    connection_manager: ConnectionManager,
) -> FastAPI:
    app = FastAPI(
        title="ghrah-subject-observer",
        description="ghrah-subject Observer WebSocket server",
        version="0.1.0",
        # 业务/元信息入口一律在 /api/v1 命名空间下；无前缀 GET 留给 Observer SPA。
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )

    async def websocket_endpoint(websocket: WebSocket) -> None:
        await server.handle_connection(websocket)

    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "active_sessions": connection_manager.connection_count,
        }

    async def meta() -> dict[str, str]:
        return {
            "name": "ghrah-subject-observer",
            "version": "0.1.0",
            "description": "ghrah-subject Observer WebSocket server",
        }

    app.websocket(config.ws_path)(websocket_endpoint)
    app.get("/api/v1/health")(health)
    app.get("/api/v1/meta")(meta)
    # 过渡别名：ghrah-toolchain 的 dev/生命周期仍探测无前缀 /health。
    # 待工具链切到 /api/v1/health 后删除（基线切换阶段，见 T0 任务书）。
    app.get("/health", deprecated=True, include_in_schema=False)(health)
    return app


def _payload_as_dict(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        return dict(payload)
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        result = model_dump()
        if isinstance(result, Mapping):
            return dict(result)
    return {"value": payload}
