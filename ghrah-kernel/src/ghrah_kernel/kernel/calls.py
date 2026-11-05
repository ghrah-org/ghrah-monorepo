# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""异步调用、有限结算、在途持有与交付资格；不解释业务载荷。"""

import asyncio
import math
from dataclasses import asdict, replace

from ..context import ProviderContext
from ..protocol import ErrorRecord
from .tables import Invocation, KernelFault


class Calls:
    settlement_seconds = 0.05

    def __init__(self, kernel):
        self.kernel = kernel
        self.late_replies = 0

    def invoke(self, session, request):
        meta = request.meta
        handle, obj, scheme = self.kernel._target(session, meta["handle"])
        if meta["action"] not in handle.actions:
            raise KernelFault("ACCESS_DENIED", "句柄不允许该动作")
        deadline = meta.get("deadline")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise KernelFault("INVALID_ARGUMENT", "deadline 必须为有限单调时钟值")
        if sum(x.owner is session for x in self.kernel.invocations.values()) >= 64:
            raise KernelFault("QUOTA_EXCEEDED", "调用观察记录已满")
        invocation = Invocation(
            self.kernel.new_id("invocation"),
            session,
            obj.object_id,
            meta["action"],
            request.opaque_payload,
            source=handle.handle_id,
            deadline=deadline,
        )
        if deadline is not None and scheme.domain_id is not None:
            if len(self.kernel.policy_requests) >= 64:
                raise KernelFault("QUOTA_EXCEEDED", "超时策略事实预留已满")
            domain = self.kernel.processes.get(scheme.domain_id)
            self.kernel.policy_requests[invocation.invocation_id] = {
                "invocation_id": invocation.invocation_id,
                "domain_id": domain.domain_id,
                "generation": domain.generation,
                "status": "reserved",
            }
        obj.pins += 1
        self.kernel.invocations[invocation.invocation_id] = invocation
        invocation.task = asyncio.create_task(self.run(invocation, obj, scheme))
        return {"invocation_id": invocation.invocation_id}

    def owned(self, session, reference):
        invocation = self.kernel.invocations.get(reference)
        if invocation is None or invocation.owner is not session or not invocation.observed:
            raise KernelFault("INVALID_INVOCATION", "调用不属于当前会话")
        return invocation

    def interrupt(self, invocation, reason):
        if invocation.terminal.is_set():
            return False
        # 首个执行停止原因固定；后续撤权依然会阻止结果交付。
        if invocation.stop_reason is None:
            invocation.stop_reason = reason
            invocation.policy_action_required = reason == "TIMED_OUT" and invocation.dispatched
            invocation.interrupted.set()
        return True

    def commit(self, invocation, request):
        if self.kernel.shutdown.active:
            raise KernelFault("SHUTDOWN", "停机后不再提交排队工作")
        handle = self.kernel.references.nodes[invocation.source]
        self.kernel.references.check(handle)
        scheme = self.kernel.schemes[handle.basis[0]]
        if scheme.domain_id is not None:
            domain = self.kernel.processes.get(scheme.domain_id)
            if domain.reaped or domain.state not in ("ready", "draining"):
                raise KernelFault("STALE_REFERENCE", "传输提交前实例已终结")
        if invocation.stop_reason:
            raise KernelFault(invocation.stop_reason, "派发前调用已停止")
        if (
            invocation.deadline is not None
            and invocation.deadline <= asyncio.get_running_loop().time()
        ):
            self.interrupt(invocation, "TIMED_OUT")
            raise KernelFault("TIMED_OUT", "派发前截止时间已到")
        invocation.dispatched, invocation.effect, invocation.state = True, "UNKNOWN", "DISPATCHED"
        return request

    async def execute(self, invocation, obj, scheme):
        try:
            async with obj.invoke_lock:
                if scheme.domain_id is None:
                    from .builtins import invoke_builtin

                    self.commit(invocation, None)
                    payload, effect = await invoke_builtin(
                        self.kernel,
                        invocation.owner,
                        scheme.name,
                        invocation.action,
                        invocation.payload,
                        self.kernel.references.nodes[invocation.source],
                    )
                    return payload, None, effect
                domain = self.kernel.processes.get(scheme.domain_id)
                if domain.state not in ("ready", "draining") or domain.reaped:
                    raise KernelFault("STALE_REFERENCE", "派发前实例终结")
                reply = await self.kernel._rpc(
                    domain,
                    {
                        "op": "invoke",
                        "scheme_id": scheme.scheme_id,
                        "provider_object_id": obj.provider_object_id,
                        "action": invocation.action,
                        "invocation_id": invocation.invocation_id,
                        "context": ProviderContext(
                            invocation.owner.principal,
                            invocation.owner.session_id,
                            invocation.owner.domain_id,
                            invocation.owner.resolution_scope,
                            scheme.scheme_id,
                            obj.provider_object_id,
                            invocation.invocation_id,
                            invocation.deadline,
                        ).to_dict(),
                    },
                    invocation.payload,
                    prepare=lambda request: self.commit(invocation, request),
                    reply_timeout=None,
                )
                effect = {
                    "NO_EFFECT": "PROVIDER_REPORTED_NO_EFFECT",
                    "EFFECT_OCCURRED": "PROVIDER_REPORTED_EFFECT",
                }.get(reply.meta.get("reported_effect"), "UNKNOWN")
                return reply.opaque_payload, reply.error, effect
        except KernelFault as exc:
            return (
                None,
                ErrorRecord(exc.code, str(exc)),
                "UNKNOWN" if invocation.dispatched else "NOT_DISPATCHED",
            )
        except Exception as exc:
            self.kernel.errors.append(repr(exc))
            return (
                None,
                ErrorRecord("INTERNAL_ERROR", repr(exc)),
                "UNKNOWN" if invocation.dispatched else "NOT_DISPATCHED",
            )

    async def notify_cancel(self, invocation, scheme):
        if scheme.domain_id is None:
            return
        domain = self.kernel.processes.get(scheme.domain_id)
        try:
            # 取消回执不作为效果证据；只有原调用答复能够报告效果。
            await domain.peer.exchange(
                {
                    "op": "cancel",
                    "scheme_id": scheme.scheme_id,
                    "invocation_id": invocation.invocation_id,
                },
                timeout=0.2,
            )
        except ConnectionError, OSError, TimeoutError:
            pass

    async def run(self, invocation, obj, scheme):
        loop = asyncio.get_running_loop()
        timers = []
        worker = asyncio.create_task(self.execute(invocation, obj, scheme))
        interrupted = asyncio.create_task(invocation.interrupted.wait())
        source = self.kernel.references.nodes[invocation.source]
        if invocation.deadline is not None:
            timers.append(
                loop.call_at(invocation.deadline, self.interrupt, invocation, "TIMED_OUT")
            )
        if math.isfinite(source.expiry):
            timers.append(loop.call_at(source.expiry, self.interrupt, invocation, "REVOKED"))
        try:
            await asyncio.wait((worker, interrupted), return_when=asyncio.FIRST_COMPLETED)
            if invocation.stop_reason is not None:
                if not invocation.dispatched:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                    invocation.result, invocation.error, invocation.effect = (
                        None,
                        ErrorRecord(invocation.stop_reason, "调用在派发前停止"),
                        "NOT_DISPATCHED",
                    )
                else:
                    invocation.state = "SETTLING"
                    self.kernel.maintain(self.notify_cancel(invocation, scheme))
                    try:
                        payload, error, effect = await asyncio.wait_for(
                            asyncio.shield(worker), self.settlement_seconds
                        )
                    except TimeoutError:
                        payload, _error, effect = None, None, "UNKNOWN"
                    invocation.result, invocation.effect = payload, effect
                    invocation.error = ErrorRecord(
                        invocation.stop_reason, "调用停止，效果以证据字段为准"
                    )
                invocation.state = invocation.stop_reason
            else:
                invocation.result, invocation.error, invocation.effect = worker.result()
                invocation.state = "FAILED" if invocation.error else "COMPLETED"
            invocation.terminal.set()
            policy = self.kernel.policy_requests.get(invocation.invocation_id)
            if policy is not None:
                if invocation.policy_action_required:
                    policy.update({**self.facts(invocation)[0], "status": "pending"})
                    self.kernel.notify_supervision({"type": "policy_action_required", **policy})
                else:
                    self.kernel.policy_requests.pop(invocation.invocation_id)
            self.kernel.changed.set()
            # 观察终结不等于 Provider 已停止。仍持有对象，直到原答复或端点终结。
            if not worker.done():
                await worker
                self.late_replies += 1
        finally:
            for timer in timers:
                timer.cancel()
            interrupted.cancel()
            await asyncio.gather(interrupted, return_exceptions=True)
            obj.pins -= 1
            await self.kernel._release(obj)
            if not invocation.observed or not invocation.owner.active:
                self.kernel.invocations.pop(invocation.invocation_id, None)
            self.kernel.changed.set()

    async def wait(self, session, meta):
        invocation = self.owned(session, meta["invocation"])
        timeout = self.timeout(meta)
        try:
            await asyncio.wait_for(invocation.terminal.wait(), timeout=timeout)
        except TimeoutError:
            raise KernelFault("WAIT_TIMEOUT", "仅等待超时，调用未被取消") from None
        return self.facts(invocation)

    @staticmethod
    def facts(invocation):
        return {
            "invocation_id": invocation.invocation_id,
            "settlement_state": invocation.state,
            "dispatch_fact": "DISPATCHED" if invocation.dispatched else "NOT_DISPATCHED",
            "effect_evidence": invocation.effect,
            "call_error": asdict(invocation.error) if invocation.error else None,
            "policy_action_required": invocation.policy_action_required,
        }, invocation.result

    @staticmethod
    def timeout(meta):
        timeout = meta.get("timeout_ms", 2000)
        if type(timeout) is not int or not 0 <= timeout <= 3000:
            raise KernelFault("INVALID_ARGUMENT", "等待预算范围为 0..3000 ms")
        return timeout / 1000

    def delivery(self, session, request, reply):
        reference = request.meta.get("invocation") or (
            reply.meta.get("target", {}).get("reference")
            if reply.meta.get("target", {}).get("kind") == "invocation"
            else None
        )
        if request.meta.get("op") != "wait" or reference is None or reply.error:
            return reply
        invocation = self.owned(session, reference)
        try:
            handle = self.kernel.references.nodes[invocation.source]
            self.kernel.references.check(handle)
            scheme = self.kernel.schemes[handle.basis[0]] if handle.basis else None
            if scheme and scheme.domain_id is not None:
                domain = self.kernel.processes.get(scheme.domain_id)
                if domain.reaped or domain.state not in ("ready", "draining"):
                    raise KernelFault("STALE_REFERENCE", "结果目标实例已终结")
            status = "DELIVERED"
        except KernelFault as exc:
            status = exc.code
        return replace(
            reply,
            meta={**reply.meta, "delivery_status": status},
            opaque_payload=reply.opaque_payload if status == "DELIVERED" else None,
        )
