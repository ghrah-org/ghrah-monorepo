# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""FastAPI 应用入口（Ouroboros 形态薄 facade）。

Observer FastAPI app 由 ``WebSocketObserverEndpointUnit`` 在装配期间创建
并持有（Unit owns app）。``create_app(config, *, ctx)`` 仅
作为经 ctx 取回该 app 的薄 facade。

生命周期：先 ``assemble_subject(ctx, config, profile="full")``（Unit
初始化并构造 app），再 ``create_app(..., ctx=ctx)`` 取回 app；Context
退出时 dispose 全部 fiber。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI

from ghrah.subject.server.config import ObserverServerConfig

if TYPE_CHECKING:
    from ouroboros import Context  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


def create_app(
    config: ObserverServerConfig | None = None,
    *,
    ctx: Context,
) -> FastAPI:
    """返回 ctx 中 observer unit 持有的 FastAPI app（Unit owns app）。

    Args:
        config: 保留用于签名兼容；Unit 的 ws_path/event_replay_capacity
            已在装配期由该 Unit 自身的 ObserverServerConfig 确定。
        ctx: 已装配的 Ouroboros Context（须含 websocket_observer_endpoint）。

    Returns:
        WebSocketObserverEndpointUnit 持有的 FastAPI 实例。

    Raises:
        RuntimeError: ctx 未装配 websocket_observer_endpoint unit。
    """
    del config
    from ghrah.subject.units.websocket_observer_endpoint import (
        WebSocketObserverEndpointUnit,
    )

    unit = ctx.get("observer_endpoint", strict=False)
    if not isinstance(unit, WebSocketObserverEndpointUnit):
        raise RuntimeError(
            "create_app requires an assembled Context with the "
            "websocket_observer_endpoint unit "
            "(assemble_subject(profile='full'))."
        )
    return unit.app


async def serve() -> None:
    """启动 Subject Observer 服务器（Ouroboros 装配 + uvicorn）。"""
    import asyncio
    import contextlib
    import os
    import signal

    import uvicorn
    from ouroboros import Context

    from ghrah.subject.config import SubjectConfig
    from ghrah.subject.runtime.assembly import assemble_subject

    observer_config = ObserverServerConfig.from_env()
    subject_config = SubjectConfig.from_env()

    # uvicorn 0.5x 的 Server.capture_signals 会在优雅停机后恢复原信号处理器，并
    # 重放捕获到的 SIGTERM/SIGINT（"try to trigger the expected behaviour now"）。
    # 进程启动时的原处理器是默认处置，重放会直接终止进程，于是下面
    # ``async with Context()`` 的 __aexit__ 永不执行——unit.stop →
    # Supervisor.shutdown → 能力启动注册的 release 全部被跳过，注册占位残留，
    # 下次启动 reconcile 重 spawn 同一稳定 agent_id 时抛 conflict。
    # 先把信号登记成"只记录不终止"的处理器：uvicorn 会把它当作原处理器在重放前
    # 恢复，重放落回这里被吞下；等 Context 退出、各 unit 收尾完成后，再按原语义
    # 补发信号，保留调用方期望的退出码约定。
    deferred: list[int] = []

    def _defer_termination(signum: int, _frame: object) -> None:
        deferred.append(signum)

    previous_handlers: dict[int, object] = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_handlers[sig] = signal.signal(sig, _defer_termination)
        except ValueError, OSError:
            logger.warning("信号处理器安装失败，跳过：%s", sig, exc_info=True)
    try:
        async with Context() as ctx:
            await assemble_subject(ctx, subject_config, profile="full")
            try:
                app = create_app(observer_config, ctx=ctx)
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host=observer_config.host,
                        port=observer_config.port,
                        log_level=observer_config.log_level.lower(),
                        ws_ping_interval=observer_config.ping_interval,
                        ws_ping_timeout=observer_config.ping_timeout,
                    )
                )
                if deferred:
                    # 装配期间已收到停止信号：不再进入服务主循环，直接走收尾。
                    server.should_exit = True
                await server.serve()
            except asyncio.CancelledError:
                logger.info("observer serve cancelled, disposing via Context exit")
                raise
    finally:
        for sig, handler in previous_handlers.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handler)
    if deferred:
        logger.info("收到停止信号 %s：Context 收尾完成，按原语义退出", deferred)
    for sig in deferred:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, signal.SIG_DFL)
        os.kill(os.getpid(), sig)


def main() -> None:
    """启动 Observer 服务器。"""
    import asyncio

    from ghrah.subject.config import SubjectConfig
    from ghrah.subject.logging_setup import configure_logging

    config = SubjectConfig.from_env()
    log_path = configure_logging(
        level=config.log_level, levels=config.log_levels, log_file=config.log_file
    )
    logger.info("ghrah-subject observer server starting (ouroboros, profile=full)...")
    if log_path is not None:
        logger.info("  log_file: %s", log_path)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
