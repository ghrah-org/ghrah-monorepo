# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 扫描与 frontmatter 解析的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.skills import (
    DEFAULT_SKILL_ROOT_RANK,
    DEFAULT_SKILL_ROOT_SOURCE,
    SKILL_FILE_NAME,
    SkillParseError,
    SkillRoot,
    default_pool,
    default_skill_root,
    parse_skill_file,
    scan_skills,
    split_frontmatter,
)

VALID_SKILL_MD = """---
name: db-query
description: >-
  对关系型数据库执行只读查询并返回结果集。
permission:
  approve: auto
  files:
    read: ["/data/**"]
  net: deny
requires:
  - db:read
implementation:
  factory: script
  params:
    runtime: python3
    entrypoint: scripts/query.py
    timeout: 30
---

# db-query：数据库只读查询

正文内容。
"""


def _make_skill(root: Path, dir_name: str, content: str = VALID_SKILL_MD) -> Path:
    """在 root 下造一个 bundle 形态 skill，返回其包目录。"""
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    (skill_dir / SKILL_FILE_NAME).write_text(content, encoding="utf-8")
    return skill_dir


def _pool(root: Path, rank: int = 100, source: str = "skill-ability") -> tuple[SkillRoot, ...]:
    return (SkillRoot(path=root, rank=rank, source=source),)


class TestDefaultPool:
    def test_single_root_with_top_priority(self) -> None:
        pool = default_pool()

        assert len(pool) == 1
        assert pool[0].path == Path.home() / ".ghrah" / "skill-ability"
        assert pool[0].rank == DEFAULT_SKILL_ROOT_RANK
        assert pool[0].source == DEFAULT_SKILL_ROOT_SOURCE

    def test_default_skill_root(self) -> None:
        assert default_skill_root() == Path.home() / ".ghrah" / "skill-ability"


class TestScanSkills:
    def test_extracts_metadata_from_valid_skill(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query")

        result = scan_skills(_pool(tmp_path))

        assert result.issues == ()
        assert len(result.skills) == 1
        skill = result.skills[0]
        assert skill.full_name == "db-query"
        assert skill.name == "db-query"
        assert skill.description == "对关系型数据库执行只读查询并返回结果集。"
        assert skill.source == "skill-ability"
        assert skill.rank == 100
        assert skill.skill_dir == skill_dir

    def test_keeps_full_frontmatter_without_interpreting_it(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "db-query")

        skill = scan_skills(_pool(tmp_path)).skills[0]

        assert skill.frontmatter["permission"]["approve"] == "auto"
        assert skill.frontmatter["requires"] == ["db:read"]
        assert skill.frontmatter["implementation"]["factory"] == "script"

    def test_package_dir_name_is_full_name(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "org-db-query")

        skill = scan_skills(_pool(tmp_path)).skills[0]

        assert skill.full_name == "org-db-query"
        assert skill.name == "db-query"
        assert skill.skill_dir == skill_dir

    def test_same_skill_name_in_two_packages_coexists(self, tmp_path: Path) -> None:
        """跨包重名：full_name 不同即两个技能，互不覆盖。"""
        _make_skill(tmp_path, "alpha-research", VALID_SKILL_MD.replace("db-query", "research"))
        _make_skill(tmp_path, "beta-research", VALID_SKILL_MD.replace("db-query", "research"))

        result = scan_skills(_pool(tmp_path))

        assert result.issues == ()
        assert [s.full_name for s in result.skills] == ["alpha-research", "beta-research"]

    def test_directory_name_may_differ_from_frontmatter_name(self, tmp_path: Path) -> None:
        """身份只认目录名；frontmatter 的 name 是技能文档自称的名字，不参与判定。"""
        _make_skill(tmp_path, "whatever-dir")

        result = scan_skills(_pool(tmp_path))

        assert result.issues == ()
        assert result.skills[0].full_name == "whatever-dir"
        assert result.skills[0].name == "db-query"

    def test_invalid_directory_name_reports_issue(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "Bad_Dir", "---\nname: db-query\ndescription: 描述\n---\n")

        result = scan_skills(_pool(tmp_path))

        assert result.skills == ()
        assert len(result.issues) == 1
        assert "包目录名格式非法" in result.issues[0].reason

    def test_ignores_non_skill_entries(self, tmp_path: Path) -> None:
        (tmp_path / "assets").mkdir()
        (tmp_path / "notes.md").write_text("没有 frontmatter", encoding="utf-8")

        result = scan_skills(_pool(tmp_path))

        assert result.skills == ()
        assert result.issues == ()

    def test_does_not_recurse(self, tmp_path: Path) -> None:
        nested = tmp_path / "outer" / "inner"
        nested.mkdir(parents=True)
        (nested / SKILL_FILE_NAME).write_text(VALID_SKILL_MD, encoding="utf-8")

        assert scan_skills(_pool(tmp_path)).skills == ()

    def test_missing_root_reports_issue(self, tmp_path: Path) -> None:
        result = scan_skills(_pool(tmp_path / "nope"))

        assert result.skills == ()
        assert len(result.issues) == 1
        assert "目录不存在" in result.issues[0].reason

    def test_result_sorted_by_full_name(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "bbb", VALID_SKILL_MD.replace("db-query", "bbb"))
        _make_skill(tmp_path, "aaa", VALID_SKILL_MD.replace("db-query", "aaa"))

        assert [s.full_name for s in scan_skills(_pool(tmp_path)).skills] == ["aaa", "bbb"]

    def test_lower_rank_wins_on_duplicate_full_name(self, tmp_path: Path) -> None:
        high = tmp_path / "high"
        low = tmp_path / "low"
        _make_skill(high, "db-query", VALID_SKILL_MD)
        _make_skill(low, "db-query", VALID_SKILL_MD.replace("只读查询", "低优先级版本"))

        result = scan_skills(
            (
                SkillRoot(path=low, rank=200, source="low"),
                SkillRoot(path=high, rank=100, source="high"),
            )
        )

        assert len(result.skills) == 1
        assert result.skills[0].source == "high"
        assert len(result.issues) == 1
        assert "重复的 full_name" in result.issues[0].reason


class TestParseSkillFile:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("# 没有 frontmatter\n", "起始分隔符"),
            ("---\nname: a\n", "结束分隔符"),
            ("---\nname: [\n---\n", "YAML 解析失败"),
            ("---\n- a\n---\n", "顶层不是 YAML 映射"),
            ("---\ndescription: 有描述\n---\n", "缺少必填字段 name"),
            ("---\nname: db-query\n---\n", "缺少必填字段 description"),
            ("---\nname: db-query\ndescription: '   '\n---\n", "缺少必填字段 description"),
            ("---\nname: Bad_Name\ndescription: 描述\n---\n", "name 格式非法"),
            ("---\nname: BadName\ndescription: 描述\n---\n", "name 格式非法"),
        ],
    )
    def test_invalid_skill_reports_issue(self, tmp_path: Path, content: str, expected: str) -> None:
        _make_skill(tmp_path, "broken", content)

        result = scan_skills(_pool(tmp_path))

        assert result.skills == ()
        assert len(result.issues) == 1
        assert expected in result.issues[0].reason

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SkillParseError):
            parse_skill_file(tmp_path / SKILL_FILE_NAME, skill_dir=tmp_path, source="s", rank=1)


class TestSplitFrontmatter:
    def test_returns_mapping_and_body(self) -> None:
        data, body = split_frontmatter("---\nname: a-b\n---\n\n正文\n\n")

        assert data == {"name": "a-b"}
        assert body == "正文"
