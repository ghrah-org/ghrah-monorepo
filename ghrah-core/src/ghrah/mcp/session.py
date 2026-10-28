# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""单台服务器的连接生命周期与调用执行。

本模块**不 import MCP SDK**：它只持有一个不透明的连接句柄，真正的调用通过
:mod:`ghrah.mcp.client` 完成。这样会话逻辑（重建、超时、取消）可以在不启动 HTTP
端点的前提下，用进程内服务器完整验证。

四件事，按重要性排列：

**一、连接是惰性的。** 声明一台服务器不会建立连接；第一次真正调用时才连。读取
技能、渲染能力清单都不触发网络。

**二、会话被服务端丢弃时要自己重建。** Python SDK 收到 404 时只报告
「Session terminated」并让传输层失效，不重新握手。不补这一步的后果是：服务端一次
普通重启，这台服务器就永久不可用，直到进程重启。重建后**只对声明为只读的工具
自动重放一次**——写操作可能已经在服务端生效，重放会重复执行且无法撤销。

**三、取消时不要指望优雅关闭。** 连接关闭过程可能等待在途请求，而此刻我们正是
在被取消；等待它就是把取消变成挂起。因此先**同步丢弃句柄**（下一次调用自然会
重建），再尽力而为地关闭，失败不影响正确性。

**四、工具级失败不是异常。** SDK 把服务器返回的错误（含工具内部抛出的异常）归一
成 ``is_error=True`` 的结果，因此调用路径的返回值才是主要错误通道，异常只表示
连接与协议层出问题。这一点如果搞反，错误会被当成空结果静默吞掉。

已知限制：连接重建没有对并发调用做整体串行化——重建期间到达的另一个调用可能拿到
已失效的句柄，并因此也触发一次重建。最坏结果是多重连一次，不会产生错误结果。
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, suppress
from typing import Any

from ghrah.mcp.client import Connector, call_tool_once, open_client, translate_exception
from ghrah.mcp.errors import (
    FailureKind,
    McpConnectionError,
    McpError,
    McpRequestError,
)
from ghrah.mcp.result import ToolOutcome
from ghrah.mcp.spec import McpServerSpec, McpToolSpec

__all__ = ["McpSession"]


class McpSession:
    """一台 MCP 服务器的连接句柄持有者。"""

    def __init__(
        self,
        spec: McpServerSpec,
        *,
        credential: str | None = None,
        connector: Connector | None = None,
    ) -> None:
        self._spec = spec
        self._credential = credential
        self._connector: Connector = connector or open_client
        self._stack: AsyncExitStack | None = None
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._generation = 0
        self._closed = False

    @property
    def spec(self) -> McpServerSpec:
        return self._spec

    @property
    def server(self) -> str:
        return self._spec.server

    @property
    def generation(self) -> int:
        """连接代际。每次成功建连或断开都会递增，便于日志与陈旧判断。"""
        return self._generation

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def _connect_locked(self) -> Any:
        """在持锁状态下建立连接。"""
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(
                self._connector(
                    self._spec,
                    credential=self._credential,
                    read_timeout_s=self._spec.execution_timeout_ms / 1000,
                )
            )
        except BaseException:
            with suppress(BaseException):
                await stack.aclose()
            raise
        self._stack = stack
        self._client = client
        self._generation += 1
        return client

    async def _reset(self) -> None:
        """丢弃当前连接，并尽力关闭它。

        先同步清空句柄再关闭：即便关闭过程被取消或抛错，「本会话已断开」这个事实
        已经成立，下一次调用会重建，不会复用半坏的连接。
        """
        stack, self._stack, self._client = self._stack, None, None
        self._generation += 1
        if stack is not None:
            with suppress(BaseException):
                await stack.aclose()

    async def _ensure_client(self) -> Any:
        if self._closed:
            raise McpConnectionError(f"session for server {self._spec.server!r} is closed")
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                return await self._connect_locked()
            return self._client

    async def call_tool(
        self,
        tool: McpToolSpec,
        arguments: dict[str, Any] | None = None,
        *,
        read_timeout_s: float | None = None,
    ) -> ToolOutcome:
        """调用一个工具，返回投影后的结果。

        失败一律以异常形式抛出（:class:`McpError` 的子类），成功的调用**也可能**
        带有 ``is_error=True``——那是服务器声明的工具级失败，调用方必须检查。
        """
        timeout = self._spec.execution_timeout_ms / 1000
        attempts = 0

        while True:
            client = await self._ensure_client()
            try:
                return await asyncio.wait_for(
                    call_tool_once(
                        client,
                        tool.name,
                        arguments,
                        read_timeout_s=read_timeout_s,
                    ),
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                # 先断开再放行取消：清理可能等待在途请求，而此刻正在被取消。
                await self._reset()
                raise
            except TimeoutError:
                await self._reset()
                raise McpRequestError(
                    f"tool {tool.name!r} on server {self._spec.server!r} "
                    f"exceeded execution timeout ({self._spec.execution_timeout_ms} ms)",
                    kind=FailureKind.TRANSIENT,
                    uncertain=not tool.read_only,
                ) from None
            except Exception as exc:  # noqa: BLE001 - 统一翻译为本模块异常
                error: McpError = translate_exception(exc)
                if (
                    isinstance(error, McpRequestError)
                    and error.kind is FailureKind.SESSION_TERMINATED
                ):
                    await self._reset()
                    if tool.read_only and attempts == 0:
                        attempts += 1
                        continue  # 只读工具：重建后透明重放一次
                    raise McpRequestError(
                        f"session for server {self._spec.server!r} was terminated by the "
                        f"server while calling {tool.name!r}; the connection has been "
                        "rebuilt, but this call may or may not have taken effect",
                        kind=FailureKind.SESSION_TERMINATED,
                        code=error.code,
                        uncertain=not tool.read_only,
                    ) from exc
                raise error from exc

    async def close(self) -> None:
        """关闭连接。重复调用是安全的。"""
        self._closed = True
        await self._reset()

    async def __aenter__(self) -> McpSession:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()
