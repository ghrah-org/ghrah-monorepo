# 冻结快照（2026-10-26）：本文件是能力侧旧声明栈的历史副本，只作备份与参考。
# 它不属于任何包、不被导入、不参与构建与测试。
# 本目录的删除条件见同目录 README.md；import 路径已随重命名刷新。
#
# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any

from ghrah.scheme.ability import AbilityHooks, AbilityManifest
from ghrah.scheme.agent import AgentManifest
from ghrah.scheme.types import ImplementationDef, PermissionFlags, ToolSchema
from pydantic import BaseModel

__all__ = [
    "AbilityManifestData",
    "AgentManifestData",
    "ManifestPutResult",
    "ManifestDeleteResult",
    "ValidateResult",
    "ResolvedAbilityData",
    "ResolvedAgentData",
]


class AbilityManifestData(BaseModel):
    """manifest_get_ability 响应数据：解析后的模型 + 原始 YAML 源"""

    manifest: AbilityManifest
    source: str


class AgentManifestData(BaseModel):
    """manifest_get_agent 响应数据：解析后的模型 + 原始 YAML 源"""

    manifest: AgentManifest
    source: str


class ManifestPutResult(BaseModel):
    """manifest_put_ability / manifest_put_agent 响应数据"""

    full_name: str


class ManifestDeleteResult(BaseModel):
    """manifest_delete_ability / manifest_delete_agent 响应数据"""

    full_name: str


class ValidateResult(BaseModel):
    """manifest_validate 响应数据"""

    valid: bool
    errors: list[str]


class ResolvedAbilityData(BaseModel):
    """manifest_resolve_agent 响应中的 ability 数据（完整字段）"""

    ability_name: str
    tool_schema: ToolSchema | None = None
    permissions: PermissionFlags
    implementation: ImplementationDef
    hooks: AbilityHooks


class ResolvedAgentData(BaseModel):
    """manifest_resolve_agent 响应数据"""

    config: dict[str, Any]
    abilities: list[ResolvedAbilityData]
