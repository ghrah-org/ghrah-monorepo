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
from dataclasses import dataclass
from typing import Any

import mcp
from ghrah.mcp.diagnostics import enrich_request_error
from ghrah.mcp.errors import (
    FailureKind,
    McpConfigError,
    McpCredentialMissingError,
    McpError,
    McpProtocolError,
    McpRequestError,
    classify_rpc_error,
    is_session_terminated,
)
from ghrah.mcp.result import ToolOutcome, project_result
from ghrah.mcp.spec import (
    AUTH_PLACEHOLDER,
    DEFAULT_MAX_PAGES,
    McpServerSpec,
    ProtocolPreference,
)
from mcp import types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

Connector = Callable[..., AbstractAsyncContextManager[Any]]
"""建连函数的形状。

抽成可注入参数，是为了让上层在不启动 HTTP 端点的情况下，用进程内服务器验证会话
生命周期（重建、超时、取消）。生产路径使用 :func:`open_client`。
"""

__all__ = [
    "Connector",
    "RemoteTool",
    "call_tool_once",
    "list_endpoint_tools",
    "list_tool_names",
    "list_tools",
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
        try:
            async with client:
                yield client
        except Exception as exc:  # noqa: BLE001 - 统一翻译后再补 HTTP 取证
            error = translate_exception(exc)
            # SDK 丢掉了非 2xx 的状态码，握手失败时再探一次，把 401/404 区分出来。
            enriched = await enrich_request_error(
                error,
                spec=spec,
                headers=headers,
                secrets=(credential,) if credential else (),
            )
            raise enriched from exc


def unwrap_exception_group(exc: BaseException) -> BaseException:
    """剥掉 anyio TaskGroup 的 ``ExceptionGroup``，拿到最内层的真实异常。

    SDK 的传输层把 ``ClientSession`` 跑在任务组里，失败时抛出的是一层
    ``ExceptionGroup``（消息只有 ``unhandled errors in a TaskGroup``）。如果不剥，
    失败分类只能落到兜底的「瞬时错误」，真正的协议错误码与消息全被埋掉。
    """
    current: BaseException = exc
    while True:
        nested = getattr(current, "exceptions", None)
        if not nested:
            return current
        current = nested[0]


def translate_exception(exc: Exception) -> McpError:
    """把 SDK 抛出的异常翻译成本模块的失败分类。

    已经是本模块异常的原样返回，便于上层统一处理。``pydantic`` 的校验失败意味着
    对端返回的结果不符合协商出的协议版本，归为协议错误。
    """
    unwrapped = unwrap_exception_group(exc)
    if isinstance(unwrapped, Exception):
        exc = unwrapped

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


@dataclass(frozen=True)
class RemoteTool:
    """服务器在 ``tools/list`` 里自报的一件工具。

    这是**远端真源的一次快照**，与 :class:`ghrah.mcp.spec.McpToolSpec`（本地白名单
    配置）不是同一样东西：不做名字、描述、schema 的校验，服务器说什么就记什么。
    用途是核对本地快照是否过期，或为一个陌生端点起草配置。
    """

    name: str
    """服务器上的工具名，原样保留，不做归一化。"""

    description: str
    """服务器给的用途说明；服务器未提供时为空串。"""

    input_schema: Mapping[str, Any]
    """参数 JSON Schema，服务器原样返回，不整形、不截断。"""

    read_only: bool
    """服务器的 ``readOnlyHint``；未标注时为 ``False``（不知道就不当只读）。"""


def _remote_tool(tool: types.Tool) -> RemoteTool:
    """把 SDK 的工具声明投影成本模块的形状。"""
    return RemoteTool(
        name=tool.name,
        description=tool.description or "",
        input_schema=tool.input_schema,
        read_only=read_only_hint(tool),
    )


async def list_tools(
    client: mcp.Client,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[RemoteTool, ...]:
    """列出服务器上的全部工具，跟随分页游标直到取尽。

    与 :func:`list_tool_names` 只差「带回说明与参数 schema」，两者都只用于诊断与
    校验——运行时可见的工具清单来自本地配置，不走网络。

    ``max_pages`` 是防跑飞的上限：服务器若一直回 ``next_cursor``，取到该页数仍不
    收敛就抛 :class:`McpProtocolError`，而不是把一次诊断变成无界循环。单页超时由
    建连时传入的 ``read_timeout_s`` 约束。
    """
    if max_pages < 1:
        raise McpConfigError(f"max_pages must be positive, got {max_pages}")

    tools: list[RemoteTool] = []
    cursor: str | None = None
    for _ in range(max_pages):
        page = await client.list_tools(cursor=cursor)
        tools.extend(_remote_tool(tool) for tool in page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tuple(tools)
    raise McpProtocolError(
        f"server returned more than {max_pages} pages of tools; "
        "pass a larger max_pages to keep paging"
    )


async def list_tool_names(
    client: mcp.Client,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[str, ...]:
    """列出服务器上的全部工具名，跟随分页游标直到取尽。

    只取名字；需要说明或参数 schema 时用 :func:`list_tools`。仅用于诊断与校验
    （例如确认配置里声明的工具是否仍然存在）；平时不调用——工具清单来自本地配置，
    不需要联网获取。
    """
    return tuple(tool.name for tool in await list_tools(client, max_pages=max_pages))


_ENDPOINT_PROBE_SKILL = "ghrah.mcp.endpoint"
"""临时声明用的技能名。

不是任何真实技能，只为满足 :class:`McpServerSpec` 的非空约束；这份声明不登记到
任何运行时，调用结束即丢弃。
"""

_ENDPOINT_PROBE_SERVER = "endpoint"
"""临时声明用的服务器名，仅出现在错误信息里。"""


async def list_endpoint_tools(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    credential: str | None = None,
    protocol: ProtocolPreference = ProtocolPreference.MODERN_FIRST,
    read_timeout_s: float | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[RemoteTool, ...]:
    """连上 ``url``，取回该端点 ``tools/list`` 的完整清单。

    这是「手上只有一个地址」时的入口：不需要配置文件、技能或运行时——建一次连接、
    取尽清单、断开。核对配置快照是否过期、给一个陌生端点起草配置都用它。

    Args:
        url: MCP 端点地址。http(s)、不含内嵌凭据，走与配置同一套校验。
        headers: 请求头。可以只放字面值（例如 ``Bearer <key>``），也可以放一个
            :data:`ghrah.mcp.spec.AUTH_PLACEHOLDER` 占位符，由 ``credential`` 填充。
            走占位符更安全：失败后的 HTTP 探测只对 ``credential`` 做脱敏，字面密钥
            会被原样带进错误信息。
        credential: 占位符的替换值。请求头里没有占位符时忽略。
        protocol: 协议协商偏好，默认「先按无状态探测、失败再回退握手」。
        read_timeout_s: 单次请求读超时；``None`` 用 SDK 默认值。
        max_pages: 分页上限，语义见 :func:`list_tools`。

    Returns:
        按服务器返回顺序排列的工具，已跟完所有分页。

    Raises:
        McpError: 建连、协商或分页失败。请求头引用了占位符却没有 ``credential``
            时，:class:`McpCredentialMissingError` 在**建连之前**就抛出（文案提示
            去 agentconf 登记，那正是凭据的唯一来源；临时调用也可以直接传
            ``credential``，不必写占位符，改放字面值即可）。
        McpConfigError: ``url`` / ``headers`` 不合法，或 ``max_pages`` 非正。

    ``asyncio.CancelledError`` 不会被翻译，原样穿透。

    实现上构造一份瞬态 :class:`McpServerSpec`，只为复用同一套校验（地址 scheme 与
    主机、请求头、占位符唯一性），不代表任何技能，也不会进入任何激活集合。
    """
    spec = McpServerSpec(
        skill_full_name=_ENDPOINT_PROBE_SKILL,
        server=_ENDPOINT_PROBE_SERVER,
        url=url,
        headers=dict(headers or {}),
        protocol=protocol,
    )
    async with open_client(
        spec,
        credential=credential,
        read_timeout_s=read_timeout_s,
    ) as client:
        return await list_tools(client, max_pages=max_pages)


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
