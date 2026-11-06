# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 工具表面契约。

本包提供表面条目、工具名算法、阈值默认值、per-Agent 覆盖类型与授权面
投影：形状和纯函数不读关系库，投影只消费已有的关系库投影（``AbilityTreeView``）。
静态集裁切与搜索索引属于表面装配在后续实现中的接线。
"""

from __future__ import annotations

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
from ghrah.ability.surface.projection import project_grants
from ghrah.ability.surface.types import MemberRef, SurfaceEntry

__all__ = [
    "DEFAULT_DEFINITION_TOKEN_RATIO",
    "DEFAULT_HARD_CAP",
    "DEFAULT_LOADED_LIMIT",
    "DEFAULT_MAX_STATIC",
    "DEFAULT_SEARCH_LIMIT",
    "META_TOOL_NAMES",
    "RESERVED_TOOL_NAMES",
    "RUN_SCRIPT_TOOL_NAME",
    "SEARCH_LIMIT_ABS_MAX",
    "STATIC_ABS_LIMIT",
    "TOOL_NAME_MAX",
    "TOOL_NAME_PATTERN",
    "MemberRef",
    "NamedMember",
    "SurfaceConfig",
    "SurfaceEntry",
    "ToolNameError",
    "assign_tool_names",
    "project_grants",
]
