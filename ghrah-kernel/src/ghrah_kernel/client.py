# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""高层消息客户端；call 是 SDK 帮助函数，不代表完整 ABI。"""

import asyncio
from typing import Any

from .endpoint import AsyncEndpoint
from .payloads import decode_payload, encode_payload
from .protocol import Envelope, ErrorRecord, ProtocolError, decode_envelope, encode_envelope
from .wire import MessageTooLarge


class RemoteError(RuntimeError):
    def __init__(self, error: ErrorRecord):
        self.code = error.code
        super().__init__(error.message)


class RpcClient:
    def __init__(self, endpoint: AsyncEndpoint):
        self.endpoint = endpoint
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Envelope]] = {}
        self._reader: asyncio.Task | None = None
        self._failure: BaseException | None = None
        self._slots = asyncio.Semaphore(16)
        self.event_handler = None
        self.failure_handler = None

    async def call(self, action: str, arguments: Any, *, timeout: float = 2) -> Any:
        reply = await self.exchange({"action": action}, encode_payload(arguments), timeout=timeout)
        if reply.error is not None:
            raise RemoteError(reply.error)
        return decode_payload(reply.opaque_payload)

    async def exchange(
        self,
        meta: dict,
        opaque_payload: bytes | None = None,
        *,
        timeout: float | None = 2,
        prepare=None,
    ) -> Envelope:
        """通用控制信封往返；此层不解读业务 bytes。"""
        async with self._slots:
            if self._failure is not None:
                raise ConnectionError("客户端连接不可用") from self._failure
            if self._reader is None:
                self._reader = asyncio.create_task(self._read_responses())
            self._next_id += 1
            req_id = self._next_id
            request = Envelope("request", req_id, meta, opaque_payload)
            response = asyncio.get_running_loop().create_future()
            self._pending[req_id] = response
            try:
                data = encode_envelope(request)
                if len(data) > self.endpoint.wire.max_bytes:
                    raise MessageTooLarge("消息超过端点上限")

                def commit():
                    if prepare is not None:
                        prepare(request)
                    return data

                async with asyncio.timeout(timeout):
                    await self.endpoint.send(b"", prepare=commit)
                    reply = await response
                return reply
            except TimeoutError:
                raise
            except (EOFError, OSError) as exc:
                raise ConnectionError("消息连接已结束") from exc
            finally:
                self._pending.pop(req_id, None)
                if not response.done():
                    response.cancel()
                elif not response.cancelled():
                    response.exception()

    async def _read_responses(self) -> None:
        try:
            while True:
                reply = decode_envelope(await self.endpoint.receive())
                if reply.kind == "event" and self.event_handler is not None:
                    self.event_handler(reply)
                    continue
                if reply.kind != "response":
                    raise ProtocolError("客户端只接收 response")
                pending = self._pending.get(reply.req_id)
                if pending is not None and not pending.done():
                    pending.set_result(reply)
        except (EOFError, OSError, ConnectionError, ProtocolError) as exc:
            self._failure = exc
            if self.failure_handler is not None:
                self.failure_handler()
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(ConnectionError("消息连接已结束"))

    async def aclose(self) -> None:
        self._failure = ConnectionError("客户端已关闭")
        await self.endpoint.aclose()
        if self._reader is not None:
            await self._reader

    async def __aenter__(self) -> RpcClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
