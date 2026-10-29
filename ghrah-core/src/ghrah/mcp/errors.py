# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP 调用的错误分类。

分类的目的不是描述错误，而是**决定能不能重试**。MCP 的写操作没有回滚，所以
「看起来可以重试」的失败信号并不可信：代理可能在**上游已经执行完之后**才伪造
一个会话过期错误。因此本模块的默认倾向是**不可重试**，只有被明确判定为
「可安全重放」的情形才放行。

四种判定结果对应四种不同的处置：

``BAD_ARGUMENTS``
    参数不合法。**既不是永久错误也不是瞬时错误**——重试同样的参数永远失败，
    退避重试只会浪费时间；正确动作是把服务器的原文回灌给模型，让它改参数。

``SESSION_TERMINATED``
    服务端已经不认这个会话（重启或空闲回收）。连接本身没问题，需要丢弃
    session id 并重建传输层。是否重放由调用是否只读决定。

``TRANSIENT``
    网络抖动、超时、限流、5xx。可重试，但同样只在只读调用上自动重放。

``PERMANENT``
    其余一切，包括未知的 JSON-RPC 错误码。**默认落到这里**，因为把一个
    未知错误当成可重试，代价是重复的副作用；当成不可重试，代价只是一次
    失败提示。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ERR_CONNECTION_CLOSED",
    "ERR_INVALID_PARAMS",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_PARSE_ERROR",
    "ERR_SESSION_NOT_FOUND",
    "SESSION_TERMINATED_MESSAGE",
    "FailureKind",
    "McpConfigError",
    "McpConnectionError",
    "McpCredentialMissingError",
    "McpError",
    "McpNotActivatedError",
    "McpProtocolError",
    "McpRequestError",
    "McpToolMissingError",
    "classify_http_status",
    "classify_rpc_error",
    "is_session_terminated",
    "should_replay",
]

ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
"""JSON-RPC 标准错误码。``-32602`` 是参数校验失败，必须与网络错误区分开。"""

ERR_CONNECTION_CLOSED = -32000
"""连接已关闭。属实现自定义区间，各实现取值不一，仅作参考识别。"""

ERR_SESSION_NOT_FOUND = -32001
"""TypeScript 实现用于「会话不存在」的码。Python 实现用 ``-32600`` 加固定文案，
两个都要认，否则换一个服务端实现就漏判。"""

SESSION_TERMINATED_MESSAGE = "Session terminated"
"""Python SDK 在 http 404 时生成的通知文案。

这不是我们猜的文本：它是 SDK 传输层固定生成的字符串，因此可以被当作稳定信号。
相比逐个匹配服务端可能返回的多种措辞（"invalid or expired session"、
"broken pipe" 等），只认这一个来源明确的信号更可靠、也更少误判。
"""


class FailureKind(StrEnum):
    """一次请求失败的可重试性判定。"""

    BAD_ARGUMENTS = "bad_arguments"
    SESSION_TERMINATED = "session_terminated"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class McpError(Exception):
    """本模块所有异常的基类。"""


class McpConfigError(McpError):
    """配置或规格非法。在建连之前就该被拦下。"""


class McpNotActivatedError(McpError):
    """调用的服务器/工具不在调用方的已激活集合或白名单内。

    这是权限判定失败，不是配置错误——调用方没有资格知道服务器上到底有什么，
    所以本异常**不带**可用工具列表。
    """


class McpToolMissingError(McpError):
    """工具在该服务器上不存在。

    与 :class:`McpNotActivatedError` 分开：这里是「有资格调，但名字写错了」，
    因此可以安全地回灌候选列表帮模型自我纠正。
    """

    def __init__(self, server: str, tool: str, available: tuple[str, ...] = ()) -> None:
        self.server = server
        self.tool = tool
        self.available = tuple(available)
        detail = f"Unknown tool {tool!r} on server {server!r}."
        if self.available:
            detail += " Available: " + ", ".join(self.available) + "."
        super().__init__(detail)


class McpCredentialMissingError(McpError):
    """该服务器未登记凭据，但配置里引用了凭据占位符。

    刻意在建连之前抛出，而不是让它变成 401：401 会被使用者和模型误读成
    「凭证错了」，而实际问题是「还没配」。文案需要给出可执行的下一步。

    凭据登记在 agentconf 里，用 MCP 服务器的地址与 provider 记录的 ``base_url``
    关联，因此文案要说明的是"去 agentconf 加一条记录"，不是一个 ghrah 子命令。
    """

    def __init__(self, server: str, url: str) -> None:
        self.server = server
        self.url = url
        super().__init__(
            f"No credential registered for MCP endpoint {url} "
            f"(server {server!r}). Register a provider in agentconf whose "
            f"base_url is {url} and whose api_key holds the secret."
        )


class McpConnectionError(McpError):
    """建连失败或连接中断。上层可据此做有界重连。"""


class McpRequestError(McpError):
    """请求已发出但失败。携带判定结果，供上层决定是否重放。"""

    def __init__(
        self,
        message: str,
        *,
        kind: FailureKind = FailureKind.PERMANENT,
        code: int | None = None,
        uncertain: bool = False,
        http_status: int | None = None,
        body_snippet: str | None = None,
    ) -> None:
        self.kind = kind
        self.code = code
        self.uncertain = uncertain
        """``True`` 表示**这次写操作可能已经在服务器上生效**。

        出现于失败信号不可信的情形：连接在回包之前断开、会话被丢弃、请求超时。
        此时不能自动重放，且必须提示先核验外部状态再决定下一步——重放一个已经
        生效的写操作，其后果无法撤销。
        """
        self.http_status = http_status
        """失败时探测到的 HTTP 状态码；``None`` 表示没探到（或没探）。

        SDK 会把所有非 2xx 响应折叠成 ``-32603 Server returned an error response``
        并丢掉状态码，401 与 502 在异常文本上无法区分。本字段由
        :mod:`ghrah.mcp.diagnostics` 在失败后补上。
        """
        self.body_snippet = body_snippet
        """响应体开头（已截断、已脱敏），通常正是网关给出的错误码与提示。"""
        super().__init__(message)

    def with_http_probe(self, *, status: int, body_snippet: str | None) -> McpRequestError:
        """带上探测结果复制一份，其余判定不变。

        判定（``kind``/``uncertain``）**不因状态码而改变**：判定已经按「不可重试
        优先」的原则做过，事后追加的状态码只用于解释失败原因，不该让一次写调用
        突然变得可以自动重放。
        """
        detail = f"{self} (HTTP {status}"
        if body_snippet:
            detail += f": {body_snippet}"
        detail += ")"
        return McpRequestError(
            detail,
            kind=self.kind,
            code=self.code,
            uncertain=self.uncertain,
            http_status=status,
            body_snippet=body_snippet,
        )


class McpProtocolError(McpError):
    """协议层不兼容：版本无法协商、必要能力缺失等。"""


def is_session_terminated(*, code: int | None, message: str) -> bool:
    """判断一个 JSON-RPC 错误是否表示「服务端已丢弃该会话」。

    只认两个来源明确的信号，不做模糊文本匹配：

    * Python SDK 生成的 ``-32600`` + ``"Session terminated"``；
    * TypeScript 实现使用的 ``-32001``。

    刻意**不**匹配 ``broken pipe`` / ``EOF`` / ``ClosedResourceError`` 这类传输层
    症状：它们常在一次调用的**回包中途**触发，此时上游可能已经执行完毕，把它们
    当成「会话过期可重放」会导致重复副作用。这类错误应落到 ``TRANSIENT``，并且
    同样只在只读调用上重放。
    """
    if code == ERR_SESSION_NOT_FOUND:
        return True
    return code == ERR_INVALID_REQUEST and SESSION_TERMINATED_MESSAGE in message


def classify_rpc_error(code: int, message: str) -> FailureKind:
    """按 JSON-RPC 错误码判定可重试性。"""
    if is_session_terminated(code=code, message=message):
        return FailureKind.SESSION_TERMINATED
    if code == ERR_INVALID_PARAMS:
        # 参数错要模型改参数，退避重试无意义。
        return FailureKind.BAD_ARGUMENTS
    if code == ERR_CONNECTION_CLOSED:
        return FailureKind.TRANSIENT
    # -32601（方法不存在）、-32600（请求非法）、-32700（解析失败）以及任何
    # 未知的应用级错误，一律按不可重试处理。
    return FailureKind.PERMANENT


def classify_http_status(status: int, *, has_session_id: bool) -> FailureKind:
    """按 HTTP 状态码判定可重试性。

    ``has_session_id`` 是**必须先判断的前置条件**：只有当前传输层确实持有会话
    标识时，``404`` 才意味着「会话被丢弃」。现代无状态协议没有会话标识，它的
    ``404`` 表示路径写错了，误判成会话过期会引发无意义的重建。
    """
    if status in (401, 403):
        return FailureKind.PERMANENT
    if status == 404:
        return FailureKind.SESSION_TERMINATED if has_session_id else FailureKind.PERMANENT
    if status in (408, 425, 429) or 500 <= status <= 599:
        return FailureKind.TRANSIENT
    return FailureKind.PERMANENT


def should_replay(kind: FailureKind, *, read_only: bool) -> bool:
    """判断失败后能否自动重放该请求。

    ``read_only`` 取自工具的 ``readOnlyHint``。**未标注时按可写处理**——把
    「不知道」当成「只读」会在写工具上造成重复执行，而这个错误无法撤销。
    """
    if kind is FailureKind.SESSION_TERMINATED or kind is FailureKind.TRANSIENT:
        return read_only
    return False
