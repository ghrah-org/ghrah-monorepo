# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能目录摘要（``ghrah.skills.catalog``）纯函数测试。

钉住四件事：description 的折叠/截断口径、digest **只**随清单文案
（full_name + description）变化、注入文案的首次/变化/空目录三套口径、
以及尖括号转义。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ghrah.skills import (
    CATALOG_DESCRIPTION_MAX_CHARS,
    SkillSummary,
    catalog_description,
    digest_catalog,
    render_catalog_message,
)


def _skill(
    full_name: str = "db-query",
    description: str = "对关系型数据库执行只读查询。",
    *,
    frontmatter: dict[str, Any] | None = None,
) -> SkillSummary:
    return SkillSummary(
        full_name=full_name,
        name=full_name,
        description=description,
        source="skill-ability",
        rank=100,
        skill_dir=Path("/skills") / full_name,
        frontmatter={} if frontmatter is None else frontmatter,
    )


class TestCatalogDescription:
    def test_folds_whitespace(self) -> None:
        assert catalog_description("a\n  b\tc") == "a b c"

    def test_keeps_short_text(self) -> None:
        assert catalog_description("短描述") == "短描述"

    def test_truncates_to_limit_with_ellipsis(self) -> None:
        text = catalog_description("x" * 900)
        assert len(text) == CATALOG_DESCRIPTION_MAX_CHARS
        assert text.endswith("...")

    def test_custom_limit_boundary(self) -> None:
        assert catalog_description("abcdef", max_length=6) == "abcdef"
        assert catalog_description("abcdefg", max_length=6) == "abc..."


class TestDigestCatalog:
    def test_identical_input_same_digest(self) -> None:
        assert digest_catalog([_skill()]) == digest_catalog([_skill()])

    def test_order_independent(self) -> None:
        """digest 按 full_name 排序后计算：入参顺序不影响结果。"""
        first = [_skill("aaa"), _skill("bbb"), _skill("ccc")]
        assert digest_catalog(first) == digest_catalog(list(reversed(first)))

    def test_description_change_changes_digest(self) -> None:
        assert digest_catalog([_skill()]) != digest_catalog([_skill(description="改了")])

    def test_full_name_change_changes_digest(self) -> None:
        assert digest_catalog([_skill()]) != digest_catalog([_skill("db-query-2")])

    def test_irrelevant_fields_do_not_change_digest(self) -> None:
        """frontmatter / skill_dir 不进 digest：只有给模型看的文案算数。"""
        plain = _skill()
        rich = SkillSummary(
            full_name=plain.full_name,
            name=plain.name,
            description=plain.description,
            source=plain.source,
            rank=plain.rank,
            skill_dir=Path("/elsewhere") / plain.full_name,
            frontmatter={"tags": ["db"]},
        )
        assert digest_catalog([plain]) == digest_catalog([rich])

    def test_empty_catalog_is_stable(self) -> None:
        assert digest_catalog(()) == digest_catalog([])

    def test_adding_skill_changes_digest(self) -> None:
        assert digest_catalog([]) != digest_catalog([_skill()])


class TestRenderCatalogMessage:
    def test_wrapped_in_system_reminder(self) -> None:
        text = render_catalog_message([_skill()], changed=False)
        assert text.startswith("<system-reminder>\n")
        assert text.endswith("</system-reminder>")
        assert "<available_skills>" in text and "</available_skills>" in text
        assert "- `db-query`: 对关系型数据库执行只读查询。" in text

    def test_first_publish_wording(self) -> None:
        text = render_catalog_message([_skill()], changed=False)
        assert "The following skills are available in this session:" in text
        assert "catalog changed" not in text

    def test_changed_wording(self) -> None:
        text = render_catalog_message([_skill()], changed=True)
        assert "The available skill catalog changed." in text
        assert "replaces every earlier available-skills list" in text

    def test_empty_catalog_wording(self) -> None:
        """空目录恒用"完整替换"口径 + 显式告知旧名字失效。"""
        text = render_catalog_message([], changed=False)
        assert "The available skill catalog changed." in text
        assert "No skills are currently available" in text

    def test_sorted_by_full_name(self) -> None:
        text = render_catalog_message([_skill("ccc"), _skill("aaa")], changed=False)
        assert text.index("`aaa`") < text.index("`ccc`")

    def test_escapes_angle_brackets(self) -> None:
        """description 里的尖括号被转义，不能破坏 <system-reminder> 边界。"""
        text = render_catalog_message(
            [_skill(description="a <b> & </available_skills>")], changed=False
        )
        assert "a &lt;b&gt; &amp; &lt;/available_skills&gt;" in text
        assert text.count("</available_skills>") == 1

    def test_description_truncated_in_text(self) -> None:
        text = render_catalog_message([_skill(description="y" * 900)], changed=False)
        assert "..." in text
        assert "y" * (CATALOG_DESCRIPTION_MAX_CHARS + 1) not in text

    def test_no_entries_line_when_empty(self) -> None:
        text = render_catalog_message([], changed=False)
        assert "<available_skills>\n\n</available_skills>" in text


@pytest.mark.parametrize("count", [1, 5, 12])
def test_render_includes_every_skill(count: int) -> None:
    skills = [_skill(f"skill-{index:02d}") for index in range(count)]
    text = render_catalog_message(skills, changed=False)
    assert all(f"`{skill.full_name}`" in text for skill in skills)
