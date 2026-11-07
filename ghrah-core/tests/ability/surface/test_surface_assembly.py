# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""三种规模、三种模式、token 边界与显式静态配置的行为验收。"""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from ghrah.ability.surface import (
    DEFAULT_SURFACE_CONTEXT_LENGTH,
    STATIC_ABS_LIMIT,
    SurfaceAssemblyError,
    SurfaceConfig,
    SurfaceThresholds,
    assemble_surface,
    definition_tokens,
)
from ghrah.context.token_estimator import effective_length
from ghrah.core.unit import CoreUnitConfig


@pytest.mark.parametrize("count", [5, 50, 200])
def test_scale_thresholds(count, make_tool):
    tools = [make_tool(f"tool_{number:03}") for number in range(count)]
    result = assemble_surface(tools, config=SurfaceConfig(max_static=12), max_context_length=128000)
    assert len(result.static) == min(count, 12)
    assert len(result.static) + len(result.long_tail) == count
    assert result.mode == ("static" if count == 5 else "progressive")
    assert len(result.meta_tools) == (0 if count == 5 else 3)
    assert len(result.static) + len(result.meta_tools) <= 15
    assert result.static_definition_tokens <= result.token_budget
    assert all(entry.static for entry in result.static)
    assert not any(entry.static for entry in result.long_tail)
    assert result == assemble_surface(
        reversed(tools), config=SurfaceConfig(max_static=12), max_context_length=128000
    )


@pytest.mark.parametrize("window", [None, 0, -1, False, "unknown"])
def test_missing_context_is_conservative(window, make_tool):
    tools = [make_tool(f"tool_{number}") for number in range(5)]
    result = assemble_surface(tools, max_context_length=window)
    assert result.max_context_length == DEFAULT_SURFACE_CONTEXT_LENGTH == 8192
    assert result.static_definition_tokens <= int(8192 * 0.02)


def test_token_threshold_includes_complete_schema_and_cjk(make_tool):
    tool = replace(
        make_tool("read_file", description="读取文件"),
        input_schema={"type": "object", "description": "字段" * 2000},
    )
    expected = effective_length(json.dumps(tool.definition(), ensure_ascii=False, sort_keys=True))
    assert definition_tokens(tool) == expected
    result = assemble_surface([tool], max_context_length=8192)
    assert result.mode == "progressive"
    assert result.static == ()
    assert len(result.long_tail) == 1


def test_all_three_threshold_boundaries(make_tool):
    tools = [make_tool(f"tool_{number}") for number in range(4)]
    total = sum(definition_tokens(tool) for tool in tools)
    thresholds = SurfaceThresholds(definition_token_ratio=1, max_static=4, hard_cap=4)
    assert assemble_surface(tools, thresholds=thresholds, max_context_length=total).mode == "static"
    assert (
        assemble_surface(tools, thresholds=thresholds, max_context_length=total - 1).mode
        == "progressive"
    )
    for settings in (
        thresholds.model_copy(update={"max_static": 3}),
        thresholds.model_copy(update={"hard_cap": 3}),
    ):
        assert (
            assemble_surface(tools, thresholds=settings, max_context_length=total * 100).mode
            == "progressive"
        )


def test_static_forces_full_set_up_to_absolute_limit(make_tool):
    tools = [make_tool(f"tool_{number:03}") for number in range(STATIC_ABS_LIMIT + 1)]
    result = assemble_surface(
        tools[:-1], config=SurfaceConfig(mode="static", max_static=1), max_context_length=1
    )
    assert len(result.static) == STATIC_ABS_LIMIT
    assert result.long_tail == result.meta_tools == ()
    with pytest.raises(SurfaceAssemblyError, match="64"):
        assemble_surface(tools, config=SurfaceConfig(mode="static"))


def test_progressive_only_keeps_explicit_static_full_names(make_tool):
    tools = [make_tool("alpha"), make_tool("beta")]
    result = assemble_surface(
        tools,
        config=SurfaceConfig(mode="progressive", static=["example.files.beta"]),
        max_context_length=1000000,
    )
    assert [entry.tool_name for entry in result.static] == ["beta"]
    assert [entry.tool_name for entry in result.long_tail] == ["alpha"]
    assert set(result.meta_tools) == {"search_tools", "describe_tool", "execute_tool"}


def test_explicit_static_overrides_auto_budget_but_has_absolute_limit(make_tool):
    tools = [make_tool(f"tool_{number:03}") for number in range(65)]
    result = assemble_surface(
        tools,
        config=SurfaceConfig(static=[tool.full_name for tool in tools[:25]], max_static=1),
        max_context_length=1,
    )
    assert len(result.static) == 25
    with pytest.raises(SurfaceAssemblyError, match="64"):
        assemble_surface(tools, config=SurfaceConfig(static=[tool.full_name for tool in tools]))


@pytest.mark.parametrize("name", ["unknown.member", "alpha", "search_tools", "example.files.ghost"])
def test_invalid_static_config_rejected(name, make_tool):
    with pytest.raises(SurfaceAssemblyError):
        assemble_surface([make_tool("alpha")], config=SurfaceConfig(static=[name]))


def test_ambiguous_full_name_and_duplicate_tool_rejected(make_tool):
    alpha = make_tool("alpha")
    with pytest.raises(SurfaceAssemblyError, match="重复"):
        assemble_surface([alpha, alpha])
    alias = replace(alpha, entry=alpha.entry.model_copy(update={"tool_name": "other"}))
    with pytest.raises(SurfaceAssemblyError, match="不唯一"):
        assemble_surface([alpha, alias], config=SurfaceConfig(static=[alpha.full_name]))


def test_unavailable_and_reserved_tools_require_host_filter(make_tool):
    tool = make_tool("alpha")
    with pytest.raises(SurfaceAssemblyError, match="不可执行"):
        assemble_surface([replace(tool, entry=tool.entry.model_copy(update={"available": False}))])
    with pytest.raises(SurfaceAssemblyError, match="宿主"):
        assemble_surface([make_tool("execute_tool")])


def test_core_defaults_and_agent_override(make_tool):
    core = CoreUnitConfig(surface_max_static=2, surface_hard_cap=10)
    thresholds = SurfaceThresholds.from_core_config(core)
    tools = [make_tool(f"tool_{number}") for number in range(5)]
    assert (
        len(assemble_surface(tools, thresholds=thresholds, max_context_length=128000).static) == 2
    )
    result = assemble_surface(
        tools, thresholds=thresholds, config=SurfaceConfig(max_static=3), max_context_length=128000
    )
    assert len(result.static) == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"definition_token_ratio": 0},
        {"definition_token_ratio": float("nan")},
        {"definition_token_ratio": 2},
        {"max_static": 0},
        {"hard_cap": 0},
    ],
)
def test_invalid_core_thresholds_rejected(overrides):
    with pytest.raises(ValidationError):
        SurfaceThresholds(**overrides)
