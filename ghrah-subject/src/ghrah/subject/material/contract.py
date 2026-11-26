# SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""校验物料声明与模型提出的受控字段补丁。"""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

_PATCH = re.compile(r"```requirement\.patch\s*([\s\S]*?)```", re.IGNORECASE)


def json_text(value: Any) -> str:
    """生成可落盘的规范 JSON，拒绝非 JSON 数值。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class MaterialContract:
    """只允许声明字段的浅合并，并对合并后的完整数据再次校验。"""

    def __init__(self, data_schema: dict[str, Any], patch_schema: dict[str, Any] | None) -> None:
        for schema in (data_schema, patch_schema):
            if schema is None:
                continue
            if len(json_text(schema).encode()) > 262_144:
                raise ValueError("material schema exceeds 256 KiB")
            if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
                raise ValueError("material schema must declare object properties")
            self._check_refs(schema)
            Draft202012Validator.check_schema(schema)
        if patch_schema is not None and not set(patch_schema["properties"]) <= set(
            data_schema["properties"]
        ):
            raise ValueError("patch_schema contains fields outside data_schema")
        self.data_schema = data_schema
        self.patch_schema = patch_schema
        self._data_validator = Draft202012Validator(data_schema)
        self._patch_validator = (
            Draft202012Validator(patch_schema) if patch_schema is not None else None
        )

    @classmethod
    def _check_refs(cls, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    raise ValueError("material schemas may only use local references")
                cls._check_refs(value)
        elif isinstance(node, list):
            for value in node:
                cls._check_refs(value)

    def validate_data(self, data: dict[str, Any]) -> None:
        """拒绝未知字段和不满足完整数据契约的值。"""
        if len(json_text(data).encode()) > 1_048_576:
            raise ValueError("material data exceeds 1 MiB")
        if not set(data) <= set(self.data_schema["properties"]):
            raise ValueError("data contains undeclared fields")
        self._data_validator.validate(data)

    def apply(self, data: dict[str, Any], text: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """校验单条 Agent 消息，返回补丁及完整新数据，不直接写库。"""
        blocks = _PATCH.findall(text)
        if len(blocks) != 1:
            raise ValueError("expected one complete requirement.patch block")
        patch = json.loads(blocks[0])
        if not isinstance(patch, dict) or not patch:
            raise ValueError("patch must be a non-empty object")
        if self._patch_validator is None or self.patch_schema is None:
            raise ValueError("material does not allow patches")
        if not set(patch) <= set(self.patch_schema["properties"]):
            raise ValueError("patch contains undeclared fields")
        self._patch_validator.validate(patch)
        updated = {**data, **patch}
        self.validate_data(updated)
        return patch, updated
