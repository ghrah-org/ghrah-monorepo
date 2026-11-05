# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""机制入口的高层客户端，业务 codec 仍只在 SDK 使用。"""

import time
from dataclasses import dataclass
from typing import Any

from .client import RemoteError, RpcClient
from .payloads import decode_payload, encode_payload
from .protocol import ErrorRecord


@dataclass(frozen=True)
class CallResult:
    invocation_id: str
    settlement_state: str
    dispatch_fact: str
    effect_evidence: str
    value: Any
    error: ErrorRecord | None
    delivery_status: str = "DELIVERED"
    policy_action_required: bool = False


@dataclass(frozen=True)
class ReadResult:
    data: bytes
    eof: bool


class KernelClient:
    def __init__(self, rpc: RpcClient):
        self.rpc = rpc
        self.session: dict = {}
        self.shutdown_report = None

    async def _request(self, op: str, *, payload: bytes | None = None, **parameters):
        reply = await self.rpc.exchange({"op": op, **parameters}, payload, timeout=4)
        if reply.error is not None:
            raise RemoteError(reply.error)
        return reply

    async def hello(
        self,
        credential: str,
        *,
        requires_ns_root: bool = True,
        accept_transfers: bool = False,
        accept_channels: bool = False,
    ) -> dict:
        self.session = (
            await self._request(
                "hello",
                credential=credential,
                requires_ns_root=requires_ns_root,
                accept_transfers=accept_transfers,
                accept_channels=accept_channels,
            )
        ).meta
        return self.session

    async def register(self, domain: str, schemes: list[dict], key: str) -> dict:
        return (
            await self._request("register", domain=domain, schemes=schemes, registration_key=key)
        ).meta

    async def open(self, path: str, *, base: str | None = None, actions=None) -> str:
        reply = await self._request(
            "open",
            base=base or self.session.get("ns_root_handle"),
            path=path,
            actions=actions,
        )
        return reply.meta["handle_id"]

    async def dup(self, handle: str, **constraints) -> dict:
        return (await self._request("dup", handle=handle, **constraints)).meta

    async def revoke(self, selector: dict) -> dict:
        return (await self._request("revoke", selector=selector)).meta

    async def cancel(self, invocation: str) -> dict:
        return (await self._request("cancel", invocation=invocation)).meta

    async def wait_event(self, *, timeout_ms=2000) -> dict:
        return (
            await self._request(
                "wait", system_events=self.session["system_events"], timeout_ms=timeout_ms
            )
        ).meta["event"]

    async def subscribe(self, handle, topic="events", *, cursor=None) -> str:
        return (await self._request("subscribe", handle=handle, topic=topic, cursor=cursor)).meta[
            "handle_id"
        ]

    async def wait_any(self, targets: list[dict], *, timeout_ms=2000) -> dict:
        reply = await self._request("wait", targets=targets, timeout_ms=timeout_ms)
        return {
            **reply.meta,
            "value": decode_payload(reply.opaque_payload)
            if reply.opaque_payload is not None
            else None,
        }

    async def wait_item(self, handle, *, kind="subscription", timeout_ms=2000) -> dict:
        return await self.wait_any([{"kind": kind, "reference": handle}], timeout_ms=timeout_ms)

    async def create_timer(self, milliseconds) -> str:
        return (
            await self.call(
                self.session["time_handle"], "create_timer", {"milliseconds": milliseconds}
            )
        )["handle_id"]

    async def cancel_timer(self, handle) -> dict:
        return await self.call(self.session["time_handle"], "cancel_timer", {"handle": handle})

    async def channel(self, handle, *, direction="upload", quota=4096) -> dict:
        return (
            await self._request("channel", handle=handle, direction=direction, quota=quota)
        ).meta

    async def read(self, handle, max_bytes=16384, *, timeout_ms=2000) -> ReadResult:
        reply = await self._request(
            "read",
            handle=handle,
            max_bytes=max_bytes,
            deadline=time.monotonic() + timeout_ms / 1000,
        )
        return ReadResult(reply.opaque_payload, reply.meta["eof"])

    async def write(self, handle, data: bytes, *, timeout_ms=2000) -> int:
        if not isinstance(data, bytes):
            raise TypeError("write 需要 bytes")
        return (
            await self._request(
                "write", handle=handle, payload=data, deadline=time.monotonic() + timeout_ms / 1000
            )
        ).meta["accepted_bytes"]

    async def invoke(
        self,
        handle: str,
        action: str,
        arguments: Any,
        *,
        deadline: float | None = None,
        timeout_ms: int | None = None,
    ) -> str:
        if timeout_ms is not None:
            if deadline is not None:
                raise ValueError("deadline 与 timeout_ms 二选一")
            deadline = time.monotonic() + timeout_ms / 1000
        return (
            await self._request(
                "invoke",
                handle=handle,
                action=action,
                payload=encode_payload(arguments),
                deadline=deadline,
            )
        ).meta["invocation_id"]

    async def wait(self, invocation: str, *, timeout_ms: int = 2000) -> CallResult:
        reply = await self._request("wait", invocation=invocation, timeout_ms=timeout_ms)
        meta = reply.meta
        error = ErrorRecord(**meta["call_error"]) if meta["call_error"] else None
        return CallResult(
            invocation,
            meta["settlement_state"],
            meta["dispatch_fact"],
            meta["effect_evidence"],
            decode_payload(reply.opaque_payload) if reply.opaque_payload is not None else None,
            error,
            meta["delivery_status"],
            meta["policy_action_required"],
        )

    async def close(self, reference: str) -> dict:
        return (await self._request("close", reference=reference)).meta

    async def call(self, handle: str, action: str, arguments: Any) -> Any:
        """组合 invoke/wait/close，不增加基础入口。"""
        invocation = await self.invoke(handle, action, arguments)
        try:
            result = await self.wait(invocation)
            if result.error is not None:
                raise RemoteError(result.error)
            if result.delivery_status != "DELIVERED":
                raise RemoteError(ErrorRecord(result.delivery_status, "结果交付资格已失效"))
            return result.value
        finally:
            await self.close(invocation)

    async def spawn(
        self, service_key: str, kind: str, *, spawn_key: str, fixture="normal", entry_key=None
    ) -> dict:
        return await self.call(
            self.session["proc_handle"],
            "spawn",
            {
                "service_key": service_key,
                "kind": kind,
                "spawn_key": spawn_key,
                "fixture": fixture,
                "entry_key": entry_key,
            },
        )

    async def kill(self, domain: str) -> dict:
        return await self.call(self.session["proc_handle"], "kill", {"domain": domain})

    async def status(self, domain: str) -> dict:
        return await self.call(self.session["proc_handle"], "status", {"domain": domain})

    async def process_snapshot(self) -> dict:
        return await self.call(self.session["proc_handle"], "snapshot", {})

    async def wait_domain(self, domain, *, timeout_ms=200) -> dict:
        return await self.call(
            self.session["proc_handle"], "wait", {"domain": domain, "timeout_ms": timeout_ms}
        )

    async def drain(self, domain, *, timeout_ms=50) -> dict:
        return await self.call(
            self.session["proc_handle"], "drain", {"domain": domain, "timeout_ms": timeout_ms}
        )

    async def ack_policy(self, record, decision) -> dict:
        return await self.call(
            self.session["proc_handle"],
            "ack_policy",
            {
                "invocation_id": record["invocation_id"],
                "domain_id": record["domain_id"],
                "generation": record["generation"],
                "decision": decision,
            },
        )

    async def wait_supervision(self, *, timeout_ms=200) -> dict:
        return (
            await self.wait_any(
                [{"kind": "supervision", "reference": self.session["supervision_events"]}],
                timeout_ms=timeout_ms,
            )
        )["event"]

    async def stats(self) -> dict:
        return await self.call(self.session["sys_handle"], "stats", {})

    async def shutdown(self, *, mode="drain", drain_timeout_ms=500, domain_order=None) -> dict:
        reply = await self.rpc.exchange(
            {
                "op": "shutdown",
                "admin_cap": self.session.get("proc_handle"),
                "mode": mode,
                "drain_timeout_ms": drain_timeout_ms,
                "domain_order": domain_order or [],
            },
            timeout=None,
        )
        if reply.error:
            raise RemoteError(reply.error)
        self.shutdown_report = reply.meta
        return reply.meta

    async def aclose(self) -> None:
        await self.rpc.aclose()
