# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Manifest 声明式 Agent 定义系统。

提供 YAML manifest 的解析、验证和运行时桥接。

"""

from __future__ import annotations

from ghrah.manifest.ability import AbilityHooks, AbilityManifest, AbilityMetadata
from ghrah.manifest.agent import (
    AbilityRef,
    AgentHooks,
    AgentManifest,
    AgentMetadata,
    ContextOverrides,
    ModelConfig,
    PersistenceOverrides,
    WindowOverrides,
)
from ghrah.manifest.errors import (
    DuplicateManifestError,
    ManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
    ManifestVersionError,
)
from ghrah.manifest.protocols import ManifestStoreProtocol
from ghrah.manifest.resolver import ManifestResolver, ResolvedAbility, ResolvedAgent
from ghrah.manifest.responses import (
    AbilityManifestData,
    AgentManifestData,
    ManifestDeleteResult,
    ManifestPutResult,
    ResolvedAbilityData,
    ResolvedAgentData,
    ValidateResult,
)
from ghrah.manifest.types import (
    SUPPORTED_ABILITY_VERSIONS,
    SUPPORTED_AGENT_VERSIONS,
    HookDef,
    ImplementationDef,
    PermissionFlags,
    ToolParameter,
    ToolSchema,
)

__all__ = [
    "AbilityHooks",
    "AbilityManifest",
    "AbilityManifestData",
    "AbilityMetadata",
    "AbilityRef",
    "AgentHooks",
    "AgentManifest",
    "AgentManifestData",
    "AgentMetadata",
    "ContextOverrides",
    "DuplicateManifestError",
    "HookDef",
    "ImplementationDef",
    "ManifestDeleteResult",
    "ManifestError",
    "ManifestNotFoundError",
    "ManifestPutResult",
    "ManifestResolver",
    "ManifestStoreProtocol",
    "ManifestValidationError",
    "ManifestVersionError",
    "ModelConfig",
    "PersistenceOverrides",
    "PermissionFlags",
    "ResolvedAbility",
    "ResolvedAbilityData",
    "ResolvedAgent",
    "ResolvedAgentData",
    "SUPPORTED_AGENT_VERSIONS",
    "SUPPORTED_ABILITY_VERSIONS",
    "ToolParameter",
    "ToolSchema",
    "ValidateResult",
    "WindowOverrides",
]
