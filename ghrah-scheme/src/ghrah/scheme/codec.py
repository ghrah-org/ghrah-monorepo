# SPDX-License-Identifier: Apache-2.0
"""单一作者的边界编解码；JSON Schema 只接收明确支持的有限子集。"""

from __future__ import annotations

import copy
import math
from typing import Any, Protocol, cast, get_args

from pydantic import BaseModel

from ghrah.scheme.models import JsonObject, JsonValue


class ActionCodec(Protocol):
    """从同一作者取得 schema、严格校验和隔离的 JSON 对象。"""

    @property
    def input_schema(self) -> dict[str, Any]: ...

    @property
    def output_schema(self) -> dict[str, Any]: ...

    def decode(self, value: JsonObject) -> JsonObject: ...

    def encode(self, value: JsonObject) -> JsonObject: ...


class PydanticCodec:
    """复用领域的严格模型，不复制一份 Scheme 专用字段。"""

    def __init__(self, input_model: type[BaseModel], output_model: type[BaseModel]) -> None:
        seen: set[type[BaseModel]] = set()

        def check(model: type[BaseModel]) -> None:
            if model in seen:
                return
            seen.add(model)
            if not model.model_config.get("strict") or model.model_config.get("extra") != "forbid":
                raise ValueError("领域模型必须严格并拒绝未知字段")

            def nested(annotation: Any) -> None:
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    check(annotation)
                for argument in get_args(annotation):
                    nested(argument)

            for field in model.model_fields.values():
                nested(field.annotation)

        for model in (input_model, output_model):
            check(model)
        self._input = input_model
        self._output = output_model

    @property
    def input_schema(self) -> dict[str, Any]:
        """返回领域输入描述。"""
        return self._input.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        """返回领域输出描述。"""
        return self._output.model_json_schema()

    def decode(self, value: JsonObject) -> JsonObject:
        """按领域作者校验输入。"""
        return self._input.model_validate(value, strict=True).model_dump(mode="json")

    def encode(self, value: JsonObject) -> JsonObject:
        """按领域作者校验结果。"""
        return self._output.model_validate(value, strict=True).model_dump(mode="json")


_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "anyOf",
    "default",
    "title",
    "description",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _check_schema(schema: dict[str, Any], depth: int = 0) -> None:
    if depth > 24 or not isinstance(schema, dict) or set(schema) - _KEYWORDS:
        raise ValueError("不支持的 schema 关键字或深度")
    if "anyOf" in schema:
        if set(schema) - {"anyOf", "title", "description"} or not schema["anyOf"]:
            raise ValueError("anyOf 必须独立声明非空分支")
        for branch in schema["anyOf"]:
            _check_schema(branch, depth + 1)
        return
    types = schema.get("type")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
        raise ValueError("required 必须是字段名数组")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("enum 必须是非空数组")
    literals = [*schema.get("enum", []), *([schema["const"]] if "const" in schema else [])]
    if any(type(value) not in {str, int, float, bool, type(None)} for value in literals):
        raise ValueError("此子集只支持标量 enum 和 const")
    for keyword in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema:
            bound = schema[keyword]
            if type(bound) not in {int, float} or (
                type(bound) is float and not math.isfinite(bound)
            ):
                raise ValueError("schema 约束必须是有限数值")
            if keyword not in {"minimum", "maximum"} and (type(bound) is not int or bound < 0):
                raise ValueError("长度约束必须是非负整数")
    if isinstance(types, list):
        if len(types) != 2 or "null" not in types:
            raise ValueError("只支持可空类型联合")
        for kind in types:
            _check_schema({**schema, "type": kind}, depth + 1)
        return
    if types not in _TYPES:
        raise ValueError("schema 必须声明受支持的 type")
    if types == "object":
        if schema.get("additionalProperties") is not False:
            raise ValueError("对象必须显式拒绝未知字段")
        properties = schema.get("properties", {})
        if (
            not isinstance(properties, dict)
            or not set(schema.get("required", [])) <= properties.keys()
        ):
            raise ValueError("无效的对象字段")
        for child in properties.values():
            _check_schema(child, depth + 1)
    if types == "array":
        _check_schema(schema.get("items", {}), depth + 1)
    # 不能接受一个已知关键字，却在不适用的类型上静默忽略它。
    constraints = {
        "properties": {"object"},
        "required": {"object"},
        "additionalProperties": {"object"},
        "items": {"array"},
        "minItems": {"array"},
        "maxItems": {"array"},
        "minimum": {"integer", "number"},
        "maximum": {"integer", "number"},
        "minLength": {"string"},
        "maxLength": {"string"},
    }
    for key, applicable in constraints.items():
        if key in schema and types not in applicable:
            raise ValueError(f"关键字 {key} 与类型不符")


def _literal_equal(left: Any, right: Any) -> bool:
    numeric = type(left) in {int, float} and type(right) in {int, float}
    return bool((type(left) is type(right) or numeric) and left == right)


def _validate(schema: dict[str, Any], value: JsonValue) -> None:
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _validate(branch, value)
                return
            except ValueError:
                pass
        raise ValueError("值不满足任何分支")
    kind = schema["type"]
    if isinstance(kind, list):
        kind = "null" if value is None else next(item for item in kind if item != "null")
    valid = {
        "null": value is None,
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    if not valid[kind]:
        raise ValueError("值类型与契约不符")
    if "enum" in schema and not any(_literal_equal(item, value) for item in schema["enum"]):
        raise ValueError("值不在枚举中")
    if "const" in schema and not _literal_equal(value, schema["const"]):
        raise ValueError("值不等于契约常量")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if set(value) - props.keys() or not set(schema.get("required", [])) <= value.keys():
            raise ValueError("未知或缺失的字段")
        for key, child in value.items():
            _validate(props[key], child)
    if isinstance(value, list):
        for child in value:
            _validate(schema["items"], child)
    for lower, upper, size in (
        ("minLength", "maxLength", len(value) if isinstance(value, str) else None),
        ("minItems", "maxItems", len(value) if isinstance(value, list) else None),
        ("minimum", "maximum", cast(int | float, value) if type(value) in {int, float} else None),
    ):
        if size is not None and (
            (type(size) is float and not math.isfinite(size))
            or size < schema.get(lower, -math.inf)
            or size > schema.get(upper, math.inf)
        ):
            raise ValueError("值超出契约范围")


class JsonSchemaCodec:
    """校验固定 JSON Schema 子集；不支持的引用和关键字在发布前拒绝。"""

    def __init__(self, input_schema: dict[str, Any], output_schema: dict[str, Any]) -> None:
        self._input = copy.deepcopy(input_schema)
        self._output = copy.deepcopy(output_schema)
        for schema in (self._input, self._output):
            _check_schema(schema)

    @property
    def input_schema(self) -> dict[str, Any]:
        """导出隔离的输入描述。"""
        return copy.deepcopy(self._input)

    @property
    def output_schema(self) -> dict[str, Any]:
        """导出隔离的输出描述。"""
        return copy.deepcopy(self._output)

    def decode(self, value: JsonObject) -> JsonObject:
        """严格验证输入；default 仅作声明，不隐式补值。"""
        _validate(self._input, value)
        return copy.deepcopy(value)

    def encode(self, value: JsonObject) -> JsonObject:
        """严格验证结果并隔离复制。"""
        _validate(self._output, value)
        return copy.deepcopy(value)
