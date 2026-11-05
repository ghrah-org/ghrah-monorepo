# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Manifest 域载荷模型（CRUD 命令、响应与事件）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ─── Manifest CRUD 载荷模型 ───


class ManifestListPayload(BaseModel):
    """manifest_list_abilities / manifest_list_agents 命令载荷。"""

    namespace: str | None = None


class ManifestGetPayload(BaseModel):
    """manifest_get_ability / manifest_get_agent 命令载荷。"""

    full_name: str


class ManifestPutPayload(BaseModel):
    """manifest_put_ability / manifest_put_agent 命令载荷。"""

    full_name: str
    content: str
    overwrite: bool = False


class ManifestDeletePayload(BaseModel):
    """manifest_delete_ability / manifest_delete_agent 命令载荷。"""

    full_name: str


class ManifestValidatePayload(BaseModel):
    """manifest_validate 命令载荷。"""

    content: str
    manifest_type: str


class ManifestResolvePayload(BaseModel):
    """manifest_resolve_agent 命令载荷。"""

    agent_full_name: str
    runtime_name: str | None = None
    content: str | None = None


# ─── Manifest CRUD 响应载荷模型 ───


class ManifestGetAbilityResponsePayload(BaseModel):
    """manifest_get_ability 响应载荷。"""

    manifest: dict[str, Any]
    source: str


class ManifestGetAgentResponsePayload(BaseModel):
    """manifest_get_agent 响应载荷。"""

    manifest: dict[str, Any]
    source: str


class ManifestListErrorPayload(BaseModel):
    """列表中无法读取的单个声明；不得冒充完整摘要。"""

    full_name: str
    error: str


class ManifestListAbilitiesResponsePayload(BaseModel):
    """有效能力摘要与逐条读取错误，二者明确分开。"""

    abilities: list[dict[str, Any]]
    errors: list[ManifestListErrorPayload] = Field(default_factory=list)


class ManifestPutResponsePayload(BaseModel):
    """manifest_put_ability / manifest_put_agent 响应载荷。"""

    full_name: str


class ManifestDeleteResponsePayload(BaseModel):
    """manifest_delete_ability / manifest_delete_agent 响应载荷。"""

    full_name: str


class ManifestValidateResponsePayload(BaseModel):
    """manifest_validate 响应载荷。"""

    valid: bool
    errors: list[str]


class ManifestResolveAgentResponsePayload(BaseModel):
    """manifest_resolve_agent 响应载荷。"""

    agent_full_name: str
    ability_source: Literal["legacy", "builtin"]
    input_source: Literal["stored", "draft"]
    config: dict[str, Any]
    abilities: list[dict[str, Any]]  # ─── Manifest 事件载荷模型 ───


class ManifestAbilityEventPayload(BaseModel):
    """manifest_ability_created/updated/deleted 事件载荷。"""

    full_name: str
    namespace: str
    manifest: dict[str, Any] | None = None
    source: str | None = None


class ManifestAgentEventPayload(BaseModel):
    """manifest_agent_created/updated/deleted 事件载荷。"""

    full_name: str
    namespace: str
    manifest: dict[str, Any] | None = None
    source: str | None = None
