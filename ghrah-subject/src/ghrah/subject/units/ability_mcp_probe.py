# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把已安装 MCP 能力包的一次只读探测编排成管理面视图。

职责只有四件：从安装存储解析包定义、取出 agentconf 服务器名、施加上限后调用
Core 探测、把报告映射成协议视图。它不碰 HTTP 路由、不触碰运行中 Agent 的
会话，也不写任何状态。

上限有三条：

- 同一服务器单飞：已有探测在飞时立即返回 ``probe_in_progress``，不排队；
- 全局并发 2：已满时立即返回 ``probe_busy``，不排队；
- 超时夹逼到 ``[1000, 10000]`` 毫秒，并在 Core 自身预算之外再加一层
  ``asyncio.timeout`` 整体包裹，防止 Core 内部预算失效时无限等待。

安全边界：视图只含服务器名与远端清单，不含 URL、请求头、凭据、绝对路径或
对端响应体。失败文案是本模块的固定中文；Core 报告里已脱敏的 ``detail`` 只
用于日志，绝不进入响应。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, cast

from ghrah.ability.loading.ability_schema import AbilityDefinition
from ghrah.ability.package.installer import AbilityInstaller, InstallerError
from ghrah.mcp.probe import (
    MAX_PROBE_TIMEOUT_MS,
    MIN_PROBE_TIMEOUT_MS,
    McpProbeFailure,
    McpProbeReport,
    probe_server,
)
from ghrah.protocol.ability_control import (
    AbilityMcpProbeErrorView,
    AbilityMcpProbePhase,
    AbilityMcpProbeReason,
    AbilityMcpProbeRequest,
    AbilityMcpProbeView,
    AbilityMcpResourceView,
    AbilityMcpServerInfoView,
    AbilityMcpToolView,
)

from ghrah.subject.units.ability_catalog import CatalogError

__all__ = ["probe_package_mcp"]

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_PROBES = 2
"""全局同时探测上限；超出的请求立即得到 ``probe_busy``，不排队等待。"""

_TIMEOUT_MARGIN_MS = 500
"""``asyncio.timeout`` 整体预算在 Core 预算之外留的余量。"""

_NOT_MCP = "definition_not_mcp"
"""非 MCP 包的错误消息；管理面据此映射 409 与 ``detail.reason``。"""

_PHASES: frozenset[str] = frozenset(
    {"config", "connect", "initialize", "list_tools", "list_resources"}
)

_FAILURE_MESSAGES: dict[str, str] = {
    "config_invalid": "MCP 服务配置不合法；请检查服务器名与传输方式。",
    "server_not_configured": "MCP 服务器未在 agentconf 中配置。",
    "credential_missing": "MCP 服务器缺少凭据。",
    "unsupported_transport": "MCP 服务器使用了不支持的传输方式；仅支持 streamable_http。",
    "sdk_unavailable": "运行环境缺少 MCP SDK。",
    "agentconf_unavailable": "agentconf 的 MCP 配置不可用。",
    "connect_failed": "无法连接到 MCP 服务器。",
    "auth_failed": "MCP 服务器拒绝了鉴权。",
    "endpoint_not_found": "MCP 端点不存在。",
    "method_not_allowed": "MCP 端点不接受该方法。",
    "rate_limited": "MCP 端点限流或超时，请稍后重试。",
    "server_error": "MCP 服务端返回错误。",
    "timeout": "MCP 探测超时。",
    "protocol_error": "MCP 协议握手或清单响应不合法。",
    "too_large": "MCP 工具或资源清单超出大小上限。",
    "probe_in_progress": "该 MCP 服务器已有探测在进行。",
    "probe_busy": "当前探测并发已达上限，请稍后重试。",
    "internal": "MCP 探测内部错误。",
}
"""封闭 ``reason`` → 固定中文文案；不拼接任何远端或配置内容。"""

_FALLBACK_HINTS: dict[str, str] = {
    "timeout": "可稍后重试，或降低查询范围后重试。",
    "probe_in_progress": "等待当前探测结束后再试。",
    "probe_busy": "稍后重试即可。",
}
"""Core 未给出 hint 时的固定补充提示；同样不含敏感信息。"""


class _ProbeGate:
    """同一服务器单飞与全局并发上限。

    只在事件循环线程里使用：``try_enter`` 与 ``leave`` 都是同步方法，检查与
    占位之间没有 ``await``，因此天然原子，不需要额外锁。进入失败只返回原因，
    由调用方立刻回一个失败视图，绝不排队。
    """

    def __init__(self, *, max_concurrent: int = _MAX_CONCURRENT_PROBES) -> None:
        self._max_concurrent = max_concurrent
        self._servers: set[str] = set()
        self._active = 0

    def try_enter(self, server: str) -> AbilityMcpProbeReason | None:
        """占一个探测位；返回 ``None`` 表示进入成功，否则返回立即失败的原因。"""
        if server in self._servers:
            return "probe_in_progress"
        if self._active >= self._max_concurrent:
            return "probe_busy"
        self._servers.add(server)
        self._active += 1
        return None

    def leave(self, server: str) -> None:
        """释放探测位；与 :meth:`try_enter` 成对，重复释放不会把计数压成负数。"""
        self._servers.discard(server)
        if self._active > 0:
            self._active -= 1


_gate = _ProbeGate()
"""进程级探测闸门；测试用 ``monkeypatch`` 替换为独立实例以隔离用例。"""


def _clamp_timeout_ms(timeout_ms: int) -> int:
    """把超时再夹到 Core 的口径 ``[1000, 10000]``。"""
    return min(MAX_PROBE_TIMEOUT_MS, max(MIN_PROBE_TIMEOUT_MS, timeout_ms))


def _safe_phase(phase: str) -> AbilityMcpProbePhase:
    """收窄 Core 报告的阶段；未知阶段按 ``config`` 处理，不让 DTO 抛错。"""
    return cast(AbilityMcpProbePhase, phase if phase in _PHASES else "config")


def _safe_reason(reason: str) -> AbilityMcpProbeReason:
    """收窄失败分类；未知分类归入 ``internal``，保持封闭枚举。"""
    return cast(AbilityMcpProbeReason, reason if reason in _FAILURE_MESSAGES else "internal")


def _error_view(
    reason: AbilityMcpProbeReason,
    *,
    http_status: int | None = None,
    hint: str = "",
) -> AbilityMcpProbeErrorView:
    """构造固定文案的错误视图；``hint`` 只接受 Core 的固定提示或本模块兜底。"""
    return AbilityMcpProbeErrorView(
        reason=reason,
        message=_FAILURE_MESSAGES[reason],
        http_status=http_status,
        hint=hint or _FALLBACK_HINTS.get(reason),
    )


def _server_info(name: str | None, version: str | None) -> AbilityMcpServerInfoView | None:
    """两端身份都为空时不给结构，避免前端渲染一个空壳身份。"""
    if not name and not version:
        return None
    return AbilityMcpServerInfoView(name=name or None, version=version or None)


def _tool_views(tools: tuple[dict[str, Any], ...]) -> list[AbilityMcpToolView]:
    """把 Core 的普通字典映射成协议视图；缺名字的残项直接丢弃。"""
    views: list[AbilityMcpToolView] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        read_only_hint = item.get("read_only_hint")
        views.append(
            AbilityMcpToolView(
                name=name,
                title=item.get("title") if isinstance(item.get("title"), str) else None,
                description=(
                    item.get("description") if isinstance(item.get("description"), str) else None
                ),
                input_schema=(
                    item.get("input_schema") if isinstance(item.get("input_schema"), dict) else None
                ),
                output_schema=(
                    item.get("output_schema")
                    if isinstance(item.get("output_schema"), dict)
                    else None
                ),
                read_only_hint=read_only_hint if isinstance(read_only_hint, bool) else None,
            )
        )
    return views


def _resource_views(resources: tuple[Any, ...]) -> list[AbilityMcpResourceView]:
    """把 Core 的 ``McpResourceInfo`` 映射成协议视图；不含资源正文。"""
    views: list[AbilityMcpResourceView] = []
    for item in resources:
        uri = getattr(item, "uri", None)
        name = getattr(item, "name", None)
        if not isinstance(uri, str) or not isinstance(name, str):
            continue
        size = getattr(item, "size", None)
        views.append(
            AbilityMcpResourceView(
                uri=uri,
                name=name,
                title=getattr(item, "title", None),
                description=getattr(item, "description", None),
                mime_type=getattr(item, "mime_type", None),
                size=size if isinstance(size, int) and not isinstance(size, bool) else None,
            )
        )
    return views


def _view_from_report(
    namespace: str,
    name: str,
    version: str,
    server: str,
    report: McpProbeReport,
) -> AbilityMcpProbeView:
    """把 Core 报告映射成协议视图；``ok`` 与 ``error`` 严格同步。"""
    failure = report.failure
    error: AbilityMcpProbeErrorView | None = None
    if not report.ok:
        reason = _safe_reason(failure.reason if failure is not None else "internal")
        error = _error_view(
            reason,
            http_status=failure.http_status if failure is not None else None,
            hint=failure.hint if failure is not None else "",
        )
    tools = _tool_views(report.tools)
    resources = _resource_views(report.resources)
    return AbilityMcpProbeView(
        namespace=namespace,
        name=name,
        version=version,
        server=server,
        ok=report.ok,
        phase=_safe_phase(report.phase),
        elapsed_ms=report.elapsed_ms,
        protocol_version=report.protocol_version,
        server_info=_server_info(report.server_name, report.server_version),
        tools=tools,
        tool_count=len(tools),
        truncated=False,
        resources_supported=report.resources_supported,
        resources=resources,
        resource_count=len(resources),
        error=error,
    )


def _terminal_view(
    namespace: str,
    name: str,
    version: str,
    server: str,
    *,
    reason: AbilityMcpProbeReason,
) -> AbilityMcpProbeView:
    """未进入 Core 就得到结论（单飞/并发）时的失败视图，阶段记为 ``config``。"""
    return AbilityMcpProbeView(
        namespace=namespace,
        name=name,
        version=version,
        server=server,
        ok=False,
        phase="config",
        elapsed_ms=0,
        tool_count=0,
        resource_count=0,
        error=_error_view(reason),
    )


def _resolve_definition(
    install_root: str | None, full_name: str, version: str
) -> AbilityDefinition:
    """从安装存储读出一个版本的包定义。

    失败按管理面口径分类：没有这个版本是 ``not_found``，其余安装/解析问题都是
    ``unavailable``。异常文本只带错误码，不带安装根或包目录的绝对路径。
    """
    if not install_root:
        raise CatalogError("not_found", f"未找到已安装能力包：{full_name}@{version}")
    installer = AbilityInstaller(Path(install_root))
    try:
        payload = installer.show(full_name, version)
    except InstallerError as error:
        if error.code == "not_installed":
            raise CatalogError("not_found", f"未找到已安装能力包：{full_name}@{version}") from error
        raise CatalogError("unavailable", f"读取已安装能力包失败（{error.code}）") from error
    except Exception as error:  # 安装根不可读等环境错误按「不可用」处理
        raise CatalogError("unavailable", "读取已安装能力包失败") from error
    if not isinstance(payload, dict):
        raise CatalogError("unavailable", "已安装能力包数据不完整")
    raw = payload.get("definition")
    if not isinstance(raw, dict):
        raise CatalogError("unavailable", "已安装能力包定义不可读")
    try:
        return AbilityDefinition.model_validate(raw)
    except Exception as error:  # 定义损坏同样降级为「不可用」
        raise CatalogError("unavailable", "已安装能力包定义不可读") from error


def _mcp_server(definition: AbilityDefinition) -> str:
    """取出定义里声明的 agentconf 服务器名；非 MCP 包直接拒绝。"""
    if definition.origin != "mcp":
        raise CatalogError("invalid_change", _NOT_MCP)
    mcp = definition.mcp
    server = mcp.get("server") if isinstance(mcp, dict) else None
    if not isinstance(server, str) or not server:
        raise CatalogError("invalid_change", _NOT_MCP)
    return server


async def _run_probe(
    server: str,
    *,
    timeout_ms: int,
    include_resources: bool,
    db_path: str | Path | None,
    connector: Any | None,
) -> McpProbeReport:
    """在整体预算内调用一次 Core 探测；预算超限折算成 ``timeout`` 报告。"""
    started = time.monotonic()
    budget_s = (timeout_ms + _TIMEOUT_MARGIN_MS) / 1000
    try:
        async with asyncio.timeout(budget_s):
            return await probe_server(
                server,
                db_path=db_path,
                timeout_ms=timeout_ms,
                include_tools=True,
                include_resources=include_resources,
                connector=connector,
            )
    except TimeoutError:
        return McpProbeReport(
            ok=False,
            phase="connect",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            failure=McpProbeFailure(reason="timeout"),
        )


async def probe_package_mcp(
    install_root: str | None,
    namespace: str,
    name: str,
    version: str,
    request: AbilityMcpProbeRequest,
    *,
    db_path: str | Path | None = None,
    connector: Any | None = None,
) -> AbilityMcpProbeView:
    """探测一个已安装 MCP 能力包声明的服务器，返回管理面视图。

    本函数不抛探测失败：连接、握手、清单与超时的失败都写进 ``error``；只有
    包解析问题抛 :class:`CatalogError`（``not_found`` / ``unavailable``），
    非 MCP 包抛 ``invalid_change``（消息 ``definition_not_mcp``）。

    Args:
        install_root: 已安装能力包的发行版级安装根；为空视为没有安装包。
        namespace: 能力包身份第一段。
        name: 能力包身份第二段。
        version: 要探测的已安装版本（SemVer core）。
        request: 探测请求；``timeout_ms`` 会再夹到 ``[1000, 10000]``。
        db_path: agentconf 数据库路径；``None`` 时用 Core 默认值。
        connector: 建连函数注入缝；生产为 ``None``（Core 用真实 SDK）。

    Returns:
        成功与失败同形的 :class:`AbilityMcpProbeView`；HTTP 层一律 200。
    """
    full_name = f"{namespace}.{name}"
    definition = _resolve_definition(install_root, full_name, version)
    server = _mcp_server(definition)
    timeout_ms = _clamp_timeout_ms(request.timeout_ms)

    gate_reason = _gate.try_enter(server)
    if gate_reason is not None:
        logger.info("ability mcp probe skipped server=%s reason=%s", server, gate_reason)
        return _terminal_view(namespace, name, version, server, reason=gate_reason)
    try:
        report = await _run_probe(
            server,
            timeout_ms=timeout_ms,
            include_resources=request.include_resources,
            db_path=db_path,
            connector=connector,
        )
    finally:
        _gate.leave(server)

    view = _view_from_report(namespace, name, version, server, report)
    if view.error is None:
        logger.info("ability mcp probe server=%s ok", server)
    else:
        logger.info("ability mcp probe server=%s reason=%s", server, view.error.reason)
    return view
