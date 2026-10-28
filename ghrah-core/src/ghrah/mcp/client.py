# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""官方 SDK 的唯一隔离层。

**本仓库只有这个文件 import ``mcp`` SDK**。其余模块只看到本模块暴露的窄接口，
这样 SDK 的类型、命名与版本差异不会渗到上层；将来 SDK 不兼容升级时，改动范围
被限制在这里。

三件职责：

1. **把配置变成请求头**：替换凭据占位符。缺失凭据在**建连之前**就失败，不让它
   变成一次 401——401 会被误读成「凭证错了」，而真实情况是「还没配」。
2. **建立连接**：https 客户端由 SDK 工厂创建（默认 30 秒连接/写入/池超时、
   300 秒读超时）。它默认**不跟随重定向**，跨源跳转一律不跟随，因此配置好的
   请求头不会被带到别的源上——这一点无需我们再实现一层剥离。
3. **把 SDK 异常翻译成本模块的失败分类**，供上层决定能否重放。

关于协议协商：SDK 的 ``mode="auto"`` 已经实现「先按无状态协议探测、失败再回退
握手协议」，且回退判定只用**明确的版本信号**——网络错误与超时一律向上抛，
不会被当成「对方不是新协议」。这正是我们需要的语义，因此不自己重写协商逻辑。

取消语义：``asyncio.CancelledError`` 是 ``BaseException`` 的子类，本模块的异常
翻译只捕获 ``Exception``，所以取消信号会原样穿透，不会被折算成一次失败结果。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import mcp
from ghrah.mcp.errors import (
    FailureKind,
    McpCredentialMissingError,
    McpError,
    McpProtocolError,
    McpRequestError,
    classify_rpc_error,
    is_session_terminated,
)
from ghrah.mcp.result import ToolOutcome, project_result
from ghrah.mcp.spec import AUTH_PLACEHOLDER, McpServerSpec, ProtocolPreference
from mcp import types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

Connector = Callable[..., AbstractAsyncContextManager[Any]]
"""建连函数的形状。

抽成可注入参数，是为了让上层在不启动 HTTP 端点的情况下，用进程内服务器验证会话
生命周期（重建、超时、取消）。生产路径使用 :func:`open_client`。
"""

__all__ = [
    "Connector",
    "call_tool_once",
    "list_tool_names",
    "open_client",
    "read_only_hint",
    "resolve_headers",
    "sdk_connect_mode",
    "translate_exception",
]

_PROTOCOL_MODES: dict[ProtocolPreference, str] = {
    ProtocolPreference.MODERN_FIRST: "auto",
    ProtocolPreference.LEGACY: "legacy",
    ProtocolPreference.MODERN: "2026-07-28",
}


def sdk_connect_mode(preference: ProtocolPreference) -> str:
    """把配置里的协商偏好映射成 SDK 的 ``mode`` 取值。

    ``auto`` 对应 SDK 的「先探测后回退」；其余取值为固定语义（握手协议或某个
    具体版本），此时 SDK 不做任何回退。
    """
    try:
        return _PROTOCOL_MODES[preference]
    except KeyError:  # pragma: no cover - 枚举已收敛，防御未来新增值
        raise McpProtocolError(f"unsupported protocol preference: {preference!r}") from None


def resolve_headers(
    spec: McpServerSpec,
    credential: str | None,
) -> dict[str, str]:
    """把配置里的请求头解析成最终发送值。

    只在建连前调用。凭据缺失时抛 :class:`McpCredentialMissingError`——这是
    fail-closed：宁可明确告诉使用者「没配」，也不要发一个注定 401 的请求。
    """
    if spec.requires_credential and not credential:
        raise McpCredentialMissingError(spec.server, spec.url)

    secret = credential or ""
    return {name: value.replace(AUTH_PLACEHOLDER, secret) for name, value in spec.headers.items()}


@asynccontextmanager
async def open_client(
    spec: McpServerSpec,
    *,
    credential: str | None = None,
    read_timeout_s: float | None = None,
) -> AsyncIterator[mcp.Client]:
    """建立一个到 ``spec`` 所声明服务器的连接，yield 已握手完成的客户端。

    ``read_timeout_s`` 是**单次请求**的读超时，不是整次调用的预算；整次调用由上层
    用执行超时包裹。

    连接失败、握手失败都会被翻译成 :class:`McpError` 的子类，``CancelledError``
    原样穿透。
    """
    headers = resolve_headers(spec, credential)
    http_client = create_mcp_http_client(headers=headers or None)

    async with http_client:
        transport = streamable_http_client(spec.url, http_client=http_client)
        client = mcp.Client(
            transport,
            mode=sdk_connect_mode(spec.protocol),
            read_timeout_seconds=read_timeout_s,
        )
        async with client:
            yield client


def translate_exception(exc: Exception) -> McpError:
    """把 SDK 抛出的异常翻译成本模块的失败分类。

    已经是本模块异常的原样返回，便于上层统一处理。``pydantic`` 的校验失败意味着
    对端返回的结果不符合协商出的协议版本，归为协议错误。
    """
    if isinstance(exc, McpError):
        return exc

    if isinstance(exc, mcp.MCPError):
        if is_session_terminated(code=exc.code, message=str(exc.message)):
            # 连接本身可用，但服务端已丢弃该会话：需要重建传输层，且只在只读
            # 调用上才允许重放。
            return McpRequestError(
                str(exc.message), kind=FailureKind.SESSION_TERMINATED, code=exc.code
            )
        return McpRequestError(
            str(exc.message), kind=classify_rpc_error(exc.code, str(exc.message)), code=exc.code
        )

    if isinstance(exc, mcp.InputRequiredRoundsExceededError):
        # 服务器要求补充输入，而我们没有配置任何可用的输入通道。这不是可重试
        # 的失败，重试只会再次要求输入。
        return McpProtocolError(
            "server requested additional input, but no input channel is configured"
        )

    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            return McpProtocolError(f"server response does not match protocol: {exc}")
    except ImportError:  # pragma: no cover - pydantic 是硬依赖
        pass

    # 传输层异常：无法证明请求没有到达服务器，因此按瞬时错误处理；是否重放仍由
    # 调用方按「只读才重放」判定。
    return McpRequestError(str(exc), kind=FailureKind.TRANSIENT)


def read_only_hint(tool: types.Tool) -> bool:
    """取工具的「只读」标注。

    未标注时返回 ``False``：把「不知道」当成「只读」会在写工具上造成重复执行，
    而这个错误无法撤销。
    """
    annotations = tool.annotations
    if annotations is None or annotations.read_only_hint is None:
        return False
    return bool(annotations.read_only_hint)


async def list_tool_names(client: mcp.Client) -> tuple[str, ...]:
    """列出服务器上的全部工具名，跟随分页游标直到取尽。

    仅用于诊断与校验（例如确认配置里声明的工具是否仍然存在）；平时不调用——工具
    清单来自本地配置，不需要联网获取。``max_pages`` 由调用方以超时或取消约束。
    """
    names: list[str] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        names.extend(tool.name for tool in page.tools)
        cursor = page.next_cursor
        if not cursor:
            break
    return tuple(names)


async def call_tool_once(
    client: mcp.Client,
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    read_timeout_s: float | None = None,
) -> ToolOutcome:
    """调用一次工具，并把结果投影成框架可用的形状。

    **工具级失败不会抛异常**：服务器返回的错误（含工具内部异常）都被 SDK 归一成
    ``is_error=True`` 的结果，因此这里必须按结果处理，而不是指望异常。异常只用于
    连接与协议层失败。
    """
    raw = await client.call_tool(
        tool_name,
        dict(arguments or {}),
        read_timeout_seconds=read_timeout_s,
    )
    return project_result(raw)
