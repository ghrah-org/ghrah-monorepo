# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""授权面投影：已获授、已选择、可暴露、未显式降级的成员才进入表面。"""

from __future__ import annotations

from ghrah.protocol.ability_tree import (
    AbilityNode,
    AbilityTreeView,
    BoundToolNode,
    OperationNode,
)

from ghrah.ability.surface import MemberRef, SurfaceEntry, project_grants


def _operation(key: str, availability: str = "ready") -> OperationNode:
    """构造一个最小 Operation 节点。"""
    return OperationNode(
        key=key,
        description=f"{key} description",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        availability=availability,
    )


def _view(
    operations: list[OperationNode],
    bound: list[BoundToolNode],
    *,
    alias: str = "fs",
) -> AbilityTreeView:
    """构造一个单 Ability 的关系库投影。"""
    return AbilityTreeView(
        project_id="project",
        agent_id="alice",
        run_id="alice-run",
        revision=3,
        abilities=[
            AbilityNode(
                alias=alias,
                instance_id="ghrah-fs",
                instance_revision=1,
                definition_digest="sha256:definition",
                version_digest="sha256:version",
                revision=2,
                allowed_roots=["/workspace"],
                operations=operations,
            )
        ],
        bound_tools=bound,
    )


def _bound(name: str, member: str, *, alias: str = "fs") -> BoundToolNode:
    """构造一个工具选择记录。"""
    return BoundToolNode(name=name, alias=alias, member=member, grant_revision=2, revision=3)


def test_projection_keeps_only_selected_visible_members() -> None:
    """未选择的成员不进入授权面；选中的成员带固定工具名与成员引用。"""
    view = _view(
        [_operation("read_file"), _operation("write_file")],
        [_bound("read_file", "read_file")],
    )
    assert project_grants(view) == [
        SurfaceEntry(
            tool_name="read_file",
            member=MemberRef(instance_id="ghrah-fs", member="read_file"),
            package="fs",
            static=False,
            available=True,
        )
    ]


def test_projection_preserves_explicit_unavailable_fact() -> None:
    """关系库声明“不可用”的成员仍可描述，但可用性为 false。"""
    view = _view(
        [_operation("write_file", "unavailable")],
        [_bound("write_file", "write_file")],
    )
    entries = project_grants(view)
    assert len(entries) == 1 and entries[0].available is False


def test_projection_skips_stale_bindings_without_target() -> None:
    """工具选择指向已撤销成员或已撤销 alias 时，投影跳过而不是猜测。"""
    view = _view([_operation("read_file")], [_bound("ghost", "revoked_member")])
    assert project_grants(view) == []

    missing_alias = _view(
        [_operation("read_file")], [_bound("read_file", "read_file", alias="gone")]
    )
    assert project_grants(missing_alias) == []


def test_projection_is_sorted_by_tool_name_and_empty_when_nothing_selected() -> None:
    """投影结果按工具名稳定排序；空选择得到空表面。"""
    view = _view(
        [_operation("read_file"), _operation("list_directory")],
        [
            _bound("read_file", "read_file"),
            _bound("list_directory", "list_directory"),
        ],
    )
    assert [entry.tool_name for entry in project_grants(view)] == [
        "list_directory",
        "read_file",
    ]
    assert project_grants(_view([_operation("read_file")], [])) == []
