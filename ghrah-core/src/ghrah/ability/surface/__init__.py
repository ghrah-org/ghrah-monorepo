# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 工具表面契约。

本包只提供表面条目、工具名算法、阈值默认值与 per-Agent 覆盖类型：
全部是带默认值的形状和纯函数，不读取授权库、不投影、不消费。表面
装配在后续实现中调用这些形状，不在本包内接线。
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
]
