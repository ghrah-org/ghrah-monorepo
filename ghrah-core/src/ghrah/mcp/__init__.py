# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""MCP 客户端。

与 ``ghrah.scheme``（能力 + 技能）**并列的独立体系**：
本包不 import 它们中的任何一个，只做一件事——把一台 MCP 服务器的连接声明变成
可执行的工具调用。桥接由 ability 层反向依赖本包完成。

对外只暴露两类东西：

* **数据结构**：:class:`McpServerSpec` 与其成员。由技能模块解析配置产出后交给
  本包消费，本包**不解析任何配置文件**。
* **执行入口**：持有连接、执行调用。工具信息不在此处二次持有——它属于技能模块
  的本地配置，这里拿到的只是「能调哪些工具名」这一份授权集合。

设计上的两个关键取舍：

**工具信息全部来自本地配置。** 读取一个技能声明的 MCP 能力全程离线，不依赖任何
网络请求；服务器宕机、凭证缺失都不影响模型看到「有哪些工具」。代价是配置里的
参数 schema 是快照，可能与服务器实际不一致，需要靠生成与校验工具维护。

**面向模型的执行入口只有一个。** 工具不作为独立函数注册，而是通过一个统一的
调用入口传入 ``(服务器, 工具, 参数)``。这使工具列表恒定不变——技能加载不会改动
它，因而不会打断提示词前缀缓存；也免去了把服务器上的任意工具名改造成合法函数名
（截断、加哈希、反解）这一整类问题。
"""

from __future__ import annotations

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
from ghrah.mcp.result import ToolOutcome, project_result
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
    "CredentialResolver",
    "FailureKind",
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
    "format_unknown_tool_message",
    "is_session_terminated",
    "list_tool_names",
    "no_credentials",
    "open_client",
    "project_result",
    "read_only_hint",
    "render_input_schema",
    "resolve_headers",
    "sdk_connect_mode",
    "should_replay",
    "suggest_tool_names",
    "translate_exception",
    "truncate_description",
    "validate_server_name",
    "validate_tool_name",
]
