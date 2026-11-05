# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""有界订阅、一次性定时器与单消费者多目标等待。"""

import asyncio
import math
from collections import deque
from dataclasses import dataclass, field, replace

from .tables import KernelFault, ObjectRecord


@dataclass
class EventRecord:
    owner: str
    domain: str | None = None
    scheme: str | None = None
    tag: str | None = None
    provider_object: str | None = None
    handle: str | None = None
    epoch: str | None = None
    sequence: int | None = None
    queue: deque = field(default_factory=deque)
    terminal: str | None = None
    waiting: bool = False
    timer: asyncio.TimerHandle | None = None
    due: float | None = None


class Events:
    def __init__(self, kernel):
        self.kernel = kernel
        self.records: dict[str, EventRecord] = {}
        self.pending: dict[str, EventRecord] = {}
        self.routes: dict[tuple[str, str], EventRecord] = {}

    def receive(self, domain, message):
        meta = message.meta
        record = self.routes.get((domain, meta.get("tag")))
        if record is None or record.terminal:
            return
        if (
            meta.get("scheme_id") != record.scheme
            or not isinstance(meta.get("provider_object_id"), str)
            or type(meta.get("seq")) is not int
            or meta["seq"] < 1
            or not isinstance(meta.get("epoch"), str)
        ):
            self.stop(record, "PROTOCOL_ERROR")
            return
        if (
            record.provider_object is not None
            and record.provider_object != meta["provider_object_id"]
        ):
            self.stop(record, "PROTOCOL_ERROR")
            return
        record.provider_object = meta["provider_object_id"]
        if record.epoch is not None and record.epoch != meta["epoch"]:
            self.stop(record, "CURSOR_GAP")
            return
        if record.sequence is not None and meta["seq"] != record.sequence + 1:
            self.stop(record, "CURSOR_GAP")
            return
        record.epoch, record.sequence = meta["epoch"], meta["seq"]
        if len(record.queue) >= 8:
            self.stop(record, "CURSOR_GAP")
            return
        record.queue.append(
            (
                {"seq": meta["seq"], "cursor": {"epoch": meta["epoch"], "seq": meta["seq"]}},
                message.opaque_payload,
            )
        )
        self.kernel.changed.set()

    async def subscribe(self, session, meta):
        source, base, scheme = self.kernel._target(session, meta["handle"])
        if (
            source.kind != "resource"
            or meta.get("topic") != "events"
            or "events" not in source.actions
        ):
            raise KernelFault("ACCESS_DENIED", "此引用没有主题订阅资格")
        if scheme.domain_id is None:
            raise KernelFault("NOT_SUPPORTED", "内置方案没有 Provider 订阅")
        if len(self.records) + len(self.pending) >= 128:
            raise KernelFault("QUOTA_EXCEEDED", "事件记录已满")
        self.kernel.references.capacity(session)
        domain = self.kernel.processes.get(scheme.domain_id)
        tag = self.kernel.new_id("subscribe")
        record = EventRecord(session.session_id, domain.domain_id, scheme.scheme_id, tag)
        self.pending[tag] = record
        self.routes[(domain.domain_id, tag)] = record
        opened = None
        try:
            reply = await self.kernel._rpc(
                domain,
                {
                    "op": "subscribe",
                    "scheme_id": scheme.scheme_id,
                    "provider_object_id": base.provider_object_id,
                    "topic": meta["topic"],
                    "tag": tag,
                    "basis": list(source.basis),
                    "authorization_epoch": source.handle_id,
                    "cursor": meta.get("cursor"),
                },
                prepare=lambda _: self.kernel.source_live(session, source),
            )
            result = self.kernel._require_ok(reply)
            opened = result["provider_object_id"]
            if (
                not isinstance(opened, str)
                or not isinstance(result["epoch"], str)
                or type(result["start_seq"]) is not int
                or result["start_seq"] < 1
            ):
                raise KernelFault("PROTOCOL_ERROR", "无效订阅答复")
            self.kernel.source_live(session, source)
            if record.provider_object is not None and record.provider_object != opened:
                raise KernelFault("PROTOCOL_ERROR", "早到事件与订阅对象不匹配")
            if record.epoch is not None and record.epoch != result["epoch"]:
                raise KernelFault("PROTOCOL_ERROR", "早到事件的时期不匹配")
            if record.queue and record.queue[0][0]["seq"] != result["start_seq"]:
                raise KernelFault("CURSOR_GAP", "早到事件起点不连续")
            record.provider_object, record.epoch = opened, result["epoch"]
            if record.sequence is None:
                record.sequence = result["start_seq"] - 1
            obj = ObjectRecord(
                self.kernel.new_id("object"), scheme.scheme_id, opened, kind="subscription"
            )
            node = self.kernel.derived(session, obj, source, {"wait"})
            record.handle = node.handle_id
            self.records[node.handle_id] = record
            if math.isfinite(node.expiry):
                record.timer = asyncio.get_running_loop().call_at(
                    node.expiry, self.stop, record, "REVOKED"
                )
            if record.terminal:
                self.kernel.drop_reference(node)
            return {
                "handle_id": node.handle_id,
                "epoch": record.epoch,
                "start_seq": result["start_seq"],
            }
        except BaseException:
            if opened is not None:
                await self.kernel.close_provider(domain, scheme.scheme_id, opened)
            self.routes.pop((domain.domain_id, tag), None)
            raise
        finally:
            self.pending.pop(tag, None)

    def stop(self, record, status):
        if record.terminal is not None:
            return
        record.terminal = status
        record.queue.clear()
        if record.timer:
            record.timer.cancel()
        if record.handle:
            self.kernel.drop_reference(self.kernel.references.nodes[record.handle])
        self.kernel.changed.set()

    def invalidate(self, node):
        record = self.records.get(node.handle_id)
        if record:
            self.stop(record, "REVOKED")

    def close(self, node):
        record = self.records.pop(node.handle_id, None)
        if record:
            self.stop(record, "CLOSED")
            if record.tag:
                self.routes.pop((record.domain, record.tag), None)

    def domain_closed(self, domain):
        for record in list(self.routes.values()):
            if record.domain == domain:
                self.stop(record, "PROVIDER_UNAVAILABLE")

    def create_timer(self, session, source, milliseconds):
        if type(milliseconds) is not int or not 0 <= milliseconds <= 3000:
            raise KernelFault("INVALID_ARGUMENT", "定时预算范围为 0..3000 ms")
        if len(self.records) + len(self.pending) >= 128:
            raise KernelFault("QUOTA_EXCEEDED", "事件记录已满")
        self.kernel.source_live(session, source)
        obj = ObjectRecord(self.kernel.new_id("object"), self.kernel.time_id, "timer", kind="timer")
        node = self.kernel.derived(session, obj, source, {"wait"})
        record = EventRecord(
            session.session_id,
            handle=node.handle_id,
            due=asyncio.get_running_loop().time() + milliseconds / 1000,
        )
        self.records[node.handle_id] = record
        record.timer = asyncio.get_running_loop().call_at(
            min(record.due, node.expiry), self.fire, record
        )
        return {"handle_id": node.handle_id, "due": record.due}

    def fire(self, record):
        if record.terminal is None:
            try:
                self.kernel.references.check(self.kernel.references.nodes[record.handle])
            except KernelFault:
                self.stop(record, "REVOKED")
                return
            record.terminal = "FIRED"
            self.kernel.changed.set()

    def cancel_timer(self, session, handle):
        node = session.handles.get(handle)
        if node is None or node.kind != "timer":
            raise KernelFault("INVALID_HANDLE", "定时器不属于当前会话")
        self.stop(self.records[handle], "CANCELLED")
        return {"terminal": self.records[handle].terminal}

    async def wait(self, session, meta):
        targets = meta.get("targets")
        if targets is None:
            targets = [{"kind": "system", "reference": meta["system_events"]}]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
            raise KernelFault("INVALID_ARGUMENT", "等待集合范围为 1..8 个目标")
        seen, claims, resolved = set(), [], []
        try:
            for target in targets:
                kind, reference = target["kind"], target["reference"]
                if reference in seen:
                    raise KernelFault("INVALID_ARGUMENT", "重复等待目标")
                seen.add(reference)
                if kind == "invocation":
                    record = self.kernel.calls.owned(session, reference)
                elif kind == "system":
                    if reference != session.hello["system_events"]:
                        raise KernelFault("INVALID_HANDLE", "系统事件入口不属于当前会话")
                    if session.waiting_events:
                        raise KernelFault("BUSY", "系统队列已有等待者")
                    session.waiting_events = True
                    claims.append((session, "waiting_events"))
                    record = session
                elif kind == "supervision":
                    if session.role != "bootstrap" or reference != session.hello.get(
                        "supervision_events"
                    ):
                        raise KernelFault("ACCESS_DENIED", "没有监督事实入口")
                    if session.waiting_supervision:
                        raise KernelFault("BUSY", "监督事实入口已有等待者")
                    session.waiting_supervision = True
                    claims.append((session, "waiting_supervision"))
                    record = session
                elif kind in ("subscription", "timer"):
                    node = session.handles.get(reference)
                    if node is None or node.kind != kind:
                        raise KernelFault("INVALID_HANDLE", "事件目标不属于当前会话")
                    record = self.records[reference]
                    if record.waiting:
                        raise KernelFault("BUSY", "事件队列已有等待者")
                    record.waiting = True
                    claims.append((record, "waiting"))
                else:
                    raise KernelFault("INVALID_ARGUMENT", "未知等待类型")
                resolved.append((kind, reference, record))
            deadline = asyncio.get_running_loop().time() + self.kernel.calls.timeout(meta)
            while True:
                self.kernel.changed.clear()
                for kind, reference, record in resolved:
                    target = {"kind": kind, "reference": reference}
                    if kind == "invocation" and record.terminal.is_set():
                        facts, payload = self.kernel.calls.facts(record)
                        return {**facts, "target": target}, payload
                    if kind == "system" and not session.events.empty():
                        return {"event": session.events.get_nowait(), "target": target}, None
                    if kind == "supervision":
                        if session.supervision_gap:
                            session.supervision_gap = False
                            while not session.supervision.empty():
                                session.supervision.get_nowait()
                            return {
                                "event": {
                                    "type": "FACTS_GAP",
                                    "revision": self.kernel.fact_revision,
                                },
                                "target": target,
                            }, None
                        if not session.supervision.empty():
                            return {
                                "event": session.supervision.get_nowait(),
                                "target": target,
                            }, None
                    if kind in ("subscription", "timer"):
                        if record.terminal:
                            return {
                                "event": {
                                    "status": record.terminal,
                                    "terminal": True,
                                    "due": record.due,
                                },
                                "target": target,
                            }, None
                        try:
                            self.kernel.references.check(self.kernel.references.nodes[reference])
                        except KernelFault:
                            self.stop(record, "REVOKED")
                            continue
                        if record.queue:
                            event, payload = record.queue.popleft()
                            return {
                                "event": {**event, "terminal": False},
                                "target": target,
                            }, payload
                remaining = deadline - asyncio.get_running_loop().time()
                if self.kernel._closing:
                    raise KernelFault("SHUTTING_DOWN", "停机结束事件等待")
                if remaining <= 0:
                    raise KernelFault("WAIT_TIMEOUT", "事件等待超时")
                try:
                    await asyncio.wait_for(self.kernel.changed.wait(), remaining)
                except TimeoutError:
                    raise KernelFault("WAIT_TIMEOUT", "事件等待超时") from None
        finally:
            for record, attribute in claims:
                setattr(record, attribute, False)

    def delivery(self, session, reply):
        target = reply.meta.get("target", {})
        if reply.error or target.get("kind") not in ("subscription", "timer"):
            return reply
        reference = target["reference"]
        record = self.records.get(reference)
        if record is None:
            status = "CLOSED"
        else:
            try:
                self.kernel.references.check(self.kernel.references.nodes[reference])
            except KernelFault:
                self.stop(record, "REVOKED")
            status = record.terminal
        if status:
            return replace(
                reply,
                meta={**reply.meta, "event": {"status": status, "terminal": True}},
                opaque_payload=None,
            )
        return reply

    def aclose(self):
        for record in self.records.values():
            self.stop(record, "CLOSED")
