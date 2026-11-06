# SPDX-License-Identifier: Apache-2.0
"""agentconf schema 6 到 MCP 客户端的受信适配；凭据仅按固定连接快照读取。"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agentconf import AgentsConfig, AgentsConfigError, StreamableHTTPConnection

from ghrah.mcp.credentials import _secret_str
from ghrah.mcp.errors import McpConfigError, McpCredentialMissingError
from ghrah.mcp.identity import MAX_SERVER_NAME_LEN
from ghrah.mcp.spec import (
    AUTH_PLACEHOLDER,
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_EXECUTION_TIMEOUT_MS,
    DEFAULT_MAX_PAGES,
    DEFAULT_MAX_RESULT_BYTES,
    McpServerSpec,
    McpToolSpec,
    ProtocolPreference,
)

logger = logging.getLogger(__name__)
__all__ = ["AgentconfMcpSource", "load_spec"]


def _host_of(url: str) -> str:
    return urlsplit(url).hostname or "mcp"


def _server_name_from(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "mcp"
    if not slug[0].isalpha():
        slug = "mcp_" + slug
    return slug[:MAX_SERVER_NAME_LEN].rstrip("_")


def _protocol_from_hint(hint: str | None) -> ProtocolPreference:
    aliases = {
        "auto": ProtocolPreference.MODERN_FIRST,
        "modern-first": ProtocolPreference.MODERN_FIRST,
        "modern_first": ProtocolPreference.MODERN_FIRST,
        "legacy": ProtocolPreference.LEGACY,
        "handshake": ProtocolPreference.LEGACY,
        "modern": ProtocolPreference.MODERN,
        ProtocolPreference.MODERN.value: ProtocolPreference.MODERN,
    }
    return aliases.get((hint or "").strip().lower(), ProtocolPreference.MODERN_FIRST)


class AgentconfMcpSource:
    """只读声明和受信调用凭据；不查模型 Provider，不按 URL 猜测关联。"""

    def __init__(
        self,
        config: AgentsConfig | None = None,
        *,
        db_path: str | Path | None = None,
        auth_header: str | None = None,
        auth_template: str = f"Bearer {AUTH_PLACEHOLDER}",
    ) -> None:
        if auth_header is not None or auth_template != f"Bearer {AUTH_PLACEHOLDER}":
            raise McpConfigError("认证方式必须显式配置在 MCP Provider 或 Server 上")
        self._config = config
        self._db_path = db_path
        self._owns_config = config is None

    def _ensure_config(self) -> AgentsConfig:
        if self._config is None:
            try:
                self._config = AgentsConfig(self._db_path) if self._db_path else AgentsConfig()
            except Exception:
                raise McpConfigError("agentconf MCP 配置不可用；请检查版本和显式迁移状态") from None
        if not hasattr(self._config, "read_mcp_credential"):
            raise McpConfigError("MCP Provider 接线需要 agentconf 0.4／schema 6")
        return self._config

    def close(self) -> None:
        """只关闭本对象创建的配置源。"""
        if self._config is not None and self._owns_config:
            self._config.close()
            self._config = None

    def __enter__(self) -> AgentconfMcpSource:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def names(self) -> tuple[str, ...]:
        """只列本地 MCP 服务记录，不发现远端工具。"""
        return tuple(server.name for server in self._ensure_config().list_mcp_servers())

    def _resolve(self, name: str) -> Any:
        try:
            return self._ensure_config().resolve_mcp_server(name)
        except AgentsConfigError, ValueError:
            raise McpConfigError("MCP 服务或认证配置不可用") from None

    def connection_spec(self, name: str, **kwargs: Any) -> McpServerSpec:
        """编译无密钥连接描述；Provider Key 与 inline 值都留到实际调用。"""
        return self.spec(name, **kwargs)

    def spec(
        self,
        name: str,
        *,
        skill_full_name: str,
        server: str | None = None,
        tools: Sequence[McpToolSpec] = (),
        protocol: ProtocolPreference | None = None,
        execution_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        max_pages: int = DEFAULT_MAX_PAGES,
        **obsolete: Any,
    ) -> McpServerSpec:
        """按确定 Server 和 MCP Provider 身份编译连接模板。"""
        if obsolete:
            raise McpConfigError("凭据与认证覆盖不再通过 spec 参数配置")
        record = self._resolve(name)
        connection = record.connection
        if not isinstance(connection, StreamableHTTPConnection):
            raise McpConfigError("只支持 streamable-http，stdio／自定义 transport 尚未接入")
        auth = record.auth
        if auth is None:
            raise McpConfigError("MCP 配置缺少 schema 6 认证描述")
        if auth.mode == "external":
            raise McpConfigError("当前宿主不支持外部凭据引用")
        headers = {} if auth.mode == "inline" else dict(connection.headers.get_secret_value())
        if auth.mode == "provider":
            binding = auth.binding
            if binding is None or binding.target != "http_header":
                raise McpConfigError("MCP 认证绑定与 HTTP 不匹配")
            headers[binding.name] = binding.value_template
        return McpServerSpec(
            skill_full_name=skill_full_name,
            server=server or _server_name_from(name),
            url=_secret_str(connection.url),
            headers=headers,
            tools=tuple(tools),
            protocol=protocol or _protocol_from_hint(connection.protocol_version_hint),
            connect_timeout_ms=max(1, round(connection.connect_timeout * 1000))
            if connection.connect_timeout
            else DEFAULT_CONNECT_TIMEOUT_MS,
            execution_timeout_ms=execution_timeout_ms,
            max_result_bytes=max_result_bytes,
            max_pages=max_pages,
            connection_identity=(
                record.server_id,
                record.revision,
                auth.provider_id,
                auth.provider_revision,
            ),
            inline_headers=auth.mode == "inline",
        )

    def credential_for_spec(self, spec: McpServerSpec) -> str | None:
        """授权后按激活时固定的 Server／Provider 版本读取 Key。"""
        identity = spec.connection_identity
        if identity is None or identity[2] is None:
            return None
        try:
            value = self._ensure_config().read_mcp_credential(
                identity[0],
                expected_server_revision=identity[1],
                expected_provider_id=identity[2],
                expected_provider_revision=identity[3],
            )
            return value.get_secret_value()
        except AgentsConfigError as error:
            from agentconf.exceptions import MCPCredentialMissingError

            if isinstance(error, MCPCredentialMissingError):
                raise McpCredentialMissingError(spec.server, spec.url) from None
            raise McpConfigError("MCP 凭据来源或版本已变化") from None

    def headers_for_spec(self, spec: McpServerSpec) -> dict[str, str] | None:
        """授权后读取 inline 请求头；调用前核对稳定 Server ID 与 revision。"""
        if not spec.inline_headers:
            return None
        try:
            value = self._ensure_config().read_mcp_inline_headers(
                spec.connection_identity[0], expected_server_revision=spec.connection_identity[1]
            )
            return dict(value.get_secret_value())
        except AgentsConfigError:
            raise McpConfigError("MCP 静态认证配置已变化") from None

    def credential_for(self, name: str, *, strict_reference: bool = True) -> str | None:
        """脚本入口仍按明确 Server 身份读取，不再提供 URL 回退。"""
        return self.credential_for_spec(
            self.connection_spec(name, skill_full_name="diagnostic/explicit")
        )

    def spec_and_credential(self, name: str, **kwargs: Any) -> tuple[McpServerSpec, str | None]:
        """显式诊断调用同时读取描述与凭据；生产激活不使用此入口。"""
        spec = self.spec(name, **kwargs)
        return spec, self.credential_for_spec(spec)


def load_spec(
    name: str, *, skill_full_name: str, db_path: str | Path | None = None, **kwargs: Any
) -> tuple[McpServerSpec, str | None]:
    """一次性诊断的具名服务读取。"""
    with AgentconfMcpSource(db_path=db_path) as source:
        return source.spec_and_credential(name, skill_full_name=skill_full_name, **kwargs)
