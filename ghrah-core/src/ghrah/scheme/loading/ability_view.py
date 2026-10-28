# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力的运行时视图：由定义面 + 实现面投影而来，喂给展示与工具面注入。

**不是能力的定义来源**——定义在 ``ability/<容器>/ability.yaml``
（见 ``ability_schema``），本模块是其投影结果的形状。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghrah.scheme.loading.manifest_fields import (
    SUPPORTED_VERSIONS,
    HookDef,
    ImplementationDef,
    ManifestVersionError,
    PermissionFlags,
    ToolSchema,
)

__all__ = [
    "AbilityHooks",
    "AbilityManifest",
    "AbilityMetadata",
]


class AbilityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.]*[a-z0-9]$")
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    title: str = ""
    description: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    icon: str = ""
    deprecated: bool = False
    since: str = ""
    permissions: PermissionFlags


class AbilityHooks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pre_execute: list[HookDef] = Field(default_factory=list)
    post_execute: list[HookDef] = Field(default_factory=list)
    on_error: list[HookDef] = Field(default_factory=list)


class AbilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: Literal["ability"]
    version: str = Field(pattern=r"^\d+(\.\d+)*$")

    metadata: AbilityMetadata
    tool: ToolSchema | None = None
    implementation: ImplementationDef
    hooks: AbilityHooks = Field(default_factory=AbilityHooks)

    @model_validator(mode="after")
    def _validate_version(self) -> AbilityManifest:
        if self.version.split(".")[0] not in SUPPORTED_VERSIONS:
            raise ManifestVersionError(
                f"Unsupported manifest version: {self.version!r}. "
                f"Supported major versions: {sorted(SUPPORTED_VERSIONS)}"
            )
        return self

    @model_validator(mode="after")
    def _validate_tool_required_for_remote(self) -> AbilityManifest:
        if self.tool is None and self.implementation.type in ("sandbox", "wasm"):
            raise ValueError(
                f"tool is required when implementation.type is '{self.implementation.type}'"
            )
        return self

    @property
    def full_name(self) -> str:
        return f"{self.metadata.namespace}.{self.metadata.name}"
