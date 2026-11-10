# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内核预检、连接继承与一次性进程生命周期；内核失效后不自动重建。"""

import asyncio
import json
import secrets
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.integration.assembly import host_configuration
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.subprocesses import start_process
from ghrah_kernel.wire import MessageConnection, message_pipe


class HostBootError(RuntimeError):
    """宿主无法建立或维持本轮内核。"""


@dataclass
class Bootstrap:
    """三个预建连接与唯一内核进程；临时配置及报告保留至关闭完成。"""

    control: KernelClient
    observer: KernelClient
    peer: KernelClient
    process: asyncio.subprocess.Process
    tokens: tuple[str, ...]
    temporary: tempfile.TemporaryDirectory
    final_report: dict | None = None
    _close_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    async def aclose(self) -> None:
        """请求内核 drain，关闭消息工作者并有界等待；必要时终止并回收进程。"""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        errors = []
        try:
            if self.process.returncode is None and self.control.shutdown_report is None:
                try:
                    async with asyncio.timeout(6):
                        await self.control.shutdown()
                except Exception as exc:
                    errors.append(exc)
            results = await asyncio.gather(
                *(client.aclose() for client in (self.control, self.observer, self.peer)),
                return_exceptions=True,
            )
            errors.extend(item for item in results if isinstance(item, BaseException))
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
                errors.append(HostBootError("内核未在停止预算内退出"))
            report = Path(self.temporary.name) / "kernel.json"
            if report.exists():
                self.final_report = json.loads(report.read_text("utf-8"))
            if self.process.returncode != 0:
                errors.append(HostBootError(f"内核异常退出：{self.process.returncode}"))
            if self.final_report and self.final_report.get("errors"):
                errors.append(HostBootError(f"内核报告错误：{self.final_report['errors']}"))
        finally:
            self.temporary.cleanup()
        if errors:
            raise HostBootError("；".join(str(exc) for exc in errors))


async def start_kernel(configuration: dict) -> Bootstrap:
    """预检后创建三对 socket 连接、启动内核并取得 bootstrap 会话。"""
    try:
        host_configuration(configuration)
    except (ValueError, KeyError, TypeError) as exc:
        raise HostBootError(f"宿主配置预检失败：{exc}") from exc
    pairs, clients, tokens = [], [], []
    temporary = tempfile.TemporaryDirectory(prefix="ghrah-host-")
    process = None
    host = None
    try:
        for _ in range(3):
            parent, child = message_pipe()
            pairs.append((parent, child))
            tokens.append(secrets.token_hex(16))
            clients.append(KernelClient(RpcClient(AsyncEndpoint(MessageConnection(parent)))))
        path = Path(temporary.name)
        (path / "host.json").write_text(json.dumps(configuration, ensure_ascii=False), "utf-8")
        arguments = []
        for name, (_, child), token in zip(("control", "observer", "peer"), pairs, tokens):
            arguments.extend((f"--{name}-fd", str(child.fileno()), f"--{name}-token", token))
        process = await start_process(
            sys.executable,
            "-m",
            "ghrah_kernel",
            "_kernel",
            *arguments,
            "--config",
            str(path / "host.json"),
            "--report",
            str(path / "kernel.json"),
            connections=tuple(child for _, child in pairs),
            new_session=True,
        )
        for _, child in pairs:
            child.close()
        host = Bootstrap(*clients, process, tuple(tokens), temporary)
        async with asyncio.timeout(10):
            await host.control.hello(tokens[0], requires_ns_root=False)
        return host
    except BaseException as exc:
        if host is not None:
            try:
                await host.aclose()
            except Exception:
                pass  # 保留最初的启动错误，所有回收仍已尝试。
        else:
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            temporary.cleanup()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HostBootError(f"内核启动失败：{exc}") from exc
    finally:
        for _, child in pairs:
            child.close()
