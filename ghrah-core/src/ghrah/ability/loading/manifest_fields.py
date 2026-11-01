# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""复用声明共用字段和错误，避免能力来源与 Agent 配置维护不同类型。

SUPPORTED_VERSIONS 仅保留旧 Ability 视图的版本口径；Agent 版本由唯一的
AgentManifest 作者校验，不再借用能力视图版本。
"""

from ghrah.manifest.errors import (
    DuplicateManifestError,
    ManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
    ManifestVersionError,
)
from ghrah.manifest.types import (
    SUPPORTED_ABILITY_VERSIONS,
    HookDef,
    ImplementationDef,
    PermissionFlags,
    ToolParameter,
    ToolSchema,
)

SUPPORTED_VERSIONS = SUPPORTED_ABILITY_VERSIONS

__all__ = [
    "SUPPORTED_VERSIONS",
    "DuplicateManifestError",
    "HookDef",
    "ImplementationDef",
    "ManifestError",
    "ManifestNotFoundError",
    "ManifestValidationError",
    "ManifestVersionError",
    "PermissionFlags",
    "ToolParameter",
    "ToolSchema",
]
