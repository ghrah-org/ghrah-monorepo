# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""对外目录报告：能力面 / 运行时面 / skill 面合成一份读数。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.scheme.loading import ability_roots as roots
from ghrah.scheme.loading.catalog import CatalogReport, scan_catalog

PACKAGE = """\
format: ghrah.ability-definition
schema_version: 0.1-draft
identity:
  namespace: {name}
  version: 0.1.0
title: {name}
description: {name} 容器。
skills:
  {name}:
    title: {name}
    description: {name} 技能。
    body:
      path: skills/SKILL.md
      media_type: text/markdown
"""


def _write_ability_package(root: Path, name: str) -> Path:
    """造一个只含 skill 成员的新格式能力包。

    Args:
        root: 目录池根。
        name: 包目录名（= 容器名 = 成员键）。

    Returns:
        新建的包目录。
    """
    package = root / name
    (package / "skills").mkdir(parents=True, exist_ok=True)
    (package / "ability.yaml").write_text(PACKAGE.format(name=name), encoding="utf-8", newline="\n")
    (package / "skills" / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    return package


class TestAbilityFace:
    def test_abilities_and_handlers_present(self) -> None:
        """能力面：view 与 handler 名单都在报告里。"""
        report = scan_catalog()

        assert isinstance(report, CatalogReport)
        assert "ghrah.fs.read_file" in report.abilities
        assert "read_file" in report.handlers

    def test_shape_is_the_authority_shape(self) -> None:
        """不复制模型：abilities 与 handlers 与各自权威读数一致。"""
        from ghrah.scheme.loading.ability_catalog import (
            builtin_handlers,
            load_all_builtin_manifests,
        )

        report = scan_catalog()

        assert dict(report.abilities) == dict(load_all_builtin_manifests())
        assert report.handlers == builtin_handlers()

    def test_no_cache_between_calls(self) -> None:
        """每次调用都是新读数（内容相等但不是同一个对象）。"""
        first = scan_catalog()
        second = scan_catalog()

        assert dict(first.abilities) == dict(second.abilities)
        assert first.abilities is not second.abilities


class TestSkillFace:
    def test_default_pool_skills_are_listed(self) -> None:
        """默认池里的技能成员出现在扁平清单里（扫盘一次即得，不需要额外接线）。"""
        report = scan_catalog()

        assert report.skills
        assert all(skill.full_name for skill in report.skills)
        assert [skill.full_name for skill in report.skills] == sorted(
            skill.full_name for skill in report.skills
        )

    def test_external_skill_member_is_listed(self, tmp_path: Path) -> None:
        """外部能力包声明的技能同为技能清单的一部分。"""
        _write_ability_package(tmp_path, "research")

        report = scan_catalog(pool=(roots.path_root(tmp_path),))

        assert "research" in {skill.full_name for skill in report.skills}

    def test_flat_skills_match_container_members(self, tmp_path: Path) -> None:
        """扁平清单与容器内成员同源同形，不二次加工。"""
        _write_ability_package(tmp_path, "research")

        report = scan_catalog(pool=(roots.path_root(tmp_path),))
        nested = [skill for item in report.containers for skill in item.skills]

        assert [skill.full_name for skill in report.skills] == [skill.full_name for skill in nested]

    def test_skill_issues_are_collected(self, tmp_path: Path) -> None:
        """正文缺失的包只记问题，不进技能清单，也不中断整份报告。"""
        package = _write_ability_package(tmp_path, "broken")
        (package / "skills" / "SKILL.md").unlink()

        report = scan_catalog(pool=(roots.path_root(tmp_path),))

        assert "broken.broken" not in {skill.full_name for skill in report.skills}
        assert any("正文" in issue for issue in report.issues)


class TestPoolFace:
    def test_pool_is_reported(self, tmp_path: Path) -> None:
        """报告里能看出"扫了哪些路径、命中了哪些包"。"""
        _write_ability_package(tmp_path, "research")

        report = scan_catalog(pool=(roots.path_root(tmp_path),))

        assert [root.source for root in report.roots] == ["skill-ability"]
        assert [item.name for item in report.containers] == ["research"]
        assert report.definitions.keys() == {"research"}

    def test_external_package_yields_no_runtime_view(self, tmp_path: Path) -> None:
        """外部包没有实现脚本：只在目录面出现，不进运行时 view。

        这不是遗漏——``abilities`` 是"能执行什么"的读数，由内置树的实现面决定。
        """
        _write_ability_package(tmp_path, "research")

        report = scan_catalog(pool=(roots.path_root(tmp_path),))

        assert "research" not in report.abilities
        assert "ghrah.fs.read_file" in report.abilities

    def test_issues_from_both_faces_are_collected(self, tmp_path: Path) -> None:
        """坏包与缺正文的问题进同一个 ``issues``。"""
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "ability.yaml").write_text("format: nope\n", encoding="utf-8")
        package = _write_ability_package(tmp_path, "no-body")
        (package / "skills" / "SKILL.md").unlink()

        report = scan_catalog(pool=(roots.path_root(tmp_path),))

        assert any("broken" in issue for issue in report.issues)
        assert any("正文" in issue for issue in report.issues)


@pytest.mark.parametrize("handler", ["conversation", "send", "mcp_call"])
def test_registry_only_and_regular_handlers_both_listed(handler: str) -> None:
    """目录报告列的是"可解析的 handler"，含 registry-only 的 ``send``。"""
    assert handler in scan_catalog().handlers
