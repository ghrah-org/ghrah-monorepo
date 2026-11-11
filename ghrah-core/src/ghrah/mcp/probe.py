# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""按 agentconf 服务器名做一次只读探测。

探测建立独立短会话：握手之后可选列出工具与资源。它只接受服务器名，
不接受地址、请求头或凭据；不调用工具，不读取资源内容，也不触碰运行中
Agent 的会话。分类后的失败写入报告，不向外抛出。取消信号原样穿透。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ghrah.mcp.agentconf_source import AgentconfMcpSource
from ghrah.mcp.client import (
    Connector,
    list_resource_templates,
    list_resources,
    list_tools,
    open_client,
    server_supports_resources,
    translate_exception,
)
from ghrah.mcp.diagnostics import explain_status, redact_secrets
from ghrah.mcp.errors import (
    ERR_METHOD_NOT_FOUND,
    FailureKind,
    McpConfigError,
    McpConnectionError,
    McpCredentialMissingError,
    McpError,
    McpProtocolError,
    McpRequestError,
    McpServerNotConfiguredError,
    McpUnsupportedTransportError,
)
from ghrah.mcp.spec import McpServerSpec

logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT_MS = 5_000
MIN_PROBE_TIMEOUT_MS = 1_000
MAX_PROBE_TIMEOUT_MS = 10_000
_PROBE_SKILL_NAME = "diagnostic/ui-probe"

_AGENTCONF_UNAVAILABLE = frozenset(
    {
        "agentconf MCP 配置不可用；请检查版本和显式迁移状态",
        "MCP Provider 接线需要 agentconf 0.4／schema 6",
    }
)

__all__ = [
    "DEFAULT_PROBE_TIMEOUT_MS",
    "MAX_PROBE_TIMEOUT_MS",
    "MIN_PROBE_TIMEOUT_MS",
    "McpProbeFailure",
    "McpProbeReport",
    "McpResourceInfo",
    "probe_server",
]


@dataclass(frozen=True)
class McpProbeFailure:
    """一次探测失败的分类结果。``detail`` 已脱敏，只供日志，不进响应。"""

    reason: str
    http_status: int | None = None
    hint: str = ""
    failure_kind: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class McpResourceInfo:
    """一条远端资源的清单字段，不含资源正文。"""

    uri: str
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class McpProbeReport:
    """一次只读探测的结果。成功与失败都用这份结构返回。"""

    ok: bool
    phase: str
    elapsed_ms: int
    transport: str = "streamable_http"
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    tools: tuple[dict[str, Any], ...] = ()
    resources_supported: bool = False
    resources: tuple[McpResourceInfo, ...] = ()
    resource_templates: tuple[dict[str, Any], ...] = ()
    failure: McpProbeFailure | None = None


def _clamp_timeout_ms(timeout_ms: int) -> int:
    return min(MAX_PROBE_TIMEOUT_MS, max(MIN_PROBE_TIMEOUT_MS, timeout_ms))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _secret_values(spec: McpServerSpec, credential: str | None) -> tuple[str, ...]:
    values: list[str] = []
    if credential:
        values.append(credential)
    if spec.url:
        values.append(spec.url)
    for value in spec.headers.values():
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _resource_info(item: dict[str, Any]) -> McpResourceInfo:
    size = item.get("size")
    return McpResourceInfo(
        uri=str(item["uri"]),
        name=str(item["name"]),
        title=item.get("title"),
        description=item.get("description"),
        mime_type=item.get("mime_type"),
        size=size if isinstance(size, int) and not isinstance(size, bool) else None,
    )


def _method_not_found(exc: Exception) -> bool:
    error = exc if isinstance(exc, McpError) else translate_exception(exc)
    return isinstance(error, McpRequestError) and error.code == ERR_METHOD_NOT_FOUND


def _as_mcp_error(exc: Exception) -> Exception:
    if isinstance(exc, McpError):
        return exc
    return translate_exception(exc)


def _reason_for_status(status: int) -> str:
    if status in (401, 403):
        return "auth_failed"
    if status == 404:
        return "endpoint_not_found"
    if status == 405:
        return "method_not_allowed"
    if status in (408, 425, 429):
        return "rate_limited"
    if 500 <= status <= 599:
        return "server_error"
    return "internal"


def _classify(
    exc: Exception, *, phase: str, connected: bool
) -> tuple[str, str, int | None, str | None]:
    if isinstance(exc, McpServerNotConfiguredError):
        return "server_not_configured", "config", None, None
    if isinstance(exc, McpCredentialMissingError):
        return "credential_missing", "config", None, None
    if isinstance(exc, McpUnsupportedTransportError):
        return "unsupported_transport", "config", None, None
    if isinstance(exc, McpConfigError):
        if str(exc) in _AGENTCONF_UNAVAILABLE:
            return "agentconf_unavailable", "config", None, None
        return "config_invalid", "config", None, None
    if isinstance(exc, McpProtocolError):
        message = str(exc)
        if message.startswith("MCP SDK unavailable"):
            return "sdk_unavailable", "config", None, None
        if "exceeds size limit" in message or "exceeds page limit" in message:
            return "too_large", phase, None, FailureKind.PERMANENT.value
        return "protocol_error", phase, None, FailureKind.PERMANENT.value
    if isinstance(exc, McpRequestError) and exc.http_status is not None:
        failure_phase = "initialize" if not connected and phase == "connect" else phase
        return (
            _reason_for_status(exc.http_status),
            failure_phase,
            exc.http_status,
            exc.kind.value,
        )
    if not connected and (
        isinstance(exc, McpConnectionError)
        or (
            isinstance(exc, McpRequestError)
            and exc.kind is FailureKind.TRANSIENT
            and exc.http_status is None
        )
    ):
        kind = exc.kind.value if isinstance(exc, McpRequestError) else FailureKind.TRANSIENT.value
        return "connect_failed", "connect", None, kind
    if isinstance(exc, McpRequestError):
        return "internal", phase, exc.http_status, exc.kind.value
    if isinstance(exc, McpConnectionError):
        where = "connect" if not connected else phase
        return "connect_failed", where, None, FailureKind.TRANSIENT.value
    return "internal", phase, None, None


def _emit(name: str, report: McpProbeReport) -> McpProbeReport:
    if report.failure is None:
        logger.info("mcp probe server=%s", name)
    else:
        logger.info("mcp probe server=%s reason=%s", name, report.failure.reason)
    return report


def _failure_report(
    *,
    name: str,
    started: float,
    phase: str,
    exc: Exception,
    secrets: Sequence[str],
    connected: bool,
    tools: tuple[dict[str, Any], ...],
    resources: tuple[McpResourceInfo, ...],
    resource_templates: tuple[dict[str, Any], ...],
    resources_supported: bool,
    protocol_version: str | None,
    server_name: str | None,
    server_version: str | None,
    timeout: bool = False,
) -> McpProbeReport:
    if timeout:
        reason, failure_phase, http_status, failure_kind = ("timeout", phase, None, "transient")
    else:
        reason, failure_phase, http_status, failure_kind = _classify(
            exc, phase=phase, connected=connected
        )
    report = McpProbeReport(
        ok=False,
        phase=failure_phase,
        elapsed_ms=_elapsed_ms(started),
        protocol_version=protocol_version,
        server_name=server_name,
        server_version=server_version,
        tools=tools,
        resources_supported=resources_supported,
        resources=resources,
        resource_templates=resource_templates,
        failure=McpProbeFailure(
            reason=reason,
            http_status=http_status,
            hint=explain_status(http_status) if http_status is not None else "",
            failure_kind=failure_kind,
            detail=redact_secrets(str(exc), secrets),
        ),
    )
    return _emit(name, report)


async def _read_resources(
    client: Any, spec: McpServerSpec
) -> tuple[bool, tuple[McpResourceInfo, ...], tuple[dict[str, Any], ...]]:
    """列出资源与模板。不支持或方法不存在时返回空清单，其它错误继续抛出。"""
    if not server_supports_resources(client):
        return False, (), ()
    try:
        listed = await list_resources(
            client, max_pages=spec.max_pages, max_bytes=spec.max_result_bytes
        )
    except TimeoutError:
        raise
    except Exception as exc:
        if _method_not_found(exc):
            return False, (), ()
        raise _as_mcp_error(exc) from exc
    resources = tuple(_resource_info(item) for item in listed)
    try:
        listed_templates = await list_resource_templates(
            client, max_pages=spec.max_pages, max_bytes=spec.max_result_bytes
        )
    except TimeoutError:
        raise
    except Exception as exc:
        if _method_not_found(exc):
            return True, resources, ()
        raise _as_mcp_error(exc) from exc
    return True, resources, tuple(listed_templates)


async def probe_server(
    name: str,
    *,
    db_path: str | Path | None = None,
    timeout_ms: int = DEFAULT_PROBE_TIMEOUT_MS,
    include_tools: bool = True,
    include_resources: bool = False,
    connector: Connector | None = None,
) -> McpProbeReport:
    """按已配置的服务器名做一次独立短会话探测。

    ``timeout_ms`` 会再夹到 1–10 秒。``include_resources`` 缺省关闭：关闭时
    不读取资源能力，也不调用资源方法。凭据与地址只出现在已脱敏的 ``detail`` 里。
    """
    started = time.monotonic()
    phase = "config"
    connected = False
    secrets: tuple[str, ...] = ()
    tools: tuple[dict[str, Any], ...] = ()
    resources: tuple[McpResourceInfo, ...] = ()
    resource_templates: tuple[dict[str, Any], ...] = ()
    resources_supported = False
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    timeout_s = _clamp_timeout_ms(timeout_ms) / 1000
    source = AgentconfMcpSource(db_path=db_path)
    try:
        try:
            async with asyncio.timeout(timeout_s):
                if name not in source.names():
                    raise McpServerNotConfiguredError(name)
                spec = source.connection_spec(name, skill_full_name=_PROBE_SKILL_NAME)
                secrets = _secret_values(spec, None)
                credential = source.credential_for_spec(spec)
                headers = source.headers_for_spec(spec)
                if headers is not None:
                    spec = replace(spec, headers=headers)
                secrets = _secret_values(spec, credential)
                phase = "connect"
                read_timeout_s = min(spec.connect_timeout_ms / 1000, timeout_s)
                connect = connector or open_client
                async with connect(
                    spec, credential=credential, read_timeout_s=read_timeout_s
                ) as client:
                    connected = True
                    phase = "initialize"
                    protocol_version = _text(getattr(client, "protocol_version", None))
                    info = getattr(client, "server_info", None)
                    if info is not None:
                        server_name = _text(getattr(info, "name", None))
                        server_version = _text(getattr(info, "version", None))
                    if include_tools:
                        phase = "list_tools"
                        tools = tuple(
                            await list_tools(
                                client,
                                max_pages=spec.max_pages,
                                max_bytes=spec.max_result_bytes,
                            )
                        )
                    if include_resources:
                        phase = "list_resources"
                        resources_supported, resources, resource_templates = await _read_resources(
                            client, spec
                        )
            return _emit(
                name,
                McpProbeReport(
                    ok=True,
                    phase=phase,
                    elapsed_ms=_elapsed_ms(started),
                    protocol_version=protocol_version,
                    server_name=server_name,
                    server_version=server_version,
                    tools=tools,
                    resources_supported=resources_supported,
                    resources=resources,
                    resource_templates=resource_templates,
                ),
            )
        except TimeoutError as exc:
            return _failure_report(
                name=name,
                started=started,
                phase=phase,
                exc=exc,
                secrets=secrets,
                connected=connected,
                tools=tools,
                resources=resources,
                resource_templates=resource_templates,
                resources_supported=resources_supported,
                protocol_version=protocol_version,
                server_name=server_name,
                server_version=server_version,
                timeout=True,
            )
        except Exception as exc:
            error = exc if isinstance(exc, McpError) else translate_exception(exc)
            return _failure_report(
                name=name,
                started=started,
                phase=phase,
                exc=error,
                secrets=secrets,
                connected=connected,
                tools=tools,
                resources=resources,
                resource_templates=resource_templates,
                resources_supported=resources_supported,
                protocol_version=protocol_version,
                server_name=server_name,
                server_version=server_version,
            )
    finally:
        source.close()
