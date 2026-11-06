"""期望状态与重启策略；所有进程手段均通过 KernelClient 请求。"""

import asyncio
import secrets
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field

from .client import RemoteError


@dataclass(frozen=True)
class ServiceBinding:
    """显式指定挂载位置、解析 Scope 与资源所属命名空间。"""

    path: str
    scheme: str
    scope: str
    namespace: str


@dataclass(frozen=True)
class ServiceSpec:
    service_key: str
    kind: str
    restart: str = "never"
    bindings: tuple[ServiceBinding | tuple[str, str], ...] = ()
    fixture: str = "normal"
    timeout_action: str = "keep"
    max_restarts: int = 3
    window_seconds: float = 5
    drain_ms: int = 50
    entry_key: str | None = None
    close_action: tuple[str, str] | None = None
    close_timeout_ms: int = 1500

    def __post_init__(self):
        if (
            not self.service_key
            or self.restart not in ("never", "on_failure", "always")
            or self.timeout_action not in ("keep", "kill")
            or not isinstance(self.fixture, str)
            or not self.fixture
            or (
                self.entry_key is not None
                and (not isinstance(self.entry_key, str) or not self.entry_key)
            )
            or type(self.max_restarts) is not int
            or not 0 <= self.max_restarts <= 3
            or not 1 <= self.window_seconds <= 60
            or type(self.drain_ms) is not int
            or not 0 <= self.drain_ms <= 500
            or type(self.close_timeout_ms) is not int
            or not 1 <= self.close_timeout_ms <= 2000
            or (
                self.close_action is not None
                and (len(self.close_action) != 2 or not all(self.close_action))
            )
        ):
            raise ValueError("无效的服务策略")
        if self.kind == "namespace" and self.restart != "never":
            raise ValueError("本轮命名根固定为 never，不提供既有会话命名根迁移")


@dataclass
class ServiceState:
    spec: ServiceSpec
    desired: str = "stopped"
    phase: str = "Stopped"
    kernel: dict | None = None
    registration: dict | None = None
    restart_count: int = 0
    next_retry: float | None = None
    error: str | None = None
    epoch: int = 0
    handled_exit: str | None = None
    endpoint_failure: str | None = None
    attempts: deque = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Supervisor:
    backoff = (0.1, 0.2, 0.4)

    def __init__(self, client, services, schemes):
        if not 1 <= len(services) <= 8 or len({x.service_key for x in services}) != len(services):
            raise ValueError("服务清单应包含 1..8 个不重名服务")
        if services[0].kind != "namespace" or sum(x.kind == "namespace" for x in services) != 1:
            raise ValueError("固定启动清单必须以唯一命名服务开头")
        for spec in services:
            declared = schemes.get(spec.kind)
            if declared is None:
                raise ValueError("服务清单引用了未声明的服务种类")
            names = {x["name"] for x in declared}
            for binding in spec.bindings:
                path, scheme = (
                    (binding.path, binding.scheme)
                    if isinstance(binding, ServiceBinding)
                    else binding
                )
                if not path.startswith("/") or scheme not in names:
                    raise ValueError("命名接线必须为绝对路径与本服务方案")
                if isinstance(binding, ServiceBinding) and (
                    not binding.scope or not binding.namespace
                ):
                    raise ValueError("真实命名接线必须声明 Scope 与命名空间")
            if spec.close_action is not None:
                scheme, action = spec.close_action
                if not any(x["name"] == scheme and action in x["actions"] for x in declared):
                    raise ValueError("关闭动作必须属于本服务声明的方案")
        self.client = client
        self.schemes = schemes
        self.states = {x.service_key: ServiceState(x) for x in services}
        self.namespace = services[0].service_key
        self.registrations: dict[str, dict] = {}
        self.history = deque(maxlen=128)
        self.changed = asyncio.Event()
        self.failure: str | None = None
        self._closing = False
        self._task = None
        self._reconcile_lock = asyncio.Lock()
        self._run_key = secrets.token_hex(8)
        self._serial = 0
        self.shutdown_report = None
        self._shutdown_task = None

    def record(self, state, event, **details):
        actual = state.kernel or {}
        self.history.append(
            {
                "time": asyncio.get_running_loop().time(),
                "service_key": state.spec.service_key,
                "event": event,
                "domain_id": actual.get("domain_id"),
                "generation": actual.get("domain_generation"),
                **details,
            }
        )
        self.changed.set()

    def view(self, name):
        state = self.states[name]
        return {
            "service_key": name,
            "desired": state.desired,
            "phase": state.phase,
            "restart_count": state.restart_count,
            "next_retry": state.next_retry,
            "error": state.error,
            "kernel": deepcopy(state.kernel),
        }

    async def wait_for(self, name, predicate, *, timeout=5):
        async with asyncio.timeout(timeout):
            while True:
                self.changed.clear()
                if self.failure:
                    raise RuntimeError(self.failure)
                value = self.view(name)
                if predicate(value):
                    return value
                await self.changed.wait()

    async def start(self, *, monitor=True):
        if self._closing:
            raise RuntimeError("监督者已关闭")
        for state in self.states.values():
            state.desired = "running"
            await self._launch(state)
        if monitor and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _release_roots(self, state):
        registration, state.registration = state.registration, None
        self.registrations.pop(state.spec.service_key, None)
        if registration:
            for reference in registration["scheme_handles"].values():
                try:
                    await self.client.close(reference)
                except RemoteError as exc:
                    if exc.code != "INVALID_HANDLE":
                        raise

    async def _kill_current(self, state, *, drain=False):
        try:
            if state.kernel and not state.kernel["reaped"]:
                reference = state.kernel["domain_id"]
                try:
                    if drain:
                        await self._close_application(state)
                        result = await self.client.drain(reference, timeout_ms=state.spec.drain_ms)
                        self.record(state, "drain", remaining_calls=result["inflight"])
                finally:
                    state.kernel = await self.client.kill(reference)
        finally:
            await self._release_roots(state)

    async def _close_application(self, state):
        if state.spec.close_action is None or state.registration is None:
            return
        scheme, action = state.spec.close_action
        reference = state.registration["scheme_handles"][scheme]
        invocation = await self.client.invoke(
            reference, action, {}, timeout_ms=state.spec.close_timeout_ms
        )
        try:
            result = await self.client.wait(
                invocation, timeout_ms=state.spec.close_timeout_ms + 100
            )
            if result.error is not None or result.delivery_status != "DELIVERED":
                raise RuntimeError(f"服务资源关闭失败: {result.error or result.delivery_status}")
            self.record(state, "resources_closed", receipt=result.value)
        finally:
            await self.client.close(invocation)

    async def _launch(self, state, *, retry=False):
        async with state.lock:
            if self._closing or state.desired != "running":
                return
            if state.phase == "Running" and state.kernel and not state.kernel["reaped"]:
                return
            if retry:
                if (
                    state.phase != "Backoff"
                    or state.next_retry is None
                    or state.next_retry > asyncio.get_running_loop().time()
                ):
                    return
                self._prune(state)
                state.attempts.append(asyncio.get_running_loop().time())
                state.restart_count += 1
            epoch = state.epoch
            self._serial += 1
            key = f"{self._run_key}:{self._serial}"
            try:
                await self._kill_current(state)
                state.phase, state.next_retry, state.error = "Starting", None, None
                state.endpoint_failure = None
                self.record(state, "starting", spawn_key=key)
                state.kernel = await self.client.spawn(
                    state.spec.service_key,
                    state.spec.kind,
                    spawn_key=key,
                    fixture=state.spec.fixture,
                    entry_key=state.spec.entry_key,
                )
                if state.desired != "running" or state.epoch != epoch or self._closing:
                    await self._kill_current(state)
                    state.phase = "Stopped"
                    return
                state.registration = await self.client.register(
                    state.kernel["domain_id"], self.schemes[state.spec.kind], "initial"
                )
                state.kernel = dict(state.registration)
                self.registrations[state.spec.service_key] = state.registration
                for binding in state.spec.bindings:
                    if state.desired != "running" or state.epoch != epoch or self._closing:
                        break
                    namespace = self.registrations[self.namespace]["scheme_handles"]["ns"]
                    path, scheme = (
                        (binding.path, binding.scheme)
                        if isinstance(binding, ServiceBinding)
                        else binding
                    )
                    target = dict(state.registration["scheme_refs"][scheme])
                    parameters = {"path": path, "target": target}
                    if isinstance(binding, ServiceBinding):
                        parameters["scope"] = binding.scope
                        target["namespace_id"] = binding.namespace
                    await self.client.call(
                        namespace,
                        "bind",
                        parameters,
                    )
                if state.desired != "running" or state.epoch != epoch or self._closing:
                    await self._kill_current(state)
                    state.phase = "Stopped"
                    return
                state.phase = "Running"
                self.record(state, "running")
            except Exception as exc:
                state.error = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
                # 启动请求可能已经创建实例；按本次操作键恢复精确对象后清理。
                snapshot = await self.client.process_snapshot()
                found = next((x for x in snapshot["domains"] if x["spawn_key"] == key), None)
                if found:
                    state.kernel = found
                    if (
                        found["schemes"]
                        and found["state"] == "ready"
                        and state.registration is None
                    ):
                        state.registration = await self.client.register(
                            found["domain_id"], self.schemes[state.spec.kind], "initial"
                        )
                await self._kill_current(state)
                if state.kernel:
                    state.handled_exit = state.kernel["domain_id"]
                self.record(state, "start_failed", error=state.error)
                if state.desired == "running" and state.epoch == epoch and not self._closing:
                    self._schedule(state, failed=True)
                else:
                    state.phase = "Stopped"
            finally:
                self.changed.set()

    def _prune(self, state):
        threshold = asyncio.get_running_loop().time() - state.spec.window_seconds
        while state.attempts and state.attempts[0] < threshold:
            state.attempts.popleft()

    def _schedule(self, state, *, failed):
        if state.spec.restart == "never" or (state.spec.restart == "on_failure" and not failed):
            state.phase, state.next_retry = "Failed" if state.error else "Exited", None
            self.record(state, "no_restart")
            return
        self._prune(state)
        if len(state.attempts) >= state.spec.max_restarts:
            state.phase, state.next_retry = "Failed", None
            state.error = "RESTART_BUDGET_EXCEEDED: 当前窗口的重启次数已用完"
            self.record(state, "budget_exhausted")
            return
        delay = self.backoff[min(len(state.attempts), len(self.backoff) - 1)]
        state.phase, state.next_retry = "Backoff", asyncio.get_running_loop().time() + delay
        self.record(state, "backoff", delay_ms=round(delay * 1000), next_retry=state.next_retry)

    async def _policy(self, request):
        state = next(
            (
                x
                for x in self.states.values()
                if x.kernel
                and (x.kernel["domain_id"], x.kernel["domain_generation"])
                == (request["domain_id"], request["generation"])
            ),
            None,
        )
        decision = "stale"
        if state:
            async with state.lock:
                if (
                    state.desired == "running"
                    and state.kernel["domain_id"] == request["domain_id"]
                    and state.kernel["domain_generation"] == request["generation"]
                    and not self._closing
                ):
                    decision = state.spec.timeout_action
                    if decision == "kill":
                        state.kernel = await self.client.kill(request["domain_id"])
                await self.client.ack_policy(request, decision)
                self.record(
                    state,
                    "policy",
                    invocation_id=request["invocation_id"],
                    decision=decision,
                    request_domain=request["domain_id"],
                    request_generation=request["generation"],
                )
        else:
            await self.client.ack_policy(request, decision)

    async def _recover_endpoint(self, state):
        reference, epoch = state.kernel["domain_id"], state.epoch
        state.phase = "Unavailable"
        self.record(state, "endpoint_unavailable")
        try:
            # EOF 可能先于 wait 返回；短暂等待由 Kernel 自证退出，避免抢杀正常退出者。
            state.kernel = await self.client.wait_domain(reference, timeout_ms=50)
            if (
                state.kernel["reaped"]
                or state.desired != "running"
                or state.epoch != epoch
                or self._closing
            ):
                return
            state.endpoint_failure = reference
            state.error = "ENDPOINT_FAILED: 端点已失效，回收仍存活的精确实例"
            self.record(state, "endpoint_recovery")
            state.kernel = await self.client.kill(reference)
            if not state.kernel["reaped"]:
                raise RuntimeError("Kernel 未确认故障实例回收")
        except Exception as exc:
            state.phase, state.error = "Failed", f"ENDPOINT_RECOVERY_FAILED: {exc}"
            self.record(state, "endpoint_recovery_failed", error=state.error)
            raise

    async def reconcile(self):
        async with self._reconcile_lock:
            snapshot = await self.client.process_snapshot()
            actual = {x["domain_id"]: x for x in snapshot["domains"]}
            for state in self.states.values():
                if state.kernel and state.kernel["domain_id"] in actual:
                    state.kernel = actual[state.kernel["domain_id"]]
            for request in snapshot["policy_requests"]:
                await self._policy(request)
            for state in self.states.values():
                async with state.lock:
                    if state.desired != "running" or self._closing:
                        continue
                    if (
                        state.kernel
                        and not state.kernel["reaped"]
                        and state.kernel["state"] == "failed"
                    ):
                        await self._recover_endpoint(state)
                    if state.desired != "running" or self._closing:
                        continue
                    if (
                        state.kernel
                        and state.kernel["reaped"]
                        and state.handled_exit != state.kernel["domain_id"]
                    ):
                        state.handled_exit = state.kernel["domain_id"]
                        await self._release_roots(state)
                        if state.desired == "running" and not self._closing:
                            self.record(
                                state, "exited", exit_code=state.kernel["exit_code"], reaped=True
                            )
                            self._schedule(
                                state,
                                failed=state.kernel["exit_code"] != 0
                                or state.endpoint_failure == state.kernel["domain_id"],
                            )
                if state.phase == "Backoff" and state.next_retry is not None:
                    await self._launch(state, retry=True)
            self.changed.set()

    async def _run(self):
        try:
            while not self._closing:
                await self.reconcile()
                retries = [x.next_retry for x in self.states.values() if x.next_retry is not None]
                delay = (
                    min(0.2, max(0.001, min(retries) - asyncio.get_running_loop().time()))
                    if retries
                    else 0.2
                )
                try:
                    await self.client.wait_supervision(timeout_ms=max(1, int(delay * 1000)))
                except RemoteError as exc:
                    if exc.code != "WAIT_TIMEOUT":
                        raise
                # 通知只触发重建；退出和超时的权威记录始终来自 Kernel 快照。
        except Exception as exc:
            self.failure = repr(exc)
            self.changed.set()

    async def stop_service(self, name):
        state = self.states[name]
        state.desired, state.next_retry, state.phase = "stopped", None, "Stopping"
        state.epoch += 1
        self.changed.set()
        async with state.lock:
            try:
                await self._kill_current(state, drain=True)
            except Exception as exc:
                state.phase, state.error = "Failed", f"STOP_FAILED: {exc}"
                self.record(state, "stop_failed", error=state.error)
                raise
            state.phase = "Stopped"
            self.record(state, "stopped")
        return self.view(name)

    async def start_service(self, name):
        if self._closing:
            raise RuntimeError("监督者已关闭")
        state = self.states[name]
        if state.desired == "running" and state.phase == "Running":
            return self.view(name)
        if name == self.namespace and state.kernel is not None:
            raise ValueError("本轮不提供既有会话的命名根迁移")
        state.desired, state.next_retry = "running", None
        state.epoch += 1
        state.attempts.clear()
        await self._launch(state)
        return self.view(name)

    async def restart_service(self, name):
        if name == self.namespace:
            raise ValueError("本轮不提供既有会话的命名根迁移")
        await self.stop_service(name)
        return await self.start_service(name)

    async def _quiesce(self):
        self._closing = True
        for state in self.states.values():
            state.desired, state.next_retry = "stopped", None
            state.epoch += 1
        if self._task:
            await self._task

    async def shutdown(self, *, mode="drain", drain_timeout_ms=500):
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown(mode, drain_timeout_ms))
        return await asyncio.shield(self._shutdown_task)

    async def _shutdown(self, mode, drain_timeout_ms):
        await self._quiesce()
        # 等待已进入启动/回收临界区的策略任务结束，再交付精确回收顺序。
        async with self._reconcile_lock:
            for state in self.states.values():
                async with state.lock:
                    pass
            close_errors = []
            if mode == "drain":
                for state in reversed(list(self.states.values())):
                    if state.kernel and not state.kernel["reaped"]:
                        try:
                            await self._close_application(state)
                        except Exception as exc:
                            close_errors.append(f"{state.spec.service_key}: {exc}")
            for request in (await self.client.process_snapshot())["policy_requests"]:
                await self.client.ack_policy(request, "stale")
            order = [
                x.kernel["domain_id"] for x in reversed(list(self.states.values())) if x.kernel
            ]
            self.shutdown_report = await self.client.shutdown(
                mode=mode, drain_timeout_ms=drain_timeout_ms, domain_order=order
            )
            actual = {x["domain_id"]: x for x in self.shutdown_report["domains"]}
            for state in self.states.values():
                if state.kernel:
                    state.kernel = actual.get(state.kernel["domain_id"], state.kernel)
                state.registration = None
                state.phase = "Stopped" if self.shutdown_report["clean"] else "Failed"
                self.record(state, "shutdown")
            self.registrations.clear()
            if close_errors:
                self.shutdown_report["application_close_errors"] = close_errors
                self.shutdown_report["clean"] = False
                for state in self.states.values():
                    state.phase = "Failed"
        return self.shutdown_report

    async def aclose(self):
        if self._shutdown_task:
            await asyncio.shield(self._shutdown_task)
            if self.failure:
                raise RuntimeError(self.failure)
            return
        await self._quiesce()
        errors = []
        for name in reversed(self.states):
            try:
                await self.stop_service(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        for request in (await self.client.process_snapshot())["policy_requests"]:
            await self.client.ack_policy(request, "stale")
        if self.failure:
            errors.append(self.failure)
        if errors:
            raise RuntimeError("; ".join(errors))
