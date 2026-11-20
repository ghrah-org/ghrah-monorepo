# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""装配规格的成员条目由 Core 解析成内部成员实体。

Subject 只按既有 wire 形状提交 ``AgentAssemblyMember``（成员全名 + 类型 + 版本 +
两条轴），不持有也不导入 Core 的实体；解析、校验都在 Core 边界完成。
"""

from __future__ import annotations

import pytest

from ghrah.ability.member import Member
from ghrah.core.agents.assembly import AssemblyError, _resolve_member, parse_spec_member


def test_spec_member_becomes_the_core_entity() -> None:
    entity = parse_spec_member(
        {
            "member": "acme.tools.render",
            "type": "operation",
            "version": "1.2.3",
            "ability_type": "external",
            "axes": {"visible": False, "available": False},
        }
    )
    assert type(entity) is Member
    assert (entity.namespace, entity.name) == ("acme", "tools")
    assert entity.key == "render"
    assert entity.package_full_name == "acme.tools"
    assert (entity.axes.visible, entity.axes.bind) == (False, False)
    # 装配规格只带身份，不带定义数据。
    assert entity.is_definition is False


def test_kernel_member_has_no_version_layer() -> None:
    entity = parse_spec_member(
        {"member": "ghrah.fs.read_file", "type": "operation", "ability_type": "kernel"}
    )
    assert entity.version is None
    assert entity.ability_type == "kernel"


def test_degenerate_package_name_is_recovered() -> None:
    entity = parse_spec_member({"member": "reporting.guide", "type": "skill", "version": "0.1.0"})
    assert entity.package_full_name == "reporting"
    assert (entity.namespace, entity.name) == ("reporting", "reporting")


def test_legacy_entry_without_type_or_version_still_parses() -> None:
    """旧快照只写成员全名：按 operation + external 解释，版本留空。"""
    entity = parse_spec_member({"member": "acme.demo.ping"})
    assert entity.type == "operation"
    assert entity.version is None


def test_axes_allow_discoverable_but_unbound() -> None:
    """``available=false`` 表示不绑定；绑定必然可见，所以反向组合才非法。"""
    entity = parse_spec_member(
        {
            "member": "acme.tools.render",
            "type": "operation",
            "version": "1.2.3",
            "axes": {"visible": True, "available": False},
        }
    )
    assert (entity.axes.visible, entity.axes.bind) == (True, False)

    with pytest.raises(AssemblyError, match="bind"):
        parse_spec_member(
            {
                "member": "acme.tools.render",
                "type": "operation",
                "version": "1.2.3",
                "axes": {"visible": False, "available": True},
            }
        )


def test_single_level_package_retries_with_legacy_two_segment_name() -> None:
    """单层身份包在能力来源里按两段名登记：三段名失败后要回退一次。"""
    calls: list[str] = []

    class Source:
        def load_ability(self, full_name: str, *, version: str | None = None) -> object:
            calls.append(full_name)
            raise RuntimeError(f"lookup failed for {full_name}")

    with pytest.raises(AssemblyError) as error:
        _resolve_member(Source(), "research.research.research", None, version="0.1.0")

    assert calls == ["research.research.research", "research.research"]
    assert "lookup failed for research.research" in str(error.value)
    assert "lookup failed for research.research.research" not in str(error.value)


def test_non_degenerate_member_is_not_retried() -> None:
    calls: list[str] = []

    class Source:
        def load_ability(self, full_name: str, *, version: str | None = None) -> object:
            calls.append(full_name)
            raise RuntimeError("boom")

    with pytest.raises(AssemblyError):
        _resolve_member(Source(), "ghrah.fs.read_file", None)  # type: ignore[arg-type]
    assert calls == ["ghrah.fs.read_file"]


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(AssemblyError, match="type"):
        parse_spec_member({"member": "acme.tools.render", "type": "widget", "version": "1.2.3"})


def test_version_layer_infers_external_for_a_versioned_member() -> None:
    """wire 默认 ``ability_type=kernel``；带版本的成员必然来自安装根，按 external 解释。"""
    entity = parse_spec_member(
        {
            "member": "ghrah.fs.read_file",
            "type": "operation",
            "version": "0.1.0",
            "ability_type": "kernel",
        }
    )
    assert entity.ability_type == "external"
    assert entity.version == "0.1.0"
