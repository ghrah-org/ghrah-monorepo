"""Kernel 唯一持有子进程创建、信号与等待。"""

import asyncio
import secrets
import sys
from multiprocessing import Pipe

from ..client import RpcClient
from ..endpoint import AsyncEndpoint
from ..wire import MessageConnection
from .tables import Domain, KernelFault

# 受管服务入口模块：当前由机制回归夹具提供，真实受管入口接入后替换。
SERVICE_ENTRY_MODULE = "_mechanism.service_main"


class Processes:
    def __init__(self, kernel):
        self.kernel = kernel
        self.domains: dict[str, Domain] = {}
        self.generations: dict[str, int] = {}
        self.spawn_keys: dict[tuple[str, str], Domain] = {}
        self._spawn_lock = asyncio.Lock()

    async def spawn(
        self, service_key: str, kind: str, spawn_key: str, fixture: str = "normal"
    ) -> dict:
        async with self._spawn_lock:
            if self.kernel._closing or self.kernel.shutdown.active:
                raise KernelFault("PROVIDER_UNAVAILABLE", "内核正在结束")
            return await self._spawn(service_key, kind, spawn_key, fixture)

    async def _spawn(self, service_key: str, kind: str, spawn_key: str, fixture: str) -> dict:
        if kind not in ("namespace", "ability", "agent"):
            raise KernelFault("INVALID_ARGUMENT", "只支持声明的实验服务种类")
        if fixture not in ("normal", "exit", "spin", "ignore_term") or (
            kind != "ability" and fixture != "normal"
        ):
            raise KernelFault("INVALID_ARGUMENT", "故障 fixture 仅用于声明的 Ability 替身")
        if not all(isinstance(value, str) and value for value in (service_key, spawn_key)):
            raise KernelFault("INVALID_ARGUMENT", "服务身份与启动键必须非空")
        old = self.spawn_keys.get((service_key, spawn_key))
        if old is not None:
            if old.kind != kind or old.fixture != fixture:
                raise KernelFault("REGISTRATION_CONFLICT", "同启动键的内容不同")
            if old.reaped:
                raise KernelFault("DOMAIN_RETIRED", "旧启动键不能复活实例")
            return old.snapshot()
        provider_parent, provider_child = Pipe(duplex=True)
        caller_parent, caller_child = Pipe(duplex=True)
        credential, enrollment = secrets.token_hex(16), secrets.token_hex(16)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                SERVICE_ENTRY_MODULE,
                "--kind",
                kind,
                "--provider-fd",
                str(provider_child.fileno()),
                "--caller-fd",
                str(caller_child.fileno()),
                "--credential",
                credential,
                "--enrollment",
                enrollment,
                "--fixture",
                fixture,
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
        self.kernel.attach_caller(caller_parent, credential, "service", domain.domain_id)
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
