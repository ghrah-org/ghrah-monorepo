"""关闭接纳、有限排空和直属回收；不解释服务依赖或业务载荷。"""

import asyncio

from .tables import KernelFault


class Shutdown:
    def __init__(self, kernel):
        self.kernel = kernel
        self.task = None
        self.parameters = None
        self.report = None

    @property
    def active(self):
        return self.task is not None

    async def request(self, session, meta):
        # 即使已有停机任务，也必须先核验调用者的管理能力。
        if session.role != "bootstrap" or meta.get("admin_cap") != session.hello.get("proc_handle"):
            raise KernelFault("ACCESS_DENIED", "shutdown 需要引导会话的进程管理能力")
        if not self.active:
            handle, _, scheme = self.kernel._target(session, meta["admin_cap"])
            if scheme.scheme_id != self.kernel.proc_id or "kill" not in handle.actions:
                raise KernelFault("ACCESS_DENIED", "进程管理能力已失效")
        mode, budget = meta.get("mode"), meta.get("drain_timeout_ms", 500)
        if mode not in ("drain", "now") or type(budget) is not int or not 0 <= budget <= 1000:
            raise KernelFault("INVALID_ARGUMENT", "shutdown 需要 drain/now 与 0..1000 ms 排空预算")
        order = meta.get("domain_order", [])
        if (
            not isinstance(order, list)
            or any(not isinstance(x, str) for x in order)
            or len(set(order)) != len(order)
        ):
            raise KernelFault("INVALID_ARGUMENT", "回收顺序必须为不重复的精确域引用列表")
        for reference in order:
            self.kernel.processes.get(reference)
        parameters = (mode, budget, tuple(order))
        if self.active:
            if self.parameters != parameters:
                raise KernelFault("SHUTDOWN_CONFLICT", "停机已开始，不能改变模式或预算")
        else:
            self.parameters = parameters
            # 接纳门禁与排队拦截在第一次让出事件循环之前生效。
            for domain in self.kernel.processes.domains.values():
                if domain.state in ("ready", "starting"):
                    domain.state = "draining"
            for invocation in self.kernel.invocations.values():
                if mode == "now" or not invocation.dispatched:
                    self.kernel.calls.interrupt(invocation, "SHUTDOWN")
            self.task = asyncio.create_task(self.run(mode, budget, order))
        return await asyncio.shield(self.task)

    async def run(self, mode, budget, order):
        kernel = self.kernel
        started = asyncio.get_running_loop().time()
        kernel.notify_supervision({"type": "shutdown_started", "mode": mode})
        # 等待已接纳的 spawn 提交；门禁阻止后续 spawn。
        async with kernel.processes._spawn_lock:
            pass
        for domain in kernel.processes.domains.values():
            if domain.state in ("ready", "starting"):
                domain.state = "draining"
        calls = list(kernel.invocations.values())
        for invocation in calls:
            if mode == "now" or not invocation.dispatched:
                kernel.calls.interrupt(invocation, "SHUTDOWN")
        deadline = started + (budget / 1000 if mode == "drain" else 0)
        while (
            any(not x.task.done() for x in calls) and deadline > asyncio.get_running_loop().time()
        ):
            kernel.changed.clear()
            try:
                await asyncio.wait_for(
                    kernel.changed.wait(), deadline - asyncio.get_running_loop().time()
                )
            except TimeoutError:
                break
        drained = all(x.task.done() for x in calls)
        for invocation in calls:
            kernel.calls.interrupt(invocation, "SHUTDOWN")
        # 先给取消有限结算机会，再执行物理终止；UNKNOWN 不伪装成无效果。
        if calls:
            await asyncio.gather(*(x.terminal.wait() for x in calls))
        kernel._closing = True
        kernel.events.aclose()
        kernel.streams.aclose()
        kernel.changed.set()
        unfinished = []
        sequence = order + [x for x in kernel.processes.domains if x not in order]
        for reference in sequence:
            try:
                await asyncio.wait_for(kernel.processes.kill(reference, force=mode == "now"), 1)
            except Exception as exc:
                unfinished.append({"domain_id": reference, "error": repr(exc)})
                kernel.processes.get(reference).state = "failed"
        workers = [x.task for x in list(kernel.invocations.values())]
        if workers:
            done, pending = await asyncio.wait(workers, timeout=1)
            for task in pending:
                unfinished.append({"task": "invocation", "error": "worker 未结束"})
        facts = [kernel.calls.facts(x)[0] for x in calls]
        for session in kernel.sessions.values():
            session.active = False
            for reference in list(session.handles):
                if reference in session.handles:
                    await kernel._close(session, reference)
            while not session.events.empty():
                session.events.get_nowait()
        while kernel._maintenance:
            await asyncio.gather(*list(kernel._maintenance))
        kernel.unhandled_policy = list(kernel.policy_requests.values())
        kernel.policy_requests.clear()
        for reference, invocation in list(kernel.invocations.items()):
            if invocation.task.done():
                kernel.invocations.pop(reference)
        # 等待被关闭事件/pipe 唤醒的请求释放等待者声明。
        pending = [
            task for task, op in kernel._requests.items() if op != "shutdown" and not task.done()
        ]
        if pending:
            _, remaining = await asyncio.wait(pending, timeout=0.1)
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
        domains = [kernel.processes.get(x).snapshot() for x in sequence]
        stats = kernel.stats()
        resource_keys = (
            "handles",
            "objects",
            "invocations",
            "inflight",
            "event_records",
            "channels",
            "pipe_fds",
            "io_waiters",
            "event_waiters",
            "supervision_waiters",
            "pending_subscriptions",
        )
        logical = all(stats[key] == 0 for key in resource_keys)
        self.report = {
            "boot_id": kernel.boot_id,
            "mode": mode,
            "drain_timeout_ms": budget,
            "drained": drained,
            "logical_invalidated": logical,
            "calls_settled": all(x.terminal.is_set() for x in calls),
            "call_count": len(facts),
            "calls": facts[-32:],
            "calls_truncated": len(facts) > 32,
            "domain_count": len(domains),
            "domains": domains[:32],
            "domains_truncated": len(domains) > 32,
            "children_reaped": all(x["reaped"] for x in domains),
            "unfinished": unfinished,
            "stats": stats,
            "elapsed_ms": round((asyncio.get_running_loop().time() - started) * 1000, 2),
        }
        self.report["clean"] = (
            logical
            and self.report["calls_settled"]
            and self.report["children_reaped"]
            and not unfinished
        )
        return self.report
