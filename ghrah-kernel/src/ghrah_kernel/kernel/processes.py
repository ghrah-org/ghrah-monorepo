# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Kernel 唯一持有子进程创建、信号与等待。"""

import asyncio
import os
import secrets
from multiprocessing import Pipe

from ..client import RpcClient
from ..endpoint import AsyncEndpoint
from ..wire import MessageConnection
from .tables import Domain, KernelFault


class Processes:
    def __init__(self, kernel):
        self.kernel = kernel
        self.domains: dict[str, Domain] = {}
        self.generations: dict[str, int] = {}
        self.spawn_keys: dict[tuple[str, str], Domain] = {}
        self._spawn_lock = asyncio.Lock()

    async def spawn(
        self,
        service_key: str,
        kind: str,
        spawn_key: str,
        fixture: str = "normal",
        entry_key: str | None = None,
    ) -> dict:
        async with self._spawn_lock:
            if self.kernel._closing or self.kernel.shutdown.active:
                raise KernelFault("PROVIDER_UNAVAILABLE", "内核正在结束")
            return await self._spawn(service_key, kind, spawn_key, fixture, entry_key)

    async def _spawn(
        self, service_key: str, kind: str, spawn_key: str, fixture: str, entry_key: str | None
    ) -> dict:
        if not all(
            isinstance(value, str) and value for value in (service_key, kind, spawn_key, fixture)
        ) or (entry_key is not None and (not isinstance(entry_key, str) or not entry_key)):
            raise KernelFault("INVALID_ARGUMENT", "服务身份、种类与启动键必须非空")
        selected = entry_key if entry_key is not None else kind
        try:
            record = self.kernel.launch_catalog.select(selected, kind, fixture)
        except ValueError as exc:
            raise KernelFault("ACCESS_DENIED", str(exc)) from exc
        old = self.spawn_keys.get((service_key, spawn_key))
        if old is not None:
            if old.kind != kind or old.fixture != fixture or old.entry_key != selected:
                raise KernelFault("REGISTRATION_CONFLICT", "同启动键的内容不同")
            if old.reaped:
                raise KernelFault("DOMAIN_RETIRED", "旧启动键不能复活实例")
            return old.snapshot()
        provider_parent, provider_child = Pipe(duplex=True)
        caller_parent, caller_child = Pipe(duplex=True)
        credential, enrollment = secrets.token_hex(16), secrets.token_hex(16)
        try:
            process = await asyncio.create_subprocess_exec(
                record.executable,
                "-m",
                record.module,
                *record.arguments,
                "--provider-fd",
                str(provider_child.fileno()),
                "--caller-fd",
                str(caller_child.fileno()),
                "--credential",
                credential,
                "--enrollment",
                enrollment,
                "--config-json",
                record.configuration,
                env={**os.environ, **dict(record.environment)},
                pass_fds=(provider_child.fileno(), caller_child.fileno()),
                start_new_session=True,
            )
        except BaseException:
            provider_parent.close()
            caller_parent.close()
            raise
        finally:
            provider_child.close()
            caller_child.close()
        generation = self.generations.get(service_key, 0) + 1
        self.generations[service_key] = generation
        domain = Domain(
            self.kernel.new_id("domain"),
            service_key,
            generation,
            kind,
            process,
            RpcClient(AsyncEndpoint(MessageConnection(provider_parent))),
            fixture=fixture,
            spawn_key=spawn_key,
            entry_key=selected,
        )
        self.domains[domain.domain_id] = domain
        domain.peer.event_handler = lambda message: self.kernel.events.receive(
            domain.domain_id, message
        )

        def disconnected():
            if not domain.expected_stop and domain.state not in ("stopped", "failed"):
                domain.state = "failed"
                self.kernel.notify_supervision(
                    {"type": "endpoint_failed", "domain_id": domain.domain_id}
                )
            self.kernel.events.domain_closed(domain.domain_id)
            self.kernel.streams.domain_closed(domain.domain_id)

        domain.peer.failure_handler = disconnected
        self.spawn_keys[(service_key, spawn_key)] = domain
        self.kernel.enrollments[domain.domain_id] = enrollment
        self.kernel.attach_caller(
            caller_parent,
            credential,
            "service",
            domain.domain_id,
            principal=record.binding.principal,
            resolution_scope=record.binding.resolution_scope,
            diagnostics=record.binding.diagnostics,
        )
        domain.monitor = asyncio.create_task(self._monitor(domain))
        return self.status(domain.domain_id)

    async def _monitor(self, domain: Domain) -> None:
        domain.exit_code = await domain.process.wait()
        domain.reaped = True
        self.kernel.events.domain_closed(domain.domain_id)
        self.kernel.streams.domain_closed(domain.domain_id)
        if domain.state not in ("stopped", "failed"):
            domain.state = "stopped" if domain.expected_stop else "failed"
        self.kernel.notify_supervision(
            {"type": "process_exited", "domain": self.status(domain.domain_id)}
        )
        await domain.peer.aclose()

    def status(self, reference):
        domain = self.get(reference)
        inflight = sum(
            not item.task.done()
            and self.kernel.schemes[self.kernel.references.nodes[item.source].basis[0]].domain_id
            == domain.domain_id
            for item in self.kernel.invocations.values()
        )
        return {**domain.snapshot(), "inflight": inflight}

    async def wait(self, reference, timeout_ms=200):
        domain = self.get(reference)
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 1000:
            raise KernelFault("INVALID_ARGUMENT", "进程等待预算范围为 0..1000 ms")
        if not domain.reaped and timeout_ms:
            try:
                await asyncio.wait_for(asyncio.shield(domain.monitor), timeout_ms / 1000)
            except TimeoutError:
                pass
        return self.status(reference)

    async def drain(self, reference, timeout_ms=50):
        domain = self.get(reference)
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 500:
            raise KernelFault("INVALID_ARGUMENT", "排空预算范围为 0..500 ms")
        if domain.state == "ready":
            domain.state = "draining"
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while self.status(reference)["inflight"]:
            self.kernel.changed.clear()
            if deadline <= asyncio.get_running_loop().time():
                break
            try:
                await asyncio.wait_for(
                    self.kernel.changed.wait(), deadline - asyncio.get_running_loop().time()
                )
            except TimeoutError:
                break
        result = self.status(reference)
        return {**result, "drained": result["inflight"] == 0}

    def get(self, reference: str) -> Domain:
        try:
            return self.domains[reference]
        except KeyError, TypeError:
            raise KernelFault("NOT_FOUND", "不存在该域实例") from None

    async def kill(self, reference: str, *, force=False) -> dict:
        domain = self.get(reference)
        if not domain.reaped:
            domain.expected_stop = True
            if domain.state not in ("stopped", "failed"):
                domain.state = "stopping"
            # 先终结本域流，再发终止信号：进程一退出管道就 EOF，若 terminal 还没落，
            # 在途 read 会偶发返回 eof=True，与 monitor 里的 domain_closed 抢跑。
            # stop() 幂等，monitor 之后重复调用无副作用。
            self.kernel.streams.domain_closed(domain.domain_id)
            try:
                if force:
                    domain.forced = True
                    domain.process.kill()
                else:
                    domain.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(domain.monitor), timeout=0.2)
            except TimeoutError:
                if domain.process.returncode is None:
                    domain.forced = True
                    domain.process.kill()
                await domain.monitor
        return self.status(reference)

    async def aclose(self) -> None:
        async with self._spawn_lock:
            for domain in list(self.domains.values()):
                await self.kill(domain.domain_id)
