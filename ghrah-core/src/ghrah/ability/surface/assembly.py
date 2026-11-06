# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""工具定义预算与静态/长尾装配；不改变授权，也不注册元工具执行对象。"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.surface.catalog import SurfaceTool, read_agent_catalog
from ghrah.ability.surface.config import (
    DEFAULT_DEFINITION_TOKEN_RATIO,
    DEFAULT_HARD_CAP,
    DEFAULT_MAX_STATIC,
    STATIC_ABS_LIMIT,
    SurfaceConfig,
)
from ghrah.ability.surface.naming import RESERVED_TOOL_NAMES
from ghrah.ability.surface.types import SurfaceEntry
from ghrah.context.token_estimator import effective_length

if TYPE_CHECKING:
    from ghrah.ability.access.store import AbilityAccessStore
    from ghrah.core.unit import CoreUnitConfig

DEFAULT_SURFACE_CONTEXT_LENGTH = 8192
"""模型窗口缺失或非正时按 8K 估算，避免按大型模型窗口乐观装配。"""

META_TOOL_ORDER = ("search_tools", "describe_tool", "execute_tool")


class SurfaceAssemblyError(ValueError):
    """显式常驻配置无效或超过固定绝对上限。"""


class SurfaceThresholds(BaseModel):
    """经过校验的 Core 默认阈值；Agent 配置只覆盖 mode/static/max_static。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    definition_token_ratio: float = Field(default=DEFAULT_DEFINITION_TOKEN_RATIO, gt=0, le=1)
    max_static: int = Field(default=DEFAULT_MAX_STATIC, ge=1)
    hard_cap: int = Field(default=DEFAULT_HARD_CAP, ge=1)

    @classmethod
    def from_core_config(cls, config: CoreUnitConfig) -> SurfaceThresholds:
        """读取 CoreUnitConfig 的表面字段，不维护第二份部署配置。"""
        return cls(
            definition_token_ratio=config.surface_definition_token_ratio,
            max_static=config.surface_max_static,
            hard_cap=config.surface_hard_cap,
        )


@dataclass(frozen=True)
class SurfaceAssembly:
    """一次装配结果。元工具列表只声明所需入口，实际授予与执行由宿主完成。

    token 统计只含 Operation 的函数定义，不含尚未装配的元工具、控制工具、
    run_script 或已加载集；后续宿主注入这些定义时必须另计预算。
    """

    mode: Literal["static", "progressive"]
    static: tuple[SurfaceEntry, ...]
    long_tail: tuple[SurfaceEntry, ...]
    meta_tools: tuple[str, ...]
    definition_tokens: int
    static_definition_tokens: int
    max_context_length: int
    token_budget: int


def definition_tokens(tool: SurfaceTool) -> int:
    """按共用的中英文加权估算口径计算真实函数定义，包括完整输入 schema。"""
    encoded = json.dumps(tool.definition(), ensure_ascii=False, sort_keys=True)
    return max(1, effective_length(encoded))


def assemble_surface(
    tools: Iterable[SurfaceTool],
    *,
    config: SurfaceConfig | None = None,
    thresholds: SurfaceThresholds | None = None,
    max_context_length: int | None = None,
) -> SurfaceAssembly:
    """将已过滤的可执行授权目录分为常驻与长尾，不读取或扩大授权。

    auto 在定义 token、max_static、hard_cap 任一超限时切换渐进。渐进下先
    保留显式成员，再按工具名排序补齐预算内的成员；显式成员可以覆盖自动预算，
    但超过 64 个即报错。强制 progressive 仅保留显式成员。静态列表按包定义的
    namespace.name.member 全名解析；未知、保留名或多实例歧义均拒绝。
    """
    config = config or SurfaceConfig()
    thresholds = thresholds or SurfaceThresholds()
    window = (
        max_context_length
        if isinstance(max_context_length, int)
        and not isinstance(max_context_length, bool)
        and max_context_length > 0
        else DEFAULT_SURFACE_CONTEXT_LENGTH
    )
    budget = int(window * thresholds.definition_token_ratio)
    maximum = config.max_static if config.max_static is not None else thresholds.max_static
    candidates = sorted(tools, key=lambda tool: tool.entry.tool_name)
    names = [tool.entry.tool_name for tool in candidates]
    if len(set(names)) != len(names):
        raise SurfaceAssemblyError("工具名重复，无法唯一装配")
    if any(name in RESERVED_TOOL_NAMES for name in names):
        raise SurfaceAssemblyError("元工具与脚本入口必须由宿主单独装配")
    if any(not tool.entry.available for tool in candidates):
        raise SurfaceAssemblyError("不可执行成员不能进入原生工具装配")

    pinned: set[str] = set()
    for full_name in config.static:
        matches = [tool for tool in candidates if tool.full_name == full_name]
        if full_name in RESERVED_TOOL_NAMES or len(matches) != 1:
            raise SurfaceAssemblyError(f"静态成员全名不存在、保留或不唯一：{full_name}")
        pinned.add(matches[0].entry.tool_name)
    if len(pinned) > STATIC_ABS_LIMIT:
        raise SurfaceAssemblyError(f"显式静态集超过 {STATIC_ABS_LIMIT} 个成员")

    costs = {tool.entry.tool_name: definition_tokens(tool) for tool in candidates}
    total = sum(costs.values())
    progressive = config.mode == "progressive" or (
        config.mode == "auto"
        and (total > budget or len(candidates) > maximum or len(candidates) > thresholds.hard_cap)
    )
    if not progressive:
        if len(candidates) > STATIC_ABS_LIMIT:
            raise SurfaceAssemblyError(f"静态集超过 {STATIC_ABS_LIMIT} 个成员")
        selected = set(names)
    else:
        selected = pinned.copy()
        spent = sum(costs[name] for name in selected)
        if config.mode == "auto":
            for name in names:
                if name in selected:
                    continue
                if len(selected) >= min(maximum, STATIC_ABS_LIMIT):
                    break
                if spent + costs[name] <= budget:
                    selected.add(name)
                    spent += costs[name]

    return SurfaceAssembly(
        mode="progressive" if progressive else "static",
        static=tuple(
            tool.entry.model_copy(update={"static": True})
            for tool in candidates
            if tool.entry.tool_name in selected
        ),
        long_tail=tuple(
            tool.entry.model_copy(update={"static": False})
            for tool in candidates
            if tool.entry.tool_name not in selected
        ),
        meta_tools=META_TOOL_ORDER if progressive else (),
        definition_tokens=total,
        static_definition_tokens=sum(costs[name] for name in selected),
        max_context_length=window,
        token_budget=budget,
    )


async def assemble_agent_surface(
    agent: str,
    run: str,
    *,
    store: AbilityAccessStore,
    backend_available: Callable[[SurfaceEntry], bool],
    config: SurfaceConfig | None = None,
    thresholds: SurfaceThresholds | None = None,
    max_context_length: int | None = None,
) -> SurfaceAssembly:
    """从固定 Agent/run 的当前关系快照装配；后端检查由宿主注入。"""
    catalog = await read_agent_catalog(agent, run, store=store)
    return assemble_surface(
        (tool for tool in catalog.tools if tool.entry.available and backend_available(tool.entry)),
        config=config,
        thresholds=thresholds,
        max_context_length=max_context_length,
    )
