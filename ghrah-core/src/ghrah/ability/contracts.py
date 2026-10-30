# SPDX-License-Identifier: Apache-2.0
"""从成员的唯一成功数据 schema 导出只读文件动作结果契约。"""

from typing import Any


def operation_result_schema(success: dict[str, Any]) -> dict[str, Any]:
    """关联成功／失败标记和数据，保留现有 ActionResult 语义。"""
    error = {
        "type": "object",
        "properties": {"error": {"type": "string"}},
        "required": ["error"],
        "additionalProperties": False,
    }
    return {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "const": outcome},
                    "data": data,
                    "next_action_hint": {"type": ["string", "null"]},
                },
                "required": ["outcome", "data", "next_action_hint"],
                "additionalProperties": False,
            }
            for outcome, data in (("success", success), ("failure", error))
        ]
    }
