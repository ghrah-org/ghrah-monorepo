# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""连接失败时的 HTTP 层取证。

**为什么需要这一层**：MCP Python SDK 在收到非 2xx 响应时，只要响应体不是合法的
JSON-RPC 错误，就会合成一个 ``-32603 Server returned an error response``
（``mcp/client/streamable_http.py`` 的 ``_handle_post_request``）。状态码和响应体
在此期间被丢弃，于是 401、403、404、502 在调用方看来是同一句话：

    失败：McpRequestError: Server returned an error response

排查时只能手工 ``curl`` 一遍才知道究竟哪一层拒了——而 401 与 404 的处置方式
完全不同（换密钥 vs 换地址）。本模块把这个手工步骤自动化：**只在已经失败之后**，
用同样的地址与请求头发一次最小的 ``initialize``，把状态码和响应体开头带回来。

代价是失败路径上多一次请求；它发生在异常之后，不会延长成功路径。探测本身失败
（网络不通、超时）时返回 ``None``，调用方保持原样抛出——取证不改变失败语义。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2

from ghrah.mcp.errors import McpError, McpRequestError
from ghrah.mcp.spec import McpServerSpec

__all__ = [
    "DEFAULT_PROBE_TIMEOUT_S",
    "PROBE_BODY_SNIPPET_CHARS",
    "HttpProbe",
    "enrich_request_error",
    "explain_status",
    "probe_endpoint",
]

logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT_S = 5.0
"""探测的单次超时。取证是尽力而为，不能拖住失败路径。"""

PROBE_BODY_SNIPPET_CHARS = 300
"""带回的响应体长度上限。网关的错误 JSON 通常不到这个量级。"""

_PROBE_PROTOCOL_VERSION = "2025-06-18"
"""探测固定用握手代版本。

刻意**不**跟随 ``spec.protocol``：这里要回答的是「HTTP 层收不收这次请求」，
不是「协议能不能协商」。现代协议的 ``server/discover`` 在小网关上更容易被拦，
会把鉴权失败伪装成协议失败。
"""


@dataclass(frozen=True)
class HttpProbe:
    """一次探测的结果。"""

    status_code: int
    """HTTP 状态码。"""

    content_type: str
    """响应头里的 ``content-type``（可能为空串）。"""

    body_snippet: str
    """响应体开头，已按 :data:`PROBE_BODY_SNIPPET_CHARS` 截断并脱敏。"""

    def summary(self) -> str:
        """拼接成一行给人看的说明。"""
        parts = [f"HTTP {self.status_code}"]
        if self.content_type:
            parts.append(self.content_type)
        if self.body_snippet:
            parts.append(self.body_snippet)
        return " | ".join(parts)


def _truncate(text: str, limit: int = PROBE_BODY_SNIPPET_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def _redact(text: str, secrets: Sequence[str]) -> str:
    """把已知密钥从正文里抹掉。

    网关偶尔会回显一段请求头（含 ``Bearer <key>``），日志与异常消息不该二次泄露。
    """
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text


async def probe_endpoint(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    secrets: Sequence[str] = (),
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> HttpProbe | None:
    """向 ``url`` 发一次最小的 ``initialize``，把 HTTP 状态码与响应体开头带回来。

    Args:
        url: MCP 端点地址。
        headers: 已经解析好的请求头（含真实密钥）。仅用于这一次请求。
        secrets: 需要从响应体中抹掉的明文；通常就是刚用的凭据。
        timeout_s: 单次超时。
        transport: 仅供测试注入（``httpx2.MockTransport``）。

    Returns:
        :class:`HttpProbe`；网络层就失败（连不上、超时）时返回 ``None``。
    """
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": "ghrah-mcp-probe",
        "method": "initialize",
        "params": {
            "protocolVersion": _PROBE_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ghrah-mcp-probe", "version": "0"},
        },
    }
    request_headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    if headers:
        request_headers.update(headers)

    try:
        async with httpx2.AsyncClient(
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
        ) as http:
            response = await http.post(url, json=payload, headers=request_headers)
    except httpx2.HTTPError:
        logger.debug("MCP endpoint probe failed to reach %s", url, exc_info=True)
        return None

    body = _redact(_truncate(response.text or ""), secrets)
    return HttpProbe(
        status_code=response.status_code,
        content_type=response.headers.get("content-type", ""),
        body_snippet=body,
    )


def explain_status(status: int) -> str:
    """把状态码翻译成一句可执行的下一步。非错误码返回空串。"""
    if status in (401, 403):
        return (
            "鉴权被拒。常见原因：密钥与端点不属于同一平台（例如 Token Plan 的 key "
            "打不开百炼的 MCP 网关），或凭据没有写进请求头。"
        )
    if status == 404:
        return "地址不存在。检查 MCP 端点路径与主机名是否正确。"
    if status == 405:
        return "方法不被允许。该地址可能只接受 GET（旧版 HTTP+SSE 端点），不是 Streamable HTTP。"
    if status == 415:
        return "请求体类型不被接受。网关可能要求不同的 content-type。"
    if status in (408, 425, 429):
        return "网关超时或限流。可以退避后重试。"
    if 500 <= status <= 599:
        return "服务端错误。稍后重试；持续失败联系服务提供方。"
    return ""


async def enrich_request_error(
    error: McpError,
    *,
    spec: McpServerSpec,
    headers: Mapping[str, str],
    secrets: Sequence[str] = (),
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> McpError:
    """给「不知道失败的 HTTP 原因」的请求错误补上探测结果。

    只处理 :class:`McpRequestError` 且尚未带状态码的情形；其余原样返回。探测失败
    时也原样返回——取证失败不该掩盖原始错误。
    """
    if not isinstance(error, McpRequestError) or error.http_status is not None:
        return error

    probe = await probe_endpoint(
        spec.url,
        headers=headers,
        secrets=secrets,
        timeout_s=timeout_s,
    )
    if probe is None:
        return error
    return error.with_http_probe(
        status=probe.status_code,
        body_snippet=probe.body_snippet or None,
    )
