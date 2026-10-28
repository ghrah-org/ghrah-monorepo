# SPDX-License-Identifier: Apache-2.0
"""模型引用的迁移诊断；绝不把旧 Agent 名称当成端点别名。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class EndpointReferenceModel(BaseModel):
    """含模型引用的输入共享旧字段检查。"""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_reference(cls, value: Any) -> Any:
        """旧引用必须先使用 Agent 导出离线转换，不能被 extra=ignore 吞掉。"""
        if isinstance(value, dict) and "agent_config_name" in value:
            raise ValueError(
                "agent_config_name was removed; migrate via Agent export to endpoint_name "
                "using scripts/migrate_agentconf_consumer.py"
            )
        return value
