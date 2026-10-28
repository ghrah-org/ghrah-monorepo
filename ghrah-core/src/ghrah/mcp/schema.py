# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""工具参数 schema 的整形：紧凑序列化与体积上限。

这里**只做整形，不做转换**。工具定义对模型而言是固定不变的 ``mcp_call``，
参数 schema 只作为文本附在技能内容里供模型阅读，参数是否合法由 MCP 服务器
自己校验。因此不需要把任意 JSON Schema 收敛成本框架的参数模型——那一步既
有损（``$ref``/``anyOf`` 无法完整表达），又会引入第二套校验规则。

剩下唯一的真实问题是体积：单个工具的 schema 可能远超一次对话能承受的量。
超限时**降级为可读的摘要**而不是直接省略——静默丢弃会让模型完全看不到参数，
它只能凭猜测调用；给出顶层属性清单至少足够它判断「这个工具需要哪些字段」。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ghrah.mcp.errors import McpConfigError

__all__ = [
    "DEFAULT_MAX_SCHEMA_BYTES",
    "SchemaRender",
    "compact_json",
    "render_input_schema",
    "require_object_schema",
]

DEFAULT_MAX_SCHEMA_BYTES = 8192
"""单个参数 schema 的默认字节上限（按 UTF-8 计算）。"""

_MAX_SUMMARY_PROPERTIES = 30
"""摘要里最多列出的顶层属性数。超出部分只报数量。"""


@dataclass(frozen=True)
class SchemaRender:
    """一次 schema 整形的结果。"""

    text: str
    """给模型看的文本（紧凑 JSON 或摘要）。"""

    degraded: bool
    """是否因超限而降级为摘要。调用方可据此记一条 warning。"""

    original_bytes: int
    """原始紧凑 JSON 的字节数，便于定位「哪个工具特别大」。"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def compact_json(value: Any) -> str:
    """把值序列化成紧凑 JSON 文本。

    ``ensure_ascii=False`` 是刻意的：中文描述如果转成 ``\\uXXXX`` 会让
    字节数膨胀数倍，更容易触发体积上限，也更难读。
    """
    return _dumps(value)


def require_object_schema(schema: Any) -> Mapping[str, Any]:
    """校验一个参数 schema 是 JSON 对象，返回它。

    MCP 规定 ``inputSchema`` 是 JSON Schema 对象（零参数工具就是 ``{}``）。
    这里只校验最外层形状——内部关键字原样透传，不做语义检查。
    """
    if not isinstance(schema, Mapping):
        raise McpConfigError(f"input_schema must be a JSON object, got {type(schema).__name__}")
    return schema


def _type_label(prop: Any) -> str:
    """取一个属性的类型标签，尽力而为、绝不抛异常。"""
    if not isinstance(prop, Mapping):
        return "unknown"
    type_value = prop.get("type")
    if isinstance(type_value, str):
        return type_value
    if isinstance(type_value, list):
        parts = [str(item) for item in type_value]
        return "|".join(parts) if parts else "unknown"
    if "$ref" in prop:
        return "ref"
    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword in prop:
            return keyword
    return "unknown"


def _summarize(schema: Mapping[str, Any], size: int) -> str:
    """构造超限时的降级摘要。"""
    header = f"full schema omitted ({size} bytes)"
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return header + "; no top-level properties declared"

    required_raw = schema.get("required")
    required = set()
    if isinstance(required_raw, list | tuple):
        required = {item for item in required_raw if isinstance(item, str)}

    entries: list[str] = []
    names = list(properties.keys())
    for name in names[:_MAX_SUMMARY_PROPERTIES]:
        label = _type_label(properties[name])
        marker = " (required)" if name in required else ""
        entries.append(f"{name}: {label}{marker}")

    body = ", ".join(entries)
    if len(names) > _MAX_SUMMARY_PROPERTIES:
        body += f", … (+{len(names) - _MAX_SUMMARY_PROPERTIES} more)"

    return f"{header}; top-level properties: {body}"


def render_input_schema(
    schema: Mapping[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_SCHEMA_BYTES,
) -> SchemaRender:
    """把参数 schema 整形成给模型看的文本，超限则降级为摘要。"""
    if max_bytes <= 0:
        raise McpConfigError(f"max_bytes must be positive, got {max_bytes}")

    text = compact_json(schema)
    size = len(text.encode("utf-8"))
    if size <= max_bytes:
        return SchemaRender(text=text, degraded=False, original_bytes=size)

    return SchemaRender(text=_summarize(schema, size), degraded=True, original_bytes=size)
