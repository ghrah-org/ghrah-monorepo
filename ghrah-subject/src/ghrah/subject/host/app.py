# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""宿主 Init：读取固定代目录，编排自检、接入门禁与停止链路。"""

import asyncio
import contextlib
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from ghrah.subject.logging_setup import configure_logging
from ghrah.subject.server.app import _stop_signals
from ghrah.subject.server.config import ObserverServerConfig

from .catalog import load_catalog, map_host_configuration, map_supervisor
from .kernel_process import Bootstrap, HostBootError, start_kernel
from .observer import create_observer_app
from .state import SystemPhase, SystemState
from .supervisor import Supervisor

logger = logging.getLogger(__name__)


class _HostServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # Init 自己安装并延迟信号；避免 uvicorn 在 startup 期间提前返回，跳过收尾。
        yield


class HostRuntime:
    """唯一 Init 写者持有系统阶段；任何探针与命令都只读它的派生视图。"""

    def __init__(self, generation_root: Path) -> None:
        self.generation_root = generation_root
        self._writer = object()
        self.state = SystemState(self._writer)
        self.bootstrap: Bootstrap | None = None
        self.supervisor: Supervisor | None = None
        self.error: str | None = None
        self.failed = asyncio.Event()
        self._stopping = False

    def snapshot(self) -> dict:
        """从唯一内核和监督者派生当前事实，健康端点不触发状态写入。"""
        return {
            **self.state.snapshot(),
            "boot_id": self.bootstrap.control.session.get("boot_id") if self.bootstrap else None,
            "kernel_pid": self.bootstrap.process.pid if self.bootstrap else None,
            "services": self.supervisor.view() if self.supervisor else [],
        }

    def fail(self, error: BaseException) -> None:
        """记录首个不可恢复错误并关闭就绪门；内核进程不会被重建。"""
        self.error = self.error or str(error)
        if self.state.phase not in (SystemPhase.FAILED, SystemPhase.STOPPED):
            self.state.transition(SystemPhase.FAILED, writer=self._writer)
        self.failed.set()

    async def boot(self) -> None:
        """按固定顺序预检、启动内核和服务、自检，然后发布 Ready。"""
        try:
            catalog = load_catalog(self.generation_root)
            configuration = map_host_configuration(catalog, os.environ)
            specs, schemes, _ = map_supervisor(catalog, os.environ)
            self.bootstrap = await start_kernel(configuration)
            self.supervisor = Supervisor(self.bootstrap.control, specs, schemes)
            await self.supervisor.start(monitor=True)
            control = self.bootstrap.control
            info = await control.call(control.session["sys_handle"], "info", {})
            if (info["boot_id"], info["kernel_pid"]) != (
                control.session["boot_id"],
                self.bootstrap.process.pid,
            ):
                raise HostBootError("内核启动身份与自检不一致")
            await self.bootstrap.observer.hello(self.bootstrap.tokens[1])
            await self.bootstrap.peer.hello(self.bootstrap.tokens[2])
            root = await self.bootstrap.observer.open("/")
            try:
                await self.bootstrap.observer.call(root, "inspect", {})
            finally:
                await self.bootstrap.observer.close(root)
            if any(item["phase"] != "Running" for item in self.supervisor.view()):
                raise HostBootError("自检时必需服务未全部 Running")
            self.state.transition(SystemPhase.READY, writer=self._writer)
            while not self._stopping:
                if self.bootstrap.process.returncode is not None:
                    raise HostBootError(f"内核意外退出：{self.bootstrap.process.returncode}")
                if self.supervisor.failure:
                    raise HostBootError(self.supervisor.failure)
                if any(item["phase"] == "Failed" for item in self.supervisor.view()):
                    raise HostBootError("必需服务无法恢复")
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("系统宿主启动或运行失败")
            self.fail(exc)

    async def close(self) -> None:
        """入口已停止接收连接后，逆序关闭服务并有界回收内核。"""
        self._stopping = True
        if self.state.phase not in (SystemPhase.FAILED, SystemPhase.STOPPED):
            self.state.transition(SystemPhase.STOPPING, writer=self._writer)
        try:
            if self.supervisor is not None and self.bootstrap.process.returncode is None:
                try:
                    async with asyncio.timeout(15):
                        report = await self.supervisor.shutdown(mode="drain")
                    if not report["clean"]:
                        raise HostBootError(f"服务未正常收束：{report}")
                except Exception as exc:
                    self.fail(exc)
            if self.bootstrap is not None:
                try:
                    await self.bootstrap.aclose()
                except Exception as exc:
                    self.fail(exc)
            if self.state.phase == SystemPhase.STOPPING:
                self.state.transition(SystemPhase.STOPPED, writer=self._writer)
        finally:
            report_path = os.environ.get("GHRAH_BOOT_REPORT_PATH")
            if report_path:
                report = {
                    **self.snapshot(),
                    "error": self.error,
                    "kernel": self.bootstrap.final_report if self.bootstrap else None,
                }
                Path(report_path).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
                )


def create_app(
    generation_root: Path | None = None,
    config: ObserverServerConfig | None = None,
) -> FastAPI:
    """先开放 Booting 探针，后台引导完成自检后才开放业务接入。"""
    runtime = HostRuntime(generation_root or Path.cwd())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.event_bus.start()
        task = asyncio.create_task(runtime.boot())
        try:
            yield
        finally:
            runtime._stopping = True
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await runtime.close()
            await app.state.event_bus.stop()

    app = create_observer_app(runtime, config or ObserverServerConfig.from_env(), lifespan=lifespan)
    app.state.runtime = runtime
    return app


async def serve() -> int:
    """延迟 uvicorn 重放的停止信号，保证 Windows 与 POSIX 都先完成收尾。"""
    config = ObserverServerConfig.from_env()
    app = create_app(config=config)
    runtime = app.state.runtime
    server = _HostServer(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            log_level=config.log_level.lower(),
            ws_ping_interval=config.ping_interval,
            ws_ping_timeout=config.ping_timeout,
        )
    )
    previous = {}
    deferred = []

    def stop(signum: int, _frame: object) -> None:
        deferred.append(signum)

    for sig in _stop_signals():
        try:
            previous[sig] = signal.signal(sig, stop)
        except ValueError, OSError:
            logger.debug("无法安装停止信号 %s", sig, exc_info=True)
    serving = asyncio.create_task(server.serve())
    failed = asyncio.create_task(runtime.failed.wait())

    async def wait_stop_file() -> None:
        # 工具链为无控制台的 Windows 后台进程注入私有停止文件路径。
        # 此路径来自受信进程环境，不从 HTTP/WS 请求接受路径。
        stop_path = os.environ.get("GHRAH_HOST_STOP_FILE")
        while True:
            requested = deferred or (stop_path and Path(stop_path).exists())
            if requested and server.started:
                server.should_exit = True
                return
            await asyncio.sleep(0.05)

    stop_request = asyncio.create_task(wait_stop_file())
    try:
        done, _ = await asyncio.wait((serving, failed), return_when=asyncio.FIRST_COMPLETED)
        if failed in done:
            # uvicorn 若在 startup 返回前看到 should_exit，会跳过 lifespan shutdown。
            # 先等监听完成或启动任务退出，再请求停止，确保 Init 始终能执行回收。
            while not server.started and not serving.done():
                await asyncio.sleep(0.01)
            if not serving.done():
                server.should_exit = True
        await serving
    finally:
        failed.cancel()
        stop_request.cancel()
        await asyncio.gather(stop_request, return_exceptions=True)
        with contextlib.suppress(asyncio.CancelledError):
            await failed
        if not serving.done():
            server.should_exit = True
            await serving
        for sig, handler in previous.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handler)
    return 1 if runtime.error or (not server.started and not deferred) else 0


def main() -> None:
    """以 console script 启动宿主，异常或收束失败均返回非零。"""
    configure_logging(
        level=os.environ.get("GHRAH_SUBJECT_LOG_LEVEL", "INFO"),
        levels=os.environ.get("GHRAH_SUBJECT_LOG_LEVELS", ""),
        log_file=os.environ.get("GHRAH_SUBJECT_LOG_FILE", ""),
    )
    raise SystemExit(asyncio.run(serve()))


if __name__ == "__main__":
    main()
