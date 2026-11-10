# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""固定顺序引导；不实现重启策略，不成为受管服务进程的父进程。"""

import asyncio
import json
import os
import secrets
import sys
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from ghrah_kernel.client import RpcClient
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.subprocesses import start_process
from ghrah_kernel.wire import MessageConnection, message_pipe

from .contracts import (
    BOOTSTRAP,
    DEFAULT_SERVICES,
    OBSERVER,
    SERVICE_SCHEMES,
    mechanism_catalog,
    mechanism_identities,
)

# 子进程入口从 tests 目录解析：内核宿主与其派生的受管服务共享这一环境。
ENTRY_ENV = {
    **os.environ,
    "PYTHONPATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1]), os.environ.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep),
}


class ExperimentHost:
    def __init__(self, control, observer, process, peer):
        self.control, self.observer, self.process = control, observer, process
        self.services: dict[str, dict] = {}
        self.final_report: dict = {}
        self.peer = peer

    async def start_service(self, service_key, kind, *, spawn_key=None):
        domain = await self.control.spawn(
            service_key, kind, spawn_key=spawn_key or secrets.token_hex(8)
        )
        registered = await self.control.register(
            domain["domain_id"], SERVICE_SCHEMES[kind], "initial"
        )
        self.services[service_key] = registered
        return registered

    async def mount(self, path, service, scheme):
        ns_handle = self.services["names"]["scheme_handles"]["ns"]
        return await self.control.call(
            ns_handle,
            "bind",
            {
                "path": path,
                "target": service["scheme_refs"][scheme],
            },
        )

    async def assemble(self, observer_token, observer_accepts=False):
        await self.start_service("names", "namespace")
        ability = await self.start_service("tools", "ability")
        await self.mount("/scheme/ability/main", ability, "ability")
        await self.mount("/scheme/view/main", ability, "view")
        agent = await self.start_service("worker", "agent")
        await self.mount("/scheme/agent/demo", agent, "agent")
        await self.observer.hello(observer_token, accept_transfers=observer_accepts)


@asynccontextmanager
async def open_service_chain(*, observer_accepts=False, peer_accepts=False, assemble=True):
    control, kernel_control = message_pipe()
    observer, kernel_observer = message_pipe()
    peer, kernel_peer = message_pipe()
    control_token, observer_token = secrets.token_hex(16), secrets.token_hex(16)
    peer_token = secrets.token_hex(16)
    process = None
    host = None
    with tempfile.TemporaryDirectory(prefix="mechanism-chain-") as temporary:
        report_path = Path(temporary) / "kernel.json"
        config_path = Path(temporary) / "host.json"
        config_path.write_text(
            json.dumps(
                {
                    "identities": mechanism_identities().to_dict(),
                    "launches": [asdict(record) for record in mechanism_catalog().records],
                    "callers": {
                        name: {
                            "principal": principal,
                            "resolution_scope": "mechanism",
                            "diagnostics": True,
                        }
                        for name, principal in (
                            ("control", BOOTSTRAP),
                            ("observer", OBSERVER),
                            ("peer", OBSERVER),
                        )
                    },
                }
            )
        )
        try:
            process = await start_process(
                sys.executable,
                "-m",
                "ghrah_kernel",
                "_kernel",
                "--control-fd",
                str(kernel_control.fileno()),
                "--control-token",
                control_token,
                "--observer-fd",
                str(kernel_observer.fileno()),
                "--observer-token",
                observer_token,
                "--peer-fd",
                str(kernel_peer.fileno()),
                "--peer-token",
                peer_token,
                "--report",
                str(report_path),
                "--config",
                str(config_path),
                connections=(kernel_control, kernel_observer, kernel_peer),
                env=ENTRY_ENV,
            )
            kernel_control.close()
            kernel_observer.close()
            kernel_peer.close()
            host = ExperimentHost(
                KernelClient(RpcClient(AsyncEndpoint(MessageConnection(control)))),
                KernelClient(RpcClient(AsyncEndpoint(MessageConnection(observer)))),
                process,
                KernelClient(RpcClient(AsyncEndpoint(MessageConnection(peer)))),
            )
            await host.control.hello(control_token, requires_ns_root=False, accept_transfers=True)
            host.observer_token, host.peer_token = observer_token, peer_token
            if assemble:
                await host.assemble(observer_token, observer_accepts)
                await host.peer.hello(peer_token, accept_transfers=peer_accepts)
            yield host
        finally:
            kernel_control.close()
            kernel_observer.close()
            kernel_peer.close()
            try:
                if host is not None:
                    try:
                        if host.control.shutdown_report is None and process.returncode is None:
                            await host.control.shutdown()
                    finally:
                        await host.observer.aclose()
                        await host.peer.aclose()
                        await host.control.aclose()
                else:
                    control.close()
                    observer.close()
                    peer.close()
            finally:
                if process is not None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=4)
                    except TimeoutError:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                        raise RuntimeError("Kernel 未按预算回收服务，实验失败")
                    if host is not None and report_path.exists():
                        host.final_report = json.loads(report_path.read_text())
                    if process.returncode != 0:
                        raise RuntimeError(f"Kernel 宿主异常退出: {process.returncode}")


@asynccontextmanager
async def open_supervised_chain(services=None, *, monitor=True):
    from ghrah_kernel.supervisor import Supervisor

    async with open_service_chain(assemble=False) as host:
        host.supervisor = Supervisor(
            host.control, services if services is not None else DEFAULT_SERVICES, SERVICE_SCHEMES
        )
        host.services = host.supervisor.registrations
        try:
            await host.supervisor.start(monitor=monitor)
            await host.observer.hello(host.observer_token)
            await host.peer.hello(host.peer_token)
            yield host
        finally:
            await host.supervisor.aclose()
