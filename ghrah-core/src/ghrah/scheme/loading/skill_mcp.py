# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 内的 MCP 配置：解析与渲染。

配置按「一个技能一个目录」组织：

```
<skill-dir>/
├── SKILL.md
└── mcp/                    # 可选；不存在表示该技能不接 MCP
    ├── sentry.yaml         # 一个文件 = 一台服务器
    └── github.yaml
```

解析放在本模块而不是 MCP 模块，是因为**配置属于技能**：技能是分发单位，MCP 服务器
是它附带的依赖。MCP 模块只接受解析后的声明，不理解文件格式。

渲染也放在这里，且分为两件事：**目录**（名字 + 一行用途，随技能正文一起给模型）与
**参数详情**（完整的 JSON Schema，按需索取）。两者的区别是体积与时机，不是内容——
目录里放 schema 会让每轮上下文线性膨胀。

一个刻意的取舍：配置里**没有**参数 schema 的来源标注、没有凭据、没有环境变量插值。
schema 是本地快照（可被裁剪），凭据只以 ``{{auth}}`` 占位符引用，见 MCP 模块的声明
约束。这样「读技能」这条路径永远不联网、也不接触密钥。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from ghrah.mcp.errors import McpConfigError
from ghrah.mcp.identity import truncate_description
from ghrah.mcp.schema import DEFAULT_MAX_SCHEMA_BYTES, render_input_schema
from ghrah.mcp.spec import (
    McpServerSpec,
    McpToolSpec,
    ProtocolPreference,
    Transport,
)

__all__ = [
    "MCP_DIR_NAME",
    "MCP_SPEC_SUFFIX",
    "load_mcp_specs",
    "render_mcp_catalog",
    "render_mcp_schema_block",
]

logger = logging.getLogger(__name__)

MCP_DIR_NAME = "mcp"
MCP_SPEC_SUFFIX = ".yaml"

_SERVER_KEYS = frozenset(
    {"server", "url", "protocol", "transport", "headers", "tools", "timeout", "limits"}
)
_TOOL_KEYS = frozenset({"name", "description", "input_schema", "read_only"})
_TIMEOUT_KEYS = frozenset({"connect_ms", "execution_ms"})
_LIMIT_KEYS = frozenset({"max_result_bytes", "max_pages"})

_DEFAULT_PROTOCOL = ProtocolPreference.MODERN_FIRST.value
_DEFAULT_TRANSPORT = Transport.STREAMABLE_HTTP.value


def _require_mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise McpConfigError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], *, where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise McpConfigError(f"{where} has unknown field(s): {', '.join(unknown)}")


def _int_option(value: Any, *, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise McpConfigError(f"{where} must be an integer, got {type(value).__name__}")
    return value


def _parse_tools(raw: Any, *, where: str) -> tuple[McpToolSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise McpConfigError(f"{where} must be a list")
    tools: list[McpToolSpec] = []
    for index, entry in enumerate(raw):
        entry_where = f"{where}[{index}]"
        item = _require_mapping(entry, where=entry_where)
        _reject_unknown(item, _TOOL_KEYS, where=entry_where)
        if "name" not in item:
            raise McpConfigError(f"{entry_where} is missing 'name'")
        if "description" not in item:
            raise McpConfigError(
                f"{entry_where} is missing 'description' — the catalog needs one line "
                "saying when to use this tool"
            )
        if "input_schema" not in item:
            raise McpConfigError(
                f"{entry_where} is missing 'input_schema' — declare the tool's full "
                "JSON Schema (use {} for a tool that takes no arguments)"
            )
        tools.append(
            McpToolSpec(
                name=str(item["name"]),
                description=str(item["description"]),
                input_schema=_require_mapping(
                    item["input_schema"], where=f"{entry_where}.input_schema"
                ),
                read_only=bool(item.get("read_only", False)),
            )
        )
    return tuple(tools)


def _parse_server_file(payload: Any, *, path: Path, skill_full_name: str) -> McpServerSpec:
    where = str(path)
    data = _require_mapping(payload, where=where)
    _reject_unknown(data, _SERVER_KEYS, where=where)

    for required in ("server", "url"):
        if required not in data:
            raise McpConfigError(f"{where} is missing required field '{required}'")

    timeout = _require_mapping(data.get("timeout") or {}, where=f"{where}.timeout")
    _reject_unknown(timeout, _TIMEOUT_KEYS, where=f"{where}.timeout")
    limits = _require_mapping(data.get("limits") or {}, where=f"{where}.limits")
    _reject_unknown(limits, _LIMIT_KEYS, where=f"{where}.limits")

    protocol_raw = str(data.get("protocol") or _DEFAULT_PROTOCOL)
    try:
        protocol = ProtocolPreference(protocol_raw)
    except ValueError:
        allowed = ", ".join(item.value for item in ProtocolPreference)
        raise McpConfigError(
            f"{where}: unsupported protocol {protocol_raw!r}; expected one of {allowed}"
        ) from None

    transport_raw = str(data.get("transport") or _DEFAULT_TRANSPORT)
    try:
        transport = Transport(transport_raw)
    except ValueError:
        raise McpConfigError(
            f"{where}: unsupported transport {transport_raw!r}; only "
            f"{_DEFAULT_TRANSPORT!r} is implemented"
        ) from None

    extra: dict[str, int] = {}
    if "connect_ms" in timeout:
        extra["connect_timeout_ms"] = _int_option(timeout["connect_ms"], where=f"{where}.timeout")
    if "execution_ms" in timeout:
        extra["execution_timeout_ms"] = _int_option(
            timeout["execution_ms"], where=f"{where}.timeout"
        )
    if "max_result_bytes" in limits:
        extra["max_result_bytes"] = _int_option(limits["max_result_bytes"], where=f"{where}.limits")
    if "max_pages" in limits:
        extra["max_pages"] = _int_option(limits["max_pages"], where=f"{where}.limits")

    headers = data.get("headers") or {}
    if not isinstance(headers, Mapping):
        raise McpConfigError(f"{where}.headers must be a mapping")

    return McpServerSpec(
        skill_full_name=skill_full_name,
        server=str(data["server"]),
        url=str(data["url"]),
        headers={str(key): str(value) for key, value in headers.items()},
        tools=_parse_tools(data.get("tools"), where=f"{where}.tools"),
        transport=transport,
        protocol=protocol,
        **extra,
    )


def load_mcp_specs(skill_dir: Path, *, skill_full_name: str) -> tuple[McpServerSpec, ...]:
    """读取一个技能的 MCP 声明。

    目录不存在 → 空元组（该技能不接 MCP，不是错误）。目录里没有 ``.yaml`` →
    空元组。任何一份文件非法 → 抛 :class:`McpConfigError`，**不做部分降级**：
    半加载的配置会让模型看到一份不完整的能力清单，比明确失败更难排查。
    """
    mcp_dir = skill_dir / MCP_DIR_NAME
    if not mcp_dir.is_dir():
        return ()

    files = sorted(mcp_dir.glob(f"*{MCP_SPEC_SUFFIX}"))
    if not files:
        return ()

    specs: list[McpServerSpec] = []
    seen: dict[str, Path] = {}
    for path in files:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise McpConfigError(f"{path}: cannot read MCP config: {exc}") from exc
        spec = _parse_server_file(payload, path=path, skill_full_name=skill_full_name)
        if spec.server in seen:
            raise McpConfigError(
                f"server name {spec.server!r} declared in both "
                f"{seen[spec.server].name} and {path.name}"
            )
        seen[spec.server] = path
        specs.append(spec)
    return tuple(specs)


def render_mcp_catalog(specs: Sequence[McpServerSpec]) -> str:
    """渲染随技能正文一起到达的工具目录。

    只包含「服务器.工具 — 一行用途」，**不含参数 schema**：目录每轮随上下文重发，
    带 schema 会让多技能会话线性膨胀。需要参数时再按需索取。

    措辞里显式声明「取代之前所有目录」：上下文是只追加的，无法删除历史消息，因此
    失效只能靠后一份声明覆盖前一份的语义。
    """
    lines: list[str] = [
        "<system-reminder>",
        "MCP tools available through this skill.",
        "This complete MCP catalog replaces every earlier MCP catalog.",
        "",
    ]
    servers = [spec for spec in specs if spec.tools]
    if not servers:
        lines.append("(none — this skill declares MCP servers but no tools)")
    else:
        for spec in servers:
            for tool in spec.tools:
                purpose = truncate_description(tool.description)
                lines.append(f"- {spec.server}.{tool.name} — {purpose}")
        lines.append("")
        example = servers[0]
        first = example.tools[0]
        lines.append(
            f'Call them with: mcp_call(server="{example.server}", '
            f'tool="{first.name}", arguments={{...}})'
        )
        lines.append("Ask load_skill again with server= and tool= for a tool's full input schema.")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def render_mcp_schema_block(
    specs: Sequence[McpServerSpec],
    *,
    server: str | None = None,
    tool: str | None = None,
    max_bytes: int = DEFAULT_MAX_SCHEMA_BYTES,
) -> str:
    """渲染参数详情。

    ``server`` 为空返回空串（目录模式不需要它）；指定 ``tool`` 时只输出该工具。
    超长 schema 会降级为可读摘要——静默丢弃会让模型完全看不到参数，只能靠猜。
    """
    if server is None:
        return ""
    selected = [spec for spec in specs if spec.server == server]
    if not selected:
        available = ", ".join(sorted(spec.server for spec in specs)) or "(none)"
        raise McpConfigError(f"unknown server {server!r}; available: {available}")

    spec = selected[0]
    tools = [item for item in spec.tools if tool is None or item.name == tool]
    if tool is not None and not tools:
        available = ", ".join(spec.tool_names) or "(none)"
        raise McpConfigError(f"unknown tool {tool!r} on server {server!r}; available: {available}")

    blocks: list[str] = []
    for item in tools:
        rendered = render_input_schema(item.input_schema, max_bytes=max_bytes)
        header = f"{spec.server}.{item.name} — {truncate_description(item.description)}"
        blocks.append(f"{header}\ninput_schema: {rendered.text}")
    return "\n\n".join(blocks)
