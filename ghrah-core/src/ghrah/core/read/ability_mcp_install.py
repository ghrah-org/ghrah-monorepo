# SPDX-License-Identifier: Apache-2.0
"""MCP 页面安装：草稿探测、连接落库、定义安装与失败回滚。

Subject 只调用 :class:`ghrah.core.read.ability.AbilityCatalog` 的两个端口
（``mcp_draft_probe`` / ``mcp_install``）；探测、agentconf 写入与安装事实都在
Core 内完成，避免 Subject 反向 import Core 实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentconf import AgentsConfig
from agentconf.models.mcp_server import StreamableHTTPConnection
from agentconf.models.secure import reveal
from ghrah.protocol.ability_control import (
    AbilityMcpDraftAuth,
    AbilityMcpDraftProbeRequest,
    AbilityMcpDraftProbeView,
    AbilityMcpInstallRequest,
    AbilityMcpInstallView,
    AbilityMcpProbeErrorView,
    AbilityMcpResourceView,
    AbilityMcpServerInfoView,
    AbilityMcpToolView,
)
from ghrah.protocol.errors import ApiError
from ghrah.protocol.problem import Problem, ProblemCode

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.package.installer import AbilityInstaller, InstallerError
from ghrah.core.api.errors import CoreApiError
from ghrah.mcp.credentials import _secret_str
from ghrah.mcp.probe import McpProbeReport, probe_draft
from ghrah.mcp.spec import AUTH_PLACEHOLDER

__all__ = ["install_mcp_ability", "probe_mcp_draft"]

_DRAFT_PROBE_MESSAGES: dict[str, str] = {
    "config_invalid": "MCP 连接配置无效",
    "credential_missing": "MCP 凭据缺失",
    "unsupported_transport": "只支持 Streamable HTTP",
    "sdk_unavailable": "MCP SDK 未安装",
    "agentconf_unavailable": "agentconf 配置库不可用",
    "connect_failed": "无法连接 MCP 端点",
    "auth_failed": "认证失败，请检查密钥",
    "endpoint_not_found": "MCP 端点不存在",
    "method_not_allowed": "MCP 端点不接受该请求",
    "rate_limited": "MCP 端点限流",
    "server_error": "MCP 端点返回服务端错误",
    "timeout": "连接超时",
    "protocol_error": "MCP 协议握手失败",
    "too_large": "MCP 响应超过大小限制",
    "internal": "MCP 探测失败",
}


class _SetupError(RuntimeError):
    """连接落库/回滚失败，由本模块转成 ``ApiError``。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _SavedConnection:
    provider_id: str
    provider_name: str
    provider_created: bool
    server_id: str
    server_name: str
    server_created: bool


def _api_error(code: ProblemCode, detail: str, **details: Any) -> ApiError:
    return CoreApiError(
        Problem.make(code, capability="ability.mcp", detail=detail, details=details or None)
    )


def _provider_headers(db_path: str | Path | None, provider_id: str) -> tuple[dict[str, str], str]:
    """按 provider_id 取受信绑定与 Key；无密钥且无绑定时按无认证连接。"""
    try:
        with _open_agentconf(db_path) as cfg:
            provider = cfg.get_mcp_provider_by_id(provider_id)
            binding = provider.default_binding
            key = _secret_str(provider.api_key)
    except Exception:
        raise _SetupError("credential_missing", "MCP 提供商不存在或不可读") from None
    if not key:
        if binding is None:
            return {}, ""
        raise _SetupError("config_invalid", "MCP 提供商缺少密钥，但已配置 HTTP Header 绑定")
    if binding is None:
        return {"Authorization": f"Bearer {AUTH_PLACEHOLDER}"}, key
    if binding.target != "http_header" or not binding.name:
        raise _SetupError("config_invalid", "MCP 提供商缺少 HTTP Header 绑定")
    return {binding.name: binding.value_template}, key


def _draft_headers(
    auth: AbilityMcpDraftAuth, *, db_path: str | Path | None = None
) -> tuple[dict[str, str], str]:
    if auth.mode == "provider":
        if not auth.provider_id:
            raise _SetupError("credential_missing", "未选择 MCP 提供商")
        return _provider_headers(db_path, auth.provider_id)
    if auth.mode == "bearer":
        return {"Authorization": f"Bearer {AUTH_PLACEHOLDER}"}, auth.key
    return {auth.header or "Authorization": AUTH_PLACEHOLDER}, auth.key


def _probe_error_view(failure: Any) -> AbilityMcpProbeErrorView:
    reason = failure.reason if failure.reason in _DRAFT_PROBE_MESSAGES else "internal"
    return AbilityMcpProbeErrorView(
        reason=reason,
        message=_DRAFT_PROBE_MESSAGES[reason],
        http_status=failure.http_status,
        hint=failure.hint or None,
    )


def _draft_view(report: McpProbeReport) -> AbilityMcpDraftProbeView:
    tools = [
        AbilityMcpToolView(
            name=item.get("name") or "",
            title=item.get("title"),
            description=item.get("description"),
            input_schema=item.get("input_schema"),
            output_schema=item.get("output_schema"),
            read_only_hint=item.get("read_only_hint"),
        )
        for item in report.tools
    ]
    resources = [
        AbilityMcpResourceView(
            uri=item.uri,
            name=item.name,
            title=item.title,
            description=item.description,
            mime_type=item.mime_type,
            size=item.size,
        )
        for item in report.resources
    ]
    return AbilityMcpDraftProbeView(
        ok=report.ok,
        phase=report.phase,
        elapsed_ms=report.elapsed_ms,
        protocol_version=report.protocol_version,
        server_info=(
            AbilityMcpServerInfoView(name=report.server_name, version=report.server_version)
            if report.server_name is not None or report.server_version is not None
            else None
        ),
        tools=tools,
        tool_count=len(tools),
        resources=resources,
        resource_count=len(resources),
        resources_supported=report.resources_supported,
        truncated=False,
        error=None if report.failure is None else _probe_error_view(report.failure),
    )


async def probe_mcp_draft(
    request: AbilityMcpDraftProbeRequest, *, db_path: str | Path | None = None
) -> AbilityMcpDraftProbeView:
    """探测未保存的草稿连接；不写 agentconf、不安装。"""
    try:
        headers, credential = _draft_headers(request.auth, db_path=db_path)
    except _SetupError as error:
        reason = "credential_missing" if error.code == "credential_missing" else "config_invalid"
        return AbilityMcpDraftProbeView(
            ok=False,
            phase="config",
            elapsed_ms=0,
            tool_count=0,
            resource_count=0,
            error=AbilityMcpProbeErrorView(
                reason=reason,
                message=str(error),
            ),
        )
    report = await probe_draft(
        url=request.url,
        headers=headers,
        credential=credential,
        timeout_ms=request.timeout_ms,
        include_tools=True,
        include_resources=request.include_resources,
    )
    return _draft_view(report)


def _open_agentconf(db_path: str | Path | None) -> AgentsConfig:
    return AgentsConfig(db_path) if db_path else AgentsConfig()


def _binding(name: str, template: str) -> dict[str, str]:
    return {"target": "http_header", "name": name, "value_template": template}


def _server_url(server: Any) -> str | None:
    settings = server.settings
    if isinstance(settings, StreamableHTTPConnection):
        return reveal(settings.url)
    return None


def _save_connection(
    db_path: str | Path | None,
    *,
    ref: str,
    vendor: str | None,
    url: str,
    auth_mode: str,
    auth_header: str | None,
    api_key: str,
    provider_id: str | None,
    timeout_ms: int,
) -> _SavedConnection:
    try:
        with _open_agentconf(db_path) as cfg:
            servers = {
                item.name: item for item in cfg.list_mcp_servers(transport="streamable_http")
            }
            server = servers.get(ref)
            if server is not None:
                if _server_url(server) != url:
                    raise _SetupError(
                        "server_conflict",
                        f"agentconf 已存在同名 MCP 服务 {ref!r}，但端点不同",
                    )
                return _SavedConnection(
                    provider_id=getattr(server.auth, "provider_id", None) or "",
                    provider_name="",
                    provider_created=False,
                    server_id=server.id,
                    server_name=server.name,
                    server_created=False,
                )

            if provider_id is not None:
                provider = cfg.get_mcp_provider_by_id(provider_id)
                if not _secret_str(provider.api_key):
                    if provider.default_binding is not None:
                        raise _SetupError(
                            "config_invalid", "MCP 提供商缺少密钥，但已配置 HTTP Header 绑定"
                        )
                    created = cfg.create_mcp_server(
                        ref,
                        "streamable_http",
                        {"url": url, "connect_timeout": timeout_ms / 1000},
                        auth={"mode": "none"},
                    )
                    return _SavedConnection(
                        provider_id=provider.id,
                        provider_name=provider.name,
                        provider_created=False,
                        server_id=created.id,
                        server_name=created.name,
                        server_created=True,
                    )
                if provider.default_binding is None:
                    provider = cfg.update_mcp_provider(
                        provider.name,
                        expected_revision=provider.revision,
                        expected_id=provider.id,
                        default_binding=_binding("Authorization", "Bearer {{auth}}"),
                    )
                created = cfg.create_mcp_server(
                    ref,
                    "streamable_http",
                    {"url": url, "connect_timeout": timeout_ms / 1000},
                    auth={
                        "mode": "provider",
                        "provider_id": provider.id,
                        "binding_mode": "inherit",
                    },
                )
                return _SavedConnection(
                    provider_id=provider.id,
                    provider_name=provider.name,
                    provider_created=False,
                    server_id=created.id,
                    server_name=created.name,
                    server_created=True,
                )

            header = "Authorization" if auth_mode == "bearer" else (auth_header or "Authorization")
            template = "Bearer {{auth}}" if auth_mode == "bearer" else "{{auth}}"
            binding = _binding(header, template)
            provider_name = f"{ref}-provider"
            provider = next(
                (item for item in cfg.list_mcp_providers() if item.name == provider_name), None
            )
            provider_created = provider is None
            if provider is None:
                provider = cfg.create_mcp_provider(
                    provider_name,
                    vendor=vendor,
                    api_key=api_key,
                    default_binding=binding,
                )
            else:
                provider = cfg.update_mcp_provider(
                    provider.name,
                    expected_revision=provider.revision,
                    api_key=api_key,
                    default_binding=binding,
                    vendor=vendor,
                )
            created = cfg.create_mcp_server(
                ref,
                "streamable_http",
                {"url": url, "connect_timeout": timeout_ms / 1000},
                auth={
                    "mode": "provider",
                    "provider_id": provider.id,
                    "binding_mode": "inherit",
                },
            )
            return _SavedConnection(
                provider_id=provider.id,
                provider_name=provider.name,
                provider_created=provider_created,
                server_id=created.id,
                server_name=created.name,
                server_created=True,
            )
    except _SetupError:
        raise
    except Exception as exc:
        raise _SetupError("agentconf_failed", "保存 MCP 连接配置失败") from exc


def _remove_connection(db_path: str | Path | None, saved: _SavedConnection) -> None:
    try:
        with _open_agentconf(db_path) as cfg:
            if saved.server_created:
                server = cfg.get_mcp_server(saved.server_name)
                cfg.delete_mcp_server(
                    server.name,
                    expected_revision=server.revision,
                    expected_id=server.id,
                )
            if saved.provider_created and saved.provider_name:
                provider = cfg.get_mcp_provider(saved.provider_name)
                cfg.delete_mcp_provider(
                    provider.name,
                    expected_revision=provider.revision,
                    expected_id=provider.id,
                )
    except Exception as exc:
        raise _SetupError("rollback_failed", "回滚 MCP 连接配置失败") from exc


async def install_mcp_ability(
    *,
    db_path: str | Path | None,
    install_root: str | Path,
    request: AbilityMcpInstallRequest,
) -> AbilityMcpInstallView:
    """验证 → 保存 agentconf → 安装定义 → 失败回滚，返回安装回执。"""
    try:
        headers, credential = _draft_headers(request.connection.auth, db_path=db_path)
    except _SetupError as error:
        raise _api_error(
            ProblemCode.INVALID_ARGUMENT,
            "MCP 提供商不可用，未开始安装",
            reason=error.code,
        ) from None
    report = await probe_draft(
        url=request.connection.url,
        headers=headers,
        credential=credential,
        timeout_ms=request.connection.timeout_ms,
        include_tools=True,
    )
    if not report.ok:
        reason = report.failure.reason if report.failure is not None else "internal"
        raise _api_error(ProblemCode.INVALID_ARGUMENT, "MCP 探测失败，未开始安装", reason=reason)
    available = {item.get("name"): item for item in report.tools if item.get("name")}
    missing = [name for name in request.tools if name not in available]
    if missing:
        raise _api_error(
            ProblemCode.FAILED_PRECONDITION,
            "工具集在探测后发生变化，请重新选择",
            missing=missing,
        )

    installer = AbilityInstaller(install_root)
    full_name = f"mcp.{request.ability.name}"
    try:
        before = {item.version for item in installer.store.versions_of(full_name)}
    except Exception:
        before = set()
    try:
        saved = _save_connection(
            db_path,
            ref=request.server.ref,
            vendor=request.server.vendor,
            url=request.connection.url,
            auth_mode=request.connection.auth.mode,
            auth_header=request.connection.auth.header,
            api_key=request.connection.auth.key,
            provider_id=request.connection.auth.provider_id,
            timeout_ms=request.connection.timeout_ms,
        )
    except _SetupError as error:
        code = (
            ProblemCode.FAILED_PRECONDITION
            if error.code == "server_conflict"
            else ProblemCode.INVALID_ARGUMENT
        )
        raise _api_error(code, str(error), reason=error.code) from error

    header = {
        "format": "ghrah.ability-definition",
        "schema_version": "2",
        "origin": "mcp",
        "identity": {
            "namespace": "mcp",
            "name": request.ability.name,
            "version": request.ability.version or "0.0.1",
        },
        "title": request.ability.title,
        "description": request.ability.description,
        "mcp": {"server": request.server.ref},
    }
    try:
        result = await installer.install_mcp_definition(
            header,
            [available[name] for name in request.tools],
            prefix=request.ability.prefix,
            requested_version=request.ability.version,
        )
    except InstallerError as error:
        try:
            _remove_connection(db_path, saved)
        except _SetupError as rollback:
            raise _api_error(
                ProblemCode.INTERNAL,
                "安装失败，且 MCP 连接配置回滚失败",
                install_code=error.code,
                rollback_code=rollback.code,
            ) from error
        code = (
            ProblemCode.FAILED_PRECONDITION
            if error.code == "version_conflict"
            else ProblemCode.INVALID_ARGUMENT
        )
        raise _api_error(code, f"MCP Ability 安装失败：{error.code}", reason=error.code) from error
    except Exception as error:
        try:
            _remove_connection(db_path, saved)
        except _SetupError as rollback:
            raise _api_error(
                ProblemCode.INTERNAL,
                "安装失败，且 MCP 连接配置回滚失败",
                rollback_code=rollback.code,
            ) from error
        raise _api_error(ProblemCode.INTERNAL, "MCP Ability 安装失败") from error

    definition = parse_definition(
        (result.root / "ability.yaml").read_text(encoding="utf-8"),
        source=str(result.root),
    )
    members = definition.to_members()
    return AbilityMcpInstallView(
        package=result.full_name,
        version=result.version,
        digest=result.package_digest,
        members=[
            {
                "member": member.member,
                "type": "mcp",
                "server": str(member.data["server"]),
                "tool": str(member.data["tool"]),
                "read_only": bool(member.data.get("read_only", False)),
            }
            for member in members
        ],
        member_count=len(members),
        version_bumped=bool(before) and result.version not in before,
        already_installed=result.already_installed,
        agentconf={
            "provider_id": saved.provider_id,
            "server_id": saved.server_id,
        },
    )
