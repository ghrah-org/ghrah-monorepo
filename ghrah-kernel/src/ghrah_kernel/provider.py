# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受管 Provider 的最小消息端口；参数、可信上下文和效果证据分别传递。"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from .context import ProviderContext
from .endpoint import AsyncEndpoint
from .payloads import decode_payload, encode_payload
from .protocol import Envelope, ErrorRecord, ProtocolError, decode_envelope, encode_envelope
from .wire import MessageTooLarge

type Effect = Literal["NO_EFFECT", "EFFECT_OCCURRED", "UNKNOWN"]
type OpenHandler = Callable[[ProviderContext, str], dict | Awaitable[dict]]


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """handler 显式报告结果与效果；不按动作名或成功状态猜测。"""

    value: Any
    reported_effect: Effect

    def __post_init__(self) -> None:
        if self.reported_effect not in ("NO_EFFECT", "EFFECT_OCCURRED", "UNKNOWN"):
            raise ValueError("未知的 Provider 效果证据")


class ProviderFault(Exception):  # noqa: N818
    """Provider 显式错误；异常或取消缺少效果证据时保留 UNKNOWN。"""

    def __init__(self, code: str, message: str, *, effect: Effect = "UNKNOWN") -> None:
        super().__init__(message)
        self.code = code
        self.effect = ProviderResult(None, effect).reported_effect


type InvokeHandler = Callable[
    [ProviderContext, str, Any], ProviderResult | Awaitable[ProviderResult]
]


class Provider:
    """实现注册、打开、调用、取消与关闭；领域 handler 只消费自己的参数。"""

    def __init__(
        self,
        endpoint: AsyncEndpoint,
        enrollment: str,
        schemes: list[dict],
        *,
        open_handler: OpenHandler,
        invoke_handler: InvokeHandler,
        close_handler: Callable[[str, str], Any] | None = None,
    ) -> None:
        if not schemes or len({x["name"] for x in schemes}) != len(schemes):
            raise ValueError("Provider 必须声明不重名的方案")
        self.endpoint = endpoint
        self.enrollment = enrollment
        self.schemes = schemes
        self.open_handler = open_handler
        self.invoke_handler = invoke_handler
        self.close_handler = close_handler
        self.bound: dict[str, dict] = {}
        self._mapping: dict[str, str] | None = None
        self.pending: dict[str, asyncio.Task] = {}

    async def serve(self) -> None:
        """消费 Kernel 专用端点，连接关闭时收束本 Provider 的请求。"""
        tasks: set[asyncio.Task] = set()
        try:
            while True:
                request = decode_envelope(await self.endpoint.receive())
                if request.kind != "request":
                    raise ProtocolError("Provider 只接收 Kernel 请求")
                if len(tasks) >= 64 and request.meta.get("op") != "cancel":
                    await self.endpoint.send(
                        encode_envelope(
                            Envelope(
                                "response", request.req_id, error=ErrorRecord("BUSY", "请求已满")
                            )
                        )
                    )
                    continue
                task = asyncio.create_task(self._answer(request))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except EOFError, OSError, ConnectionError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.endpoint.aclose()

    async def _answer(self, request: Envelope) -> None:
        invocation = (
            request.meta.get("invocation_id") if request.meta.get("op") == "invoke" else None
        )
        if invocation is not None:
            self.pending[invocation] = asyncio.current_task()
        try:
            try:
                meta, payload = await self._dispatch(request)
                reply = Envelope("response", request.req_id, meta, payload)
            except asyncio.CancelledError:
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": "UNKNOWN"},
                    error=ErrorRecord("CANCELLED", "调用已取消，效果未经核定"),
                )
            except ProviderFault as exc:
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": exc.effect},
                    error=ErrorRecord(exc.code, str(exc)),
                )
            except (ValueError, TypeError, KeyError) as exc:
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": "UNKNOWN"},
                    error=ErrorRecord("INVALID_ARGUMENT", str(exc)),
                )
            except Exception as exc:
                reply = Envelope(
                    "response",
                    request.req_id,
                    {"reported_effect": "UNKNOWN"},
                    error=ErrorRecord("PROVIDER_ERROR", str(exc)),
                )
            try:
                data = encode_envelope(reply)
                if len(data) > self.endpoint.wire.max_bytes:
                    reply = Envelope(
                        "response",
                        request.req_id,
                        {"reported_effect": reply.meta.get("reported_effect", "UNKNOWN")},
                        error=ErrorRecord("MESSAGE_TOO_LARGE", "Provider 结果超过端点消息上限"),
                    )
                    data = encode_envelope(reply)
                await self.endpoint.send(data)
            except MessageTooLarge:
                # 即使端点被配置为更小的上限，也不留下未观察的后台任务异常。
                await self.endpoint.aclose()
            except ConnectionError, OSError:
                pass
        finally:
            if invocation is not None:
                self.pending.pop(invocation, None)

    async def _dispatch(self, request: Envelope) -> tuple[dict, bytes | None]:
        meta, op = request.meta, request.meta.get("op")
        if op == "describe":
            if meta.get("enrollment") != self.enrollment:
                raise ProviderFault("ACCESS_DENIED", "启动注册凭证不匹配")
            return {"schemes": self.schemes}, None
        if op == "bind_schemes":
            mapping = meta["schemes"]
            if set(mapping) != {x["name"] for x in self.schemes} or (
                self._mapping is not None and self._mapping != mapping
            ):
                raise ProviderFault("CONTRACT_MISMATCH", "方案绑定不完整或试图换代")
            if not all(isinstance(x, str) and x for x in mapping.values()) or len(
                set(mapping.values())
            ) != len(mapping):
                raise ValueError("方案实例引用无效")
            self._mapping = dict(mapping)
            self.bound = {mapping[x["name"]]: x for x in self.schemes}
            return {"bound": True}, None
        spec = self.bound.get(meta.get("scheme_id"))
        if spec is None:
            raise ProviderFault("NOT_FOUND", "未绑定的方案实例")
        if op == "cancel":
            task = self.pending.get(meta["invocation_id"])
            if task is not None:
                task.cancel()
            return {"cancel_requested": task is not None}, None
        if op == "close":
            if self.close_handler is not None:
                result = self.close_handler(meta["scheme_id"], meta["provider_object_id"])
                if inspect.isawaitable(result):
                    await result
            return {"closed": True}, None
        if op not in ("open", "invoke"):
            raise ProviderFault("NOT_SUPPORTED", "此 Provider 未实现该端口")
        context = ProviderContext(**meta["context"])
        if (
            context.scheme_id != meta["scheme_id"]
            or context.provider_object_id != meta["provider_object_id"]
        ):
            raise ValueError("可信上下文与目标不一致")
        if op == "open":
            result = self.open_handler(context, meta["path"])
            if inspect.isawaitable(result):
                result = await result
            return result, None
        if not context.invocation_id or context.invocation_id != meta["invocation_id"]:
            raise ValueError("可信调用编号不一致")
        action = meta["action"]
        if action not in spec["actions"]:
            raise ProviderFault("UNKNOWN_ACTION", "方案未声明该动作", effect="NO_EFFECT")
        result = self.invoke_handler(context, action, decode_payload(request.opaque_payload))
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ProviderResult):
            raise TypeError("handler 必须显式返回 ProviderResult")
        return {"reported_effect": result.reported_effect}, encode_payload(result.value)
