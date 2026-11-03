# SPDX-License-Identifier: Apache-2.0
"""文件切片明确支持的成员及参数映射，不按未知成员猜测执行入口。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ghrah.ability.builtin.fs.operations.list_directory import ListDirectoryAbility
from ghrah.ability.builtin.fs.operations.read_file import ReadFileAbility
from ghrah.ability.execution.base import Ability


@dataclass(frozen=True)
class FileMember:
    """一个只读后端、它的路径参数与成功数据契约。

    ``output_schema`` 是文件切片自己的适配事实：实现只返回 ``ActionResult`` 数据，
    不声明成功形状；定义格式也不再承载 schema（成员只索引入口文件）。因此把这两个
    成员的输出契约钉在这里，由 :func:`ghrah.ability.versions.capture_file_version`
    盖章进快照。输入契约不在这里——它是实现自己的 ``bind_tool()``。
    """

    operation: type[Ability]
    path_argument: str
    output_schema: dict[str, Any]


FILE_OPERATIONS = MappingProxyType(
    {
        "read_file": FileMember(
            ReadFileAbility,
            "file_path",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "file_path": {"type": "string"},
                    "total_lines": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["content", "file_path", "total_lines"],
                "additionalProperties": False,
            },
        ),
        "list_directory": FileMember(
            ListDirectoryAbility,
            "dir_path",
            {
                "type": "object",
                "properties": {
                    "dir_path": {"type": "string"},
                    "total_entries": {"type": "integer"},
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string", "enum": ["file", "directory"]},
                                "size": {"type": "integer"},
                                "path": {"type": "string"},
                            },
                            "required": ["name", "type", "path"],
                            "additionalProperties": False,
                        },
                    },
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "truncated": {"type": "boolean"},
                    "next_offset": {"type": ["integer", "null"]},
                },
                "required": ["dir_path", "total_entries", "entries"],
                "additionalProperties": False,
            },
        ),
    }
)


def closed_schema(schema: Any) -> Any:
    """把实现给出的 schema 收成契约平面要求的形状。

    Scheme 的 JSON codec 要求每个对象显式 ``additionalProperties: false``，而
    pydantic 的 ``model_json_schema()`` 默认不写；顺带去掉 ``title``（工具面不消费）。
    只做这两件事，类型与约束原样保留。

    Args:
        schema: 任意 JSON Schema 片段。

    Returns:
        处理后的片段（非映射原样返回）。
    """
    if not isinstance(schema, dict):
        return schema
    result = {key: closed_schema(value) for key, value in schema.items() if key != "title"}
    if "properties" in result:
        result.setdefault("additionalProperties", False)
    return result


def supports_file_operation(definition: dict, key: str) -> bool:
    """后端按显式槽契约匹配，不能因成员名相同而替未知后端执行。"""
    operation = definition["operations"].get(key)
    if key not in FILE_OPERATIONS or operation is None or operation.get("script") is not None:
        return False
    route = operation["implementation"]
    requirement = definition["provider_requirements"].get(route["provider_slot"], {})
    return (
        route["operation"] == key
        and requirement.get("contract") == "ghrah.fs.v1"
        and set(requirement.get("required_capabilities", [])) <= {"request"}
    )
