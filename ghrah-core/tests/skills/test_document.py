# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SKILL.md 正文读取测试（``read_skill_document`` + ``provider.read_skill``）。

钉住：逐字返回完整原文（不剥 frontmatter、不裁剪）、路径取自清单索引、
查表是精确匹配（不切分 full_name）、扫描期有问题的技能读不到（不绕过校验）、
读取只读（不建目录、不写 lock）、读不出来时抛 ``SkillParseError``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.skills import (
    SKILL_FILE_NAME,
    SkillAbilityProvider,
    SkillNotFoundError,
    SkillParseError,
    read_skill_document,
)

SKILL_TEXT = (
    "---\n"
    "name: db-query\n"
    "description: 对关系型数据库执行只读查询。\n"
    "---\n"
    "\n"
    "# 步骤\n"
    "\n"
    "1. 先看 schema。\n"
)


def _write_skill(root: Path, dir_name: str, text: str = SKILL_TEXT) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / SKILL_FILE_NAME).write_text(text, encoding="utf-8")
    return skill_dir


def _provider(root: Path) -> SkillAbilityProvider:
    return SkillAbilityProvider.from_root(root)


class TestReadSkillDocument:
    def test_returns_raw_content_and_paths(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")

        document = read_skill_document(_provider(tmp_path).get_skill("db-query"))

        assert document.full_name == "db-query"
        assert document.skill_dir == skill_dir
        assert document.path == skill_dir / SKILL_FILE_NAME
        # 逐字：frontmatter 块与正文都在，未被剥离、未被裁剪
        assert document.content == SKILL_TEXT
        assert document.content.startswith("---\n")
        assert "# 步骤" in document.content

    def test_missing_document_raises_parse_error(self, tmp_path: Path) -> None:
        """扫描后文件被删（TOCTOU）→ SkillParseError，不是静默空内容。"""
        skill_dir = _write_skill(tmp_path, "db-query")
        skill = _provider(tmp_path).get_skill("db-query")
        (skill_dir / SKILL_FILE_NAME).unlink()

        with pytest.raises(SkillParseError, match="读取技能文档失败"):
            read_skill_document(skill)

    def test_non_utf8_document_raises_parse_error(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")
        skill = _provider(tmp_path).get_skill("db-query")
        (skill_dir / SKILL_FILE_NAME).write_bytes(b"\xff\xfe\x00\x01binary")

        with pytest.raises(SkillParseError):
            read_skill_document(skill)


class TestProviderReadSkill:
    def test_read_by_full_name(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")

        document = _provider(tmp_path).read_skill("db-query")

        assert document.full_name == "db-query"
        assert document.content == SKILL_TEXT

    def test_unknown_full_name_raises_not_found(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")

        with pytest.raises(SkillNotFoundError):
            _provider(tmp_path).read_skill("no-such-skill")

    def test_full_name_is_exact_not_split(self, tmp_path: Path) -> None:
        """包名-技能名形态：只认完整目录名，不切分字符串（`pr` 查不到 `humanlayer-pr`）。"""
        _write_skill(tmp_path, "humanlayer-pr")
        provider = _provider(tmp_path)

        assert provider.read_skill("humanlayer-pr").full_name == "humanlayer-pr"
        with pytest.raises(SkillNotFoundError):
            provider.read_skill("pr")

    def test_skill_with_scan_issue_is_not_readable(self, tmp_path: Path) -> None:
        """扫描期有问题的技能不在清单索引里 → 读不到（不绕过校验直接读文件）。"""
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / SKILL_FILE_NAME).write_text("没有 frontmatter\n", encoding="utf-8")
        provider = _provider(tmp_path)

        assert provider.scan().issues
        with pytest.raises(SkillNotFoundError):
            provider.read_skill("broken")

    def test_read_is_read_only(self, tmp_path: Path) -> None:
        """读取不建 lock、不建目录（初始化是显式动作）。"""
        skill_dir = _write_skill(tmp_path, "db-query")

        _provider(tmp_path).read_skill("db-query")

        assert sorted(p.name for p in skill_dir.iterdir()) == [SKILL_FILE_NAME]


class TestNonUtf8DoesNotBreakScan:
    def test_scan_records_issue_instead_of_raising(self, tmp_path: Path) -> None:
        """一个二进制 SKILL.md 只应记成 issue，不允许把整轮扫描炸掉。"""
        _write_skill(tmp_path, "db-query")
        broken = tmp_path / "binary-skill"
        broken.mkdir()
        (broken / SKILL_FILE_NAME).write_bytes(b"\xff\xfe\x00\x01")

        scan = _provider(tmp_path).scan()

        assert [skill.full_name for skill in scan.skills] == ["db-query"]
        assert len(scan.issues) == 1
        assert scan.issues[0].path == broken / SKILL_FILE_NAME
