# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""v1 解析策略：``ghrah.agent-templates.v1``。

把 ``AgentTemplateConfig`` 的七组配置编码成 JSON 列文本，并从列文本解回配置。
编码一律 ``sort_keys`` + 紧凑分隔符，保证同一对象在库里只有一种字节形态。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ghrah.subject.template_store.models import AgentTemplateConfig

__all__ = ["V1Format"]

_ENCODING = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


def _dumps(value: Any) -> str:
    """规范化 JSON 文本（列值的唯一形态）。"""
    return json.dumps(value, **_ENCODING)


class V1Format:
    """``ghrah.agent-templates.v1``：字段形状见模板 JSON 对象文档 §三。"""

    name = "ghrah.agent-templates.v1"

    def to_columns(self, config: AgentTemplateConfig) -> dict[str, str]:
        """配置 → 七个 JSON 列文本。"""
        return {
            "metadata": _dumps(config.metadata.model_dump(mode="json")),
            "model": _dumps(config.model.model_dump(mode="json")),
            "prompt": _dumps(config.prompt.model_dump(mode="json")),
            "context_policy": _dumps(config.context_policy.model_dump(mode="json")),
            "available_members": _dumps(
                [item.model_dump(mode="json") for item in config.available_members]
            ),
            "permission_request": _dumps(config.permission_request.model_dump(mode="json")),
            "path_request": _dumps([item.model_dump(mode="json") for item in config.path_request]),
        }

    def from_columns(self, columns: Mapping[str, Any]) -> AgentTemplateConfig:
        """七个 JSON 列文本 → 配置（未知子字段由模型层拒绝）。"""
        return AgentTemplateConfig.model_validate(
            {
                "metadata": json.loads(columns["metadata"]),
                "model": json.loads(columns["model"]),
                "prompt": json.loads(columns["prompt"]),
                "context_policy": json.loads(columns["context_policy"]),
                "available_members": json.loads(columns["available_members"]),
                "permission_request": json.loads(columns["permission_request"]),
                "path_request": json.loads(columns["path_request"]),
            }
        )
