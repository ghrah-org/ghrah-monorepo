# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 工具表面契约。

本包提供表面条目、工具名算法、阈值默认值、per-Agent 覆盖类型与授权面
投影、预算装配与授权域内的搜索索引。所有目录从关系库单向派生，不保存第二份授权。
"""

from __future__ import annotations

from ghrah.ability.surface.assembly import (
    DEFAULT_SURFACE_CONTEXT_LENGTH,
    SurfaceAssembly,
    SurfaceAssemblyError,
    SurfaceThresholds,
    assemble_agent_surface,
    assemble_surface,
    definition_tokens,
)
from ghrah.ability.surface.catalog import SurfaceCatalog, SurfaceTool
from ghrah.ability.surface.config import (
    DEFAULT_DEFINITION_TOKEN_RATIO,
    DEFAULT_HARD_CAP,
    DEFAULT_LOADED_LIMIT,
    DEFAULT_MAX_STATIC,
    DEFAULT_SEARCH_LIMIT,
    SEARCH_LIMIT_ABS_MAX,
    STATIC_ABS_LIMIT,
    SurfaceConfig,
)
from ghrah.ability.surface.naming import (
    META_TOOL_NAMES,
    RESERVED_TOOL_NAMES,
    RUN_SCRIPT_TOOL_NAME,
    TOOL_NAME_MAX,
    TOOL_NAME_PATTERN,
    NamedMember,
    ToolNameError,
    assign_tool_names,
)
from ghrah.ability.surface.projection import project_grants, project_surface
from ghrah.ability.surface.search import AgentToolSearch, SearchDocument, ToolSearchIndex
from ghrah.ability.surface.types import MemberRef, SurfaceEntry

__all__ = [
    "DEFAULT_DEFINITION_TOKEN_RATIO",
    "DEFAULT_HARD_CAP",
    "DEFAULT_LOADED_LIMIT",
    "DEFAULT_MAX_STATIC",
    "DEFAULT_SEARCH_LIMIT",
    "DEFAULT_SURFACE_CONTEXT_LENGTH",
    "META_TOOL_NAMES",
    "RESERVED_TOOL_NAMES",
    "RUN_SCRIPT_TOOL_NAME",
    "SEARCH_LIMIT_ABS_MAX",
    "STATIC_ABS_LIMIT",
    "TOOL_NAME_MAX",
    "TOOL_NAME_PATTERN",
    "MemberRef",
    "NamedMember",
    "AgentToolSearch",
    "SearchDocument",
    "SurfaceAssembly",
    "SurfaceAssemblyError",
    "SurfaceCatalog",
    "SurfaceConfig",
    "SurfaceEntry",
    "SurfaceThresholds",
    "SurfaceTool",
    "ToolSearchIndex",
    "ToolNameError",
    "assign_tool_names",
    "assemble_agent_surface",
    "assemble_surface",
    "definition_tokens",
    "project_grants",
    "project_surface",
]
