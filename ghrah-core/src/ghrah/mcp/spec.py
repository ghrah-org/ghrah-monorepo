# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""一台 MCP 服务器的连接声明。

**本模块不读任何文件、不联网**。yaml 的解析属于技能模块，本模块只承载解析后的
结果，并校验那些「无论谁来解析都必须成立」的不变量。

一条关键性质：这里的数据结构**不包含任何需要联网才能得到的信息**——工具名、
一行用途说明、参数 schema 全部来自配置。因此「读取一个技能的 MCP 能力」这件事
全程离线，服务器宕机也不影响模型看到能力清单。

凭据的处理方式是本模块最需要解释的部分。配置里**不出现密钥**，只允许一个保留
占位符 :data:`AUTH_PLACEHOLDER`：它被替换成「与这台服务器的地址绑定」的那条密钥
（登记在 agentconf 里）。选择这个形状而不是「哪个 header 放密钥」的枚举，是为了
不把认证方式固化——现实中的服务器用 ``Authorization: Bearer``、``X-Api-Key``、
``Authorization: Api-Key`` 的都有。

而「只允许引用绑定值」这点是安全边界所在：如果占位符能引用任意名字的密钥，任何
一个技能都可以写一行 ``headers: {Authorization: "Bearer {{secret:LLM_API_KEY}}"}``
把模型密钥带走。限定为地址绑定值之后，想拿到某台服务器的密钥就必须声明与它完全
相同的目的地址——而请求也正是发往那个地址。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from ghrah.mcp.errors import McpConfigError, McpToolMissingError
from ghrah.mcp.identity import validate_server_name, validate_tool_description, validate_tool_name
from ghrah.mcp.schema import (
    DEFAULT_MAX_SCHEMA_BYTES,
    SchemaRender,
    render_input_schema,
    require_object_schema,
)

__all__ = [
    "AUTH_PLACEHOLDER",
    "DEFAULT_CONNECT_TIMEOUT_MS",
    "DEFAULT_EXECUTION_TIMEOUT_MS",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_MAX_RESULT_BYTES",
    "MAX_PAGES_LIMIT",
    "McpServerSpec",
    "McpToolSpec",
    "ProtocolPreference",
    "Transport",
]

AUTH_PLACEHOLDER = "{{auth}}"
"""配置里唯一允许的占位符，解析期替换为该服务器地址绑定的密钥。"""

_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")
_TEMPLATE_LEFT_RE = re.compile(r"\$\{")

DEFAULT_CONNECT_TIMEOUT_MS = 60_000
DEFAULT_EXECUTION_TIMEOUT_MS = 300_000
DEFAULT_MAX_RESULT_BYTES = 2_000_000
DEFAULT_MAX_PAGES = 50
MAX_PAGES_LIMIT = 500

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


class Transport(StrEnum):
    """传输方式。

    只声明 ``streamable-http``：子进程传输尚未支持，用枚举而不是字符串，可以让
    「不支持」在类型层就暴露，而不是等到运行期报一个含混的连接错误。
    """

    STREAMABLE_HTTP = "streamable-http"


class ProtocolPreference(StrEnum):
    """协议协商偏好。

    ``MODERN_FIRST`` 表示先按无状态协议建连，收到明确的版本不支持信号后回退到
    握手协议。回退**只认明确的版本信号**，不因超时触发——把「服务器慢」误判成
    「协议不对」会导致多打一次请求，把慢变成一个更慢的失败。
    """

    MODERN_FIRST = "modern-first"
    LEGACY = "legacy"
    MODERN = "2026-07-28"


def _validate_url(url: str) -> str:
    """校验地址：必须 http(s)、有主机、不含内嵌凭据与空白。"""
    if not isinstance(url, str):  # pragma: no cover
        raise McpConfigError(f"url must be a string, got {type(url).__name__}")
    if not url:
        raise McpConfigError("url must not be empty")
    if any(ch.isspace() for ch in url):
        raise McpConfigError(f"url must not contain whitespace: {url!r}")

    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_URL_SCHEMES:
        raise McpConfigError(
            f"url scheme must be one of {sorted(_ALLOWED_URL_SCHEMES)}, got {parts.scheme!r}"
        )
    if not parts.netloc:
        raise McpConfigError(f"url must include a host: {url!r}")
    if parts.username is not None or parts.password is not None:
        # 凭据进地址会随日志与错误信息扩散，一律拒绝。
        raise McpConfigError(
            "url must not embed userinfo (credentials belong in the credential store)"
        )
    return url


def _validate_header_pair(name: Any, value: Any) -> None:
    """校验单个请求头名与值。"""
    if not isinstance(name, str) or not name:
        raise McpConfigError("header name must be a non-empty string")
    if any(ch.isspace() for ch in name) or ":" in name:
        raise McpConfigError(f"header name {name!r} must not contain whitespace or ':'")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        raise McpConfigError(f"header name {name!r} must not contain control characters")
    if not isinstance(value, str):
        raise McpConfigError(f"header {name!r} value must be a string, got {type(value).__name__}")
    if any(ord(ch) < 0x20 and ch not in "\t" for ch in value):
        raise McpConfigError(f"header {name!r} value must not contain control characters")
    if _TEMPLATE_LEFT_RE.search(value):
        # 残留的模板占位符发出去必然被服务器拒绝，且会掩盖真正的配置错误。
        raise McpConfigError(
            f"header {name!r} must not contain '${{...}}' templates; "
            f"use {AUTH_PLACEHOLDER} for the server credential"
        )


def _validate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """校验请求头集合，并落实「最多一个凭据头」的约束。"""
    if not isinstance(headers, Mapping):
        raise McpConfigError(f"headers must be a mapping, got {type(headers).__name__}")

    materialized: dict[str, str] = {}
    placeholder_headers: list[str] = []
    for name, value in headers.items():
        _validate_header_pair(name, value)
        for found in _PLACEHOLDER_RE.findall(value):
            if found != AUTH_PLACEHOLDER:
                raise McpConfigError(
                    f"header {name!r} contains unsupported placeholder {found}; "
                    f"only {AUTH_PLACEHOLDER} is allowed"
                )
        if AUTH_PLACEHOLDER in value:
            placeholder_headers.append(name)
        materialized[name] = value

    if len(placeholder_headers) > 1:
        # 多密钥服务器需要多个值，当前契约只支持一个；让它在配置期就失败，
        # 而不是运行期出现「一个头有值、另一个头是空串」的半坏状态。
        raise McpConfigError(
            "at most one header may carry "
            f"{AUTH_PLACEHOLDER}, got {len(placeholder_headers)}: {placeholder_headers}"
        )
    return materialized


def _require_positive_int(value: Any, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise McpConfigError(f"{name} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise McpConfigError(f"{name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class McpToolSpec:
    """白名单中的一个工具，全部信息来自本地配置。"""

    name: str
    """服务器上的工具名，原样保存，不做归一化。"""

    description: str
    """写给人看的一行用途。目录渲染直接用它，因此必须是「何时使用」而不是
    「内部如何实现」。"""

    input_schema: Mapping[str, Any] = field(default_factory=dict)
    """参数 schema，零参数工具为 ``{}``。"""

    read_only: bool = False
    """该工具是否只读。

    默认 ``False``（按可写处理）。这个标注只影响一件事：失败后能否**自动重放**。
    把不确定的工具当只读，会在它实际写入数据时重复执行，而这个错误无法撤销；
    反过来把只读工具当可写，代价只是多一次失败提示。默认值必须偏向安全。

    注意它与服务器声明的 ``readOnlyHint`` 是两回事：工具清单来自本地配置，运行期
    不取服务器标注，因此这个判断依据也只能是配置，由配置维护者负责。
    """

    def __post_init__(self) -> None:
        validate_tool_name(self.name)
        validate_tool_description(self.description)
        require_object_schema(self.input_schema)
        if not isinstance(self.read_only, bool):
            raise McpConfigError(
                f"read_only must be a boolean, got {type(self.read_only).__name__}"
            )

    def render_schema(self, *, max_bytes: int | None = None) -> SchemaRender:
        """整形后的 schema 文本，见 :mod:`ghrah.mcp.schema`。"""
        limit = DEFAULT_MAX_SCHEMA_BYTES if max_bytes is None else max_bytes
        return render_input_schema(self.input_schema, max_bytes=limit)


@dataclass(frozen=True)
class McpServerSpec:
    """一台 MCP 服务器的完整连接声明。"""

    skill_full_name: str
    """声明它的技能。激活与权限判定的作用域，也是「哪些工具可见」的边界。"""

    server: str
    """技能内唯一的服务器名。会作为调用参数出现在模型面前，故受命名约束。"""

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    """请求头。至多一个含 :data:`AUTH_PLACEHOLDER`，其余为字面值。"""

    tools: tuple[McpToolSpec, ...] = ()
    """白名单。空元组表示「声明了服务器但不暴露任何工具」。"""

    transport: Transport = Transport.STREAMABLE_HTTP
    protocol: ProtocolPreference = ProtocolPreference.MODERN_FIRST

    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS
    execution_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    max_pages: int = DEFAULT_MAX_PAGES

    def __post_init__(self) -> None:
        if not isinstance(self.skill_full_name, str) or not self.skill_full_name.strip():
            raise McpConfigError("skill_full_name must be a non-empty string")
        validate_server_name(self.server)
        _validate_url(self.url)

        headers = _validate_headers(self.headers)
        object.__setattr__(self, "headers", headers)

        seen: set[str] = set()
        duplicates: set[str] = set()
        for tool in self.tools:
            if not isinstance(tool, McpToolSpec):  # pragma: no cover
                raise McpConfigError(f"tools must contain McpToolSpec, got {type(tool).__name__}")
            if tool.name in seen:
                duplicates.add(tool.name)
            seen.add(tool.name)
        if duplicates:
            raise McpConfigError(
                "duplicate tool names in whitelist: " + ", ".join(sorted(duplicates))
            )

        _require_positive_int(self.connect_timeout_ms, name="connect_timeout_ms")
        _require_positive_int(self.execution_timeout_ms, name="execution_timeout_ms")
        _require_positive_int(self.max_result_bytes, name="max_result_bytes")
        _require_positive_int(self.max_pages, name="max_pages")
        if self.max_pages > MAX_PAGES_LIMIT:
            raise McpConfigError(f"max_pages must be <= {MAX_PAGES_LIMIT}, got {self.max_pages}")

    @property
    def tool_names(self) -> tuple[str, ...]:
        """白名单里的工具名，保持配置顺序。"""
        return tuple(tool.name for tool in self.tools)

    @property
    def credential_headers(self) -> tuple[str, ...]:
        """携带凭据占位符的请求头名。

        重定向处理需要这份清单：跨源跳转时**必须**剥掉这些头。不能写死
        ``Authorization``——凭据可能放在任意名字的头里。
        """
        return tuple(name for name, value in self.headers.items() if AUTH_PLACEHOLDER in value)

    @property
    def requires_credential(self) -> bool:
        """是否引用了凭据占位符。"""
        return bool(self.credential_headers)

    def find_tool(self, name: str) -> McpToolSpec | None:
        """按名字查白名单，未命中返回 ``None``。"""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def require_tool(self, name: str) -> McpToolSpec:
        """按名字查白名单，未命中抛 :class:`McpToolMissingError`。"""
        tool = self.find_tool(name)
        if tool is None:
            raise McpToolMissingError(self.server, name, self.tool_names)
        return tool
