# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP 连接、会话与调用客户端。

工具白名单与 schema 来自显式本地配置，激活保持离线，实际调用才取凭据、建连。
授权权威在 AbilityAccessStore，生产关联由 ghrah.ability.mcp_wiring 核验；本包的
激活集合只负责当前会话的调用接纳，不授予任何能力。逐工具的模型入口由能力工具面
装配，旧 mcp_call 已退役。SDK 延迟到实际连接或协议结果转换时导入。
"""

from __future__ import annotations

from ghrah.mcp.agentconf_source import AgentconfMcpSource, load_spec
from ghrah.mcp.client import (
    call_tool_once,
    list_tool_names,
    open_client,
    read_only_hint,
    resolve_headers,
    sdk_connect_mode,
    translate_exception,
)
from ghrah.mcp.credentials import (
    AgentconfCredentialResolver,
    CredentialResolver,
    StaticCredentials,
    no_credentials,
)
from ghrah.mcp.diagnostics import (
    HttpProbe,
    explain_status,
    probe_endpoint,
)
from ghrah.mcp.errors import (
    FailureKind,
    McpConfigError,
    McpConnectionError,
    McpCredentialMissingError,
    McpError,
    McpNotActivatedError,
    McpProtocolError,
    McpRequestError,
    McpToolMissingError,
    classify_http_status,
    classify_rpc_error,
    is_session_terminated,
    should_replay,
)
from ghrah.mcp.identity import (
    MAX_SERVER_NAME_LEN,
    MAX_TOOL_DESCRIPTION_CHARS,
    format_unknown_tool_message,
    suggest_tool_names,
    truncate_description,
    validate_server_name,
    validate_tool_name,
)
from ghrah.mcp.result import ToolOutcome, project_result, structured_as_text
from ghrah.mcp.runtime import McpRuntime
from ghrah.mcp.schema import (
    DEFAULT_MAX_SCHEMA_BYTES,
    SchemaRender,
    compact_json,
    render_input_schema,
)
from ghrah.mcp.session import McpSession
from ghrah.mcp.spec import (
    AUTH_PLACEHOLDER,
    McpServerSpec,
    McpToolSpec,
    ProtocolPreference,
    Transport,
)

__all__ = [
    "AUTH_PLACEHOLDER",
    "DEFAULT_MAX_SCHEMA_BYTES",
    "MAX_SERVER_NAME_LEN",
    "MAX_TOOL_DESCRIPTION_CHARS",
    "AgentconfCredentialResolver",
    "AgentconfMcpSource",
    "CredentialResolver",
    "FailureKind",
    "HttpProbe",
    "McpConfigError",
    "McpConnectionError",
    "McpCredentialMissingError",
    "McpError",
    "McpNotActivatedError",
    "McpProtocolError",
    "McpRequestError",
    "McpRuntime",
    "McpServerSpec",
    "McpSession",
    "McpToolMissingError",
    "McpToolSpec",
    "ProtocolPreference",
    "SchemaRender",
    "StaticCredentials",
    "ToolOutcome",
    "Transport",
    "call_tool_once",
    "classify_http_status",
    "classify_rpc_error",
    "compact_json",
    "explain_status",
    "format_unknown_tool_message",
    "is_session_terminated",
    "list_tool_names",
    "load_spec",
    "no_credentials",
    "open_client",
    "probe_endpoint",
    "project_result",
    "read_only_hint",
    "render_input_schema",
    "resolve_headers",
    "sdk_connect_mode",
    "should_replay",
    "structured_as_text",
    "suggest_tool_names",
    "translate_exception",
    "truncate_description",
    "validate_server_name",
    "validate_tool_name",
]
