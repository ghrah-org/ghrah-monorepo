# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""统一成员实体（:class:`ghrah.ability.member.Member`）的契约测试。

覆盖三种用法共用同一实体：定义态（ability.yaml / 版本画像）、引用态
（模板勾选 / 授权申请）、以及 wire 往返与路径解析。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.manifest_fields import ManifestValidationError
from ghrah.ability.member import Member, MemberAxes, MemberError


def _operation(**overrides: Any) -> Member:
    data: dict[str, Any] = {
        "title": "Edit File",
        "description": "改文件",
        "path": "ghrah/fs/operations/edit_file.py",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "execution": {
            "mode": "request",
            "timeout_ms": 1000,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
    }
    replacement = overrides.pop("data_replace", None)
    data = dict(replacement) if replacement is not None else data
    data.update(overrides.pop("data", {}) or {})
    fields = {
        "namespace": "ghrah",
        "name": "fs",
        "member": "ghrah.fs.edit_file",
        "type": "operation",
        "version": None,
        "ability_type": "kernel",
        "data": data,
    }
    fields.update(overrides)
    return Member(**fields)


def test_definition_record_carries_type_data() -> None:
    member = _operation()
    assert member.is_definition is True
    assert member.key == "edit_file"
    assert member.axes == MemberAxes(visible=True, bind=True)


def test_reference_record_allows_empty_data() -> None:
    """引用态只有身份与轴：模板/授权不需要、也不该复制定义数据。"""
    member = Member(
        namespace="ghrah",
        name="fs",
        member="ghrah.fs.edit_file",
        type="operation",
        ability_type="kernel",
    )
    assert member.is_definition is False
    assert member.axes.to_wire() == {"visible": True, "bind": True}


def test_definition_record_rejects_missing_and_unknown_data_keys() -> None:
    with pytest.raises(MemberError, match="缺少 data 必填键"):
        _operation(data_replace={"path": "ghrah/fs/operations/edit_file.py"})
    with pytest.raises(MemberError, match="未知键"):
        _operation(data={"unknown": 1})


def test_kernel_has_no_version_and_external_requires_semver() -> None:
    with pytest.raises(MemberError, match="version 必须为 null"):
        _operation(version="0.1.0")
    with pytest.raises(MemberError, match="SemVer core 三段"):
        _operation(version="1.0", ability_type="external")
    external = _operation(version="1.0.0", ability_type="external")
    assert external.version == "1.0.0"


def test_axes_are_independent() -> None:
    """可发现未绑定与「绑定但不进发现面」都是合法状态。"""
    assert MemberAxes(visible=False, bind=False).visible is False
    assert MemberAxes(visible=True, bind=False) == MemberAxes(visible=True, bind=False)
    hidden = MemberAxes(visible=False, bind=True)
    assert (hidden.visible, hidden.bind) == (False, True)
    assert hidden.with_bind(False) == MemberAxes(visible=False, bind=False)


def test_member_full_name_must_match_identity() -> None:
    with pytest.raises(MemberError, match="member 必须是"):
        _operation(member="acme.fs.edit_file")
    with pytest.raises(MemberError, match="member 必须是"):
        _operation(member="ghrah.fs.edit_file.extra")


def test_degenerate_package_uses_two_segment_member_name() -> None:
    """``identity.name`` 缺省时包名退化为 namespace，成员全名自然只有两段。"""
    member = Member(
        namespace="research",
        name="research",
        member="research.guide",
        type="skill",
        ability_type="external",
        version="0.1.0",
    )
    assert member.package_full_name == "research"
    assert member.key == "guide"
    with pytest.raises(MemberError, match="缺少 data 必填键"):
        Member(
            namespace="research",
            name="research",
            member="research.guide",
            type="skill",
            ability_type="external",
            version="0.1.0",
            data={"title": "指南"},
        )


def test_package_relative_path_strips_the_root_prefix() -> None:
    assert _operation().package_relative_path() == "operations/edit_file.py"
    external = _operation(
        namespace="acme",
        name="demo",
        member="acme.demo.edit_file",
        ability_type="external",
        version="1.4.0",
        data={"path": "acme/demo/1.4.0/operations/edit_file.py"},
    )
    assert external.package_relative_path() == "operations/edit_file.py"
    with pytest.raises(MemberError, match="ability 根相对路径"):
        _operation(data={"path": "operations/edit_file.py"}).package_relative_path()


def test_resolve_path_uses_the_root_of_the_ability_type(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    install_root = tmp_path / "install"
    assert (
        _operation().resolve_path(builtin_root=builtin_root, install_root=install_root)
        == builtin_root / "fs" / "operations" / "edit_file.py"
    )
    external = _operation(
        namespace="acme",
        name="demo",
        member="acme.demo.edit_file",
        ability_type="external",
        version="1.4.0",
        data={"path": "acme/demo/1.4.0/operations/edit_file.py"},
    )
    assert (
        external.resolve_path(builtin_root=builtin_root, install_root=install_root)
        == install_root / "acme" / "demo" / "1.4.0" / "operations" / "edit_file.py"
    )


def test_wire_round_trip_is_lossless() -> None:
    member = _operation()
    assert Member.from_wire(member.to_wire()) == member
    reference = Member(
        namespace="ghrah",
        name="fs",
        member="ghrah.fs.edit_file",
        type="operation",
        ability_type="kernel",
        axes=MemberAxes(visible=False, bind=False),
    )
    assert Member.from_wire(reference.to_wire()) == reference


def test_with_data_path_repoints_without_touching_identity() -> None:
    member = _operation()
    moved = member.with_data_path("operations/other.py")
    assert moved.data["path"] == "ghrah/fs/operations/other.py"
    assert moved.member == member.member
    assert member.data["path"] == "ghrah/fs/operations/edit_file.py"


def test_definition_to_members_matches_the_yaml_list() -> None:
    raw = yaml.safe_dump(
        {
            "format": "ghrah.ability-definition",
            "schema_version": "2",
            "identity": {"namespace": "acme", "name": "demo", "version": "1.0.0"},
            "title": "Demo",
            "description": "演示",
            "members": [
                {
                    "namespace": "acme",
                    "name": "demo",
                    "member": "acme.demo.render",
                    "type": "operation",
                    "version": "1.0.0",
                    "ability_type": "external",
                    "axes": {"visible": True, "bind": True},
                    "data": {
                        "title": "渲染",
                        "description": "渲染报表",
                        "path": "acme/demo/1.0.0/operations/render.py",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "output_schema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                        "execution": {
                            "mode": "request",
                            "timeout_ms": 1000,
                            "cancellation": "unsupported",
                            "on_revoke": "drain",
                            "retry": "never",
                        },
                    },
                }
            ],
        },
        sort_keys=False,
        allow_unicode=True,
    )
    definition = parse_definition(raw, source="test")
    (member,) = definition.to_members()
    assert member.member == "acme.demo.render"
    assert member.package_relative_path() == "operations/render.py"
    assert member.data["title"] == "渲染"
    # 反向：实体再投影回定义时仍能通过同一套契约。
    exported = {
        "format": "ghrah.ability-definition",
        "schema_version": "2",
        "identity": {"namespace": "acme", "name": "demo", "version": "1.0.0"},
        "title": "Demo",
        "description": "演示",
        "members": [item.to_wire() for item in definition.to_members()],
    }
    assert parse_definition(yaml.safe_dump(exported), source="test").operations.keys() == {"render"}


def test_ability_yaml_rejects_reference_records() -> None:
    """ability.yaml 里每条成员都必须是完整定义，引用态只出现在模板/授权侧。"""
    raw = yaml.safe_dump(
        {
            "format": "ghrah.ability-definition",
            "schema_version": "2",
            "identity": {"namespace": "acme", "name": "demo", "version": "1.0.0"},
            "title": "Demo",
            "description": "演示",
            "members": [
                {
                    "namespace": "acme",
                    "name": "demo",
                    "member": "acme.demo.render",
                    "type": "operation",
                    "version": "1.0.0",
                    "ability_type": "external",
                    "data": {},
                }
            ],
        },
        sort_keys=False,
        allow_unicode=True,
    )
    with pytest.raises(ManifestValidationError, match="缺少 data"):
        parse_definition(raw, source="test")


def test_mcp_member_read_only_is_optional_but_typed() -> None:
    """MCP 成员的 read_only 缺省 false；只接受 bool，防止页面写入字符串。"""
    data = {
        "title": "搜索",
        "description": "搜索网页",
        "server": "search",
        "tool": "web_search",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "execution": {
            "mode": "request",
            "timeout_ms": 1000,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
    }
    member = Member(
        namespace="mcp",
        name="search",
        member="mcp.search.web_search",
        type="mcp",
        version="0.0.1",
        ability_type="external",
        data={**data, "read_only": True},
    )
    assert member.data["read_only"] is True
    with pytest.raises(MemberError, match="read_only"):
        Member(
            namespace="mcp",
            name="search",
            member="mcp.search.web_search",
            type="mcp",
            version="0.0.1",
            ability_type="external",
            data={**data, "read_only": "yes"},
        )
