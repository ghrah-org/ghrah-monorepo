# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""自建 Observer app，复用传输组件，以内核事实替换旧装配引擎。"""

from typing import Any

from fastapi import FastAPI, WebSocket

from ghrah.subject.server.config import ObserverServerConfig
from ghrah.subject.server.connection_manager import ConnectionManager
from ghrah.subject.server.event_bus import EventBus
from ghrah.subject.server.router import ObserverRouter
from ghrah.subject.server.server import ObserverServer

from .health import health_response
from .state import SystemPhase


class KernelFacts:
    """Observer 仅获得显式开放的事实查询，不转发任意内核管理调用。"""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def dispatch_observer_command(
        self,
        command: str,
        payload: dict,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """健康查询读取内核事实；未接入的新命令显式拒绝。"""
        if self.runtime.state.phase != SystemPhase.READY:
            return {"success": False, "error": "System is not ready"}
        if command == "health_check":
            kernel = self.runtime.bootstrap.observer
            info = await kernel.call(kernel.session["sys_handle"], "info", {})
            return {"success": True, "data": {**self.runtime.snapshot(), "kernel": info}}
        return {"success": False, "error": f"Unsupported host command: {command}"}


def create_observer_app(
    runtime: Any,
    config: ObserverServerConfig,
    *,
    lifespan: Any = None,
) -> FastAPI:
    """组合 WS、健康探针与元信息；Booting 期间禁止 WS 业务接入。"""
    manager = ConnectionManager()
    bus = EventBus(manager, event_store_capacity=config.event_replay_capacity)
    router = ObserverRouter(manager, bus, engine=KernelFacts(runtime))
    observer = ObserverServer(config, manager, router, bus)
    app = FastAPI(lifespan=lifespan)
    app.state.event_bus = bus

    @app.get("/health")
    async def health():
        return health_response(runtime.snapshot())

    @app.get("/")
    async def metadata():
        return {"name": "ghrah-subject-host", **runtime.snapshot()}

    @app.websocket(config.ws_path)
    async def websocket(websocket: WebSocket):
        if runtime.state.phase != SystemPhase.READY:
            await websocket.close(code=1013)
            return
        await observer.handle_connection(websocket)

    return app
