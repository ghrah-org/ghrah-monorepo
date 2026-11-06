# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""工具名算法的确定性、重名限定、保留名与边界测试。"""

from __future__ import annotations

import hashlib

import pytest

from ghrah.ability.surface import (
    META_TOOL_NAMES,
    RESERVED_TOOL_NAMES,
    TOOL_NAME_MAX,
    ToolNameError,
    assign_tool_names,
)


def test_unique_member_keys_keep_bare_names() -> None:
    """授权面内成员键唯一时，工具名就是成员键。"""
    result = assign_tool_names([("fs", "read_file"), ("web", "search")])
    assert [(item.tool_name, item.package, item.member) for item in result] == [
        ("read_file", "fs", "read_file"),
        ("search", "web", "search"),
    ]


def test_empty_surface_is_allowed() -> None:
    """空授权面得到空结果，不报错。"""
    assert assign_tool_names([]) == []


def test_result_is_sorted_by_tool_name() -> None:
    """结果按工具名升序，不跟随输入顺序。"""
    result = assign_tool_names([("pkg", "z"), ("pkg", "a"), ("pkg", "m")])
    assert [item.tool_name for item in result] == ["a", "m", "z"]


def test_duplicate_member_keys_are_qualified() -> None:
    """同一成员键出现多次时全部改为 ``<包名>__<成员键>``。"""
    result = assign_tool_names([("web", "search"), ("docs", "search")])
    assert [(item.tool_name, item.package) for item in result] == [
        ("docs__search", "docs"),
        ("web__search", "web"),
    ]


def test_qualified_names_replace_hyphen_with_underscore() -> None:
    """限定名两段里的 ``-`` 都换成 ``_``。"""
    result = assign_tool_names([("web-app", "search"), ("docs-app", "search")])
    assert [item.tool_name for item in result] == ["docs_app__search", "web_app__search"]


def test_exact_length_name_is_not_truncated() -> None:
    """恰好 64 字符的候选名不触发截断。"""
    package = "a" * 56
    other = "b" * 56
    result = {
        item.package: item.tool_name
        for item in assign_tool_names([(package, "search"), (other, "search")])
    }
    assert len(result[package]) == TOOL_NAME_MAX
    assert result[package] == f"{package}__search"


def test_long_name_is_truncated_with_hash() -> None:
    """超长名字取前 54 字符 + ``__`` + 候选名 sha256 前 8 位。"""
    package = "a" * 60
    other = "b" * 60
    result = {
        item.package: item.tool_name
        for item in assign_tool_names([(package, "search"), (other, "search")])
    }
    for pkg in (package, other):
        candidate = f"{pkg}__search"
        expected = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
        name = result[pkg]
        assert len(name) == TOOL_NAME_MAX
        assert name == f"{candidate[:54]}__{expected}"


def test_final_name_conflict_is_rejected() -> None:
    """限定名与另一个成员的原名相撞时拒绝，不做静默改名。"""
    members = [("a_b", "search"), ("c", "search"), ("x", "a_b__search")]
    with pytest.raises(ToolNameError, match="工具名冲突"):
        assign_tool_names(members)


def test_duplicate_input_member_is_rejected() -> None:
    """同一 ``(包, 成员)`` 重复出现说明授权面数据重复，直接拒绝。"""
    with pytest.raises(ToolNameError, match="工具名冲突"):
        assign_tool_names([("web", "search"), ("web", "search")])


def test_result_is_independent_of_input_order() -> None:
    """同一组输入的不同顺序得到完全相同的结果。"""
    members = [("fs", "read_file"), ("fs", "write_file"), ("web", "search"), ("docs", "search")]
    expected = assign_tool_names(members)
    for variant in (list(reversed(members)), members[1:] + members[:1], list(members)):
        assert assign_tool_names(variant) == expected


def test_reserved_names_include_meta_tools_and_run_script() -> None:
    """保留名集合包含三个元工具与脚本调用工具。"""
    assert META_TOOL_NAMES <= RESERVED_TOOL_NAMES
    assert "run_script" in RESERVED_TOOL_NAMES


def test_reserved_member_key_is_rejected() -> None:
    """非 core 成员键占用保留名即拒绝。"""
    with pytest.raises(ToolNameError, match="保留"):
        assign_tool_names([("core", "search_tools")])


def test_run_script_is_reserved_even_for_core() -> None:
    """``run_script`` 由表面层占用，core 包也不能豁免。"""
    with pytest.raises(ToolNameError, match="保留"):
        assign_tool_names([("core", "run_script")], allow_reserved=META_TOOL_NAMES)


def test_core_meta_tools_may_be_exempted() -> None:
    """core 的三个元工具名允许显式豁免。"""
    result = assign_tool_names([("core", "search_tools")], allow_reserved=META_TOOL_NAMES)
    assert [item.tool_name for item in result] == ["search_tools"]


@pytest.mark.parametrize("allow_reserved", [{"run_script"}, {"unknown_tool"}])
def test_invalid_exemption_is_rejected(allow_reserved: set[str]) -> None:
    """``allow_reserved`` 只接受 core 元工具名。"""
    with pytest.raises(ToolNameError, match="allow_reserved"):
        assign_tool_names([("pkg", "member")], allow_reserved=allow_reserved)


@pytest.mark.parametrize(
    ("package", "member"),
    [
        ("Web", "search"),
        ("web.app", "search"),
        ("", "search"),
        ("web", "Search"),
        ("web", "a/b"),
        ("web", "1abc"),
        ("web", ""),
    ],
)
def test_invalid_inputs_are_rejected(package: str, member: str) -> None:
    """非法包名或成员键没有确定结果之外的猜测空间，直接拒绝。"""
    with pytest.raises(ToolNameError):
        assign_tool_names([(package, member)])


def test_non_string_input_is_rejected() -> None:
    """包名与成员键必须是字符串。"""
    with pytest.raises(ToolNameError, match="字符串"):
        assign_tool_names([(1, "search")])  # type: ignore[list-item]
