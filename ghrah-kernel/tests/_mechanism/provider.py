"""Provider SDK 在 handler 前后自动解码/编码业务参数。"""

import asyncio
import inspect
from collections.abc import Callable, Mapping

from ghrah_kernel.client import RemoteError
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.payloads import decode_payload, encode_payload
from ghrah_kernel.protocol import (
    Envelope,
    ErrorRecord,
    ProtocolError,
    decode_envelope,
    encode_envelope,
)


async def serve(endpoint: AsyncEndpoint, handlers: Mapping[str, Callable]) -> None:
    try:
        while True:
            request = decode_envelope(await endpoint.receive())
            if request.kind != "request":
                raise ProtocolError("提供者只接收 request")
            action = request.meta.get("action")
            handler = handlers.get(action) if isinstance(action, str) else None
            if handler is None:
                reply = Envelope(
                    "response",
                    request.req_id,
                    error=ErrorRecord("UNKNOWN_ACTION", "未注册的操作"),
                )
            else:
                try:
                    result = handler(decode_payload(request.opaque_payload))
                    if inspect.isawaitable(result):
                        result = await result
                    reply = Envelope(
                        "response", request.req_id, opaque_payload=encode_payload(result)
                    )
                except (ValueError, TypeError, KeyError) as exc:
                    reply = Envelope(
                        "response",
                        request.req_id,
                        error=ErrorRecord("INVALID_ARGUMENT", str(exc)),
                    )
            await endpoint.send(encode_envelope(reply))
    except EOFError, BrokenPipeError, ConnectionResetError:
        pass
    finally:
        await endpoint.aclose()


class ServiceProvider:
    """服务域 SDK：控制操作用元数据，业务 handler 只接收结构化参数。"""

    def __init__(
        self, endpoint, enrollment, schemes, handlers, *, resolver=None, fixtures=None, caller=None
    ):
        self.endpoint = endpoint
        self.enrollment = enrollment
        self.schemes = schemes
        self.handlers = handlers
        self.resolver = resolver
        self.fixtures = fixtures
        self.pending: dict[str, asyncio.Task] = {}
        self.bound: dict[str, str] = {}
        self.objects: dict[str, str] = {}
        self._next_object = 0
        self.caller = caller
        self.paths: dict[str, str] = {}
        self.subscriptions: dict[str, dict] = {}
        self.sequences: dict[str, int] = {}
        self.event_lock = asyncio.Lock()
        self.event_id = 0
        self.after_bind = None

    async def serve(self):
        tasks: set[asyncio.Task] = set()
        try:
            while True:
                request = decode_envelope(await self.endpoint.receive())
                if request.kind != "request":
                    raise ProtocolError("Provider 只接收控制请求")
                if len(tasks) >= 64 and request.meta.get("op") != "cancel":
                    await self.endpoint.send(
                        encode_envelope(
                            Envelope(
                                "response",
                                request.req_id,
                                error=ErrorRecord("BUSY", "Provider 请求额度已满"),
                            )
                        )
                    )
                    continue
                task = asyncio.create_task(self._answer(request))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except EOFError, BrokenPipeError, ConnectionResetError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.endpoint.aclose()

    async def _answer(self, request):
        if request.meta.get("op") == "subscribe":
            async with self.event_lock:
                await self._answer_inner(request)
        else:
            await self._answer_inner(request)

    async def _answer_inner(self, request):
        invocation = (
            request.meta.get("invocation_id") if request.meta.get("op") == "invoke" else None
        )
        if invocation:
            self.pending[invocation] = asyncio.current_task()
        try:
            try:
                meta, payload = await self._dispatch(request)
                reply = Envelope("response", request.req_id, meta, payload)
            except asyncio.CancelledError:
                action = request.meta.get("action")
                effect = (
                    "EFFECT_OCCURRED"
                    if action == "counter"
                    else "NO_EFFECT"
                    if action in ("delay", "echo", "add", "identity", "probe")
                    else "UNKNOWN"
                )
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": effect},
                    error=ErrorRecord("CANCELLED", "合作 Provider 已停止此调用"),
                )
            except (ValueError, TypeError, KeyError) as exc:
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": "UNKNOWN"},
                    error=ErrorRecord("INVALID_ARGUMENT", str(exc)),
                )
            except Exception as exc:
                code = getattr(exc, "code", "PROVIDER_ERROR")
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": "UNKNOWN"},
                    error=ErrorRecord(code, str(exc)),
                )
            try:
                await self.endpoint.send(encode_envelope(reply))
                if (
                    request.meta.get("op") == "bind_schemes"
                    and reply.error is None
                    and self.after_bind
                ):
                    callback, self.after_bind = self.after_bind, None
                    asyncio.get_running_loop().call_later(0.1, callback)
                if request.meta.get("op") == "subscribe" and reply.error is None:
                    await self._publish(request.meta["scheme_id"], {"ready": True})
            except ConnectionError, OSError:
                pass
        finally:
            if invocation:
                self.pending.pop(invocation, None)

    async def _dispatch(self, request):
        meta, op = request.meta, request.meta.get("op")
        if op == "describe":
            if meta.get("enrollment") != self.enrollment:
                raise ValueError("启动端点凭证不匹配")
            return {"schemes": self.schemes}, None
        if op == "bind_schemes":
            if set(meta["schemes"]) != {x["name"] for x in self.schemes}:
                raise ValueError("方案集不完整")
            self.bound = {sid: name for name, sid in meta["schemes"].items()}
            return {"bound": True}, None
        name = self.bound[meta["scheme_id"]]
        if op == "cancel":
            task = self.pending.get(meta["invocation_id"])
            if task is not None:
                task.cancel()
            return {"cancel_requested": task is not None}, None
        if op == "open":
            if self.resolver is not None:
                return self.resolver(meta["path"]), None
            spec = next(spec for spec in self.schemes if spec["name"] == name)
            grant = await self.fixtures.issue(name, spec, meta)
            self._next_object += 1
            object_id = f"{name}:{self._next_object}"
            self.objects[object_id] = name
            self.paths[object_id] = meta["path"].strip("/") or "main"
            spec = next(spec for spec in self.schemes if spec["name"] == name)
            return {
                "result": "opened",
                "provider_object_id": object_id,
                "actions": spec["actions"],
                "grant": grant,
            }, None
        if op == "close":
            self.paths.pop(meta["provider_object_id"], None)
            self.subscriptions.pop(meta["provider_object_id"], None)
            if self.objects.pop(meta["provider_object_id"], None) is not None:
                self.fixtures.closed += 1
            return {"closed": True}, None
        if op in ("subscribe", "channel"):
            base = meta["provider_object_id"]
            if self.objects.get(base) != name and base != "root":
                raise ValueError("无效的建立来源")
            path = self.paths.get(base, "main")
            if path == f"slow-{op}":
                self.fixtures.pending_setups.add(op)
                try:
                    await self.fixtures.gate(op).wait()
                finally:
                    self.fixtures.pending_setups.discard(op)
            if op == "subscribe":
                epoch = ":".join(map(str, meta["basis"])) + ":" + meta["authorization_epoch"]
                sequence = self.sequences.get(meta["scheme_id"], 0)
                cursor = meta.get("cursor")
                if cursor is not None and (
                    cursor.get("epoch") != epoch or cursor.get("seq") != sequence
                ):
                    raise RemoteError(ErrorRecord("CURSOR_GAP", "没有该授权时期的历史补发"))
            elif path == "reject-channel":
                raise RemoteError(ErrorRecord("ACCESS_DENIED", "Provider 拒绝接纳通道"))
            self._next_object += 1
            opened = f"{op}:{self._next_object}"
            self.objects[opened] = name
            self.fixtures.opened += 1
            if op == "subscribe":
                self.subscriptions[opened] = {
                    "scheme_id": meta["scheme_id"],
                    "epoch": epoch,
                    "tag": meta["tag"],
                }
                return {
                    "provider_object_id": opened,
                    "epoch": epoch,
                    "start_seq": sequence + 1,
                }, None
            return {
                "provider_object_id": opened,
                "peer_target_session_id": "not-a-session"
                if path == "bad-peer"
                else self.caller.session["session_id"],
                "effective_quota": meta["requested_quota"],
            }, None
        if op == "invoke":
            object_id = meta["provider_object_id"]
            if object_id != "root" and self.objects.get(object_id) != name:
                raise ValueError("无效的已打开对象")
            handler = self.handlers[name][meta["action"]]
            result = handler(decode_payload(request.opaque_payload))
            if inspect.isawaitable(result):
                result = await result
            effect = (
                "EFFECT_OCCURRED"
                if meta["action"] in ("counter", "bind", "emit", "release")
                else "UNKNOWN"
                if meta["action"] in ("run", "stream")
                else "NO_EFFECT"
            )
            return {"reported_effect": effect}, encode_payload(result)
        raise ValueError("不支持的 Provider 操作")

    async def publish(self, arguments):
        scheme = next(sid for sid, name in self.bound.items() if name == "ability")
        async with self.event_lock:
            await self._publish(scheme, arguments)
        return {"published": True}

    async def _publish(self, scheme, value):
        self.sequences[scheme] = self.sequences.get(scheme, 0) + 1
        for object_id, subscription in list(self.subscriptions.items()):
            if subscription["scheme_id"] == scheme:
                self.event_id += 1
                await self.endpoint.send(
                    encode_envelope(
                        Envelope(
                            "event",
                            self.event_id,
                            {
                                **subscription,
                                "provider_object_id": object_id,
                                "seq": self.sequences[scheme],
                            },
                            encode_payload(value),
                        )
                    )
                )
