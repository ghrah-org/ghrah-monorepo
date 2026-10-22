# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""指纹查询的单元测试。

重点钉住两件事：**查询是只读的**（不建目录、不补 lock），以及「未登记」与
「内容变过」是两种不同的状态（``recorded is None`` vs ``drifted is True``）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.skills import (
    LOCK_FILE_NAME,
    SKILL_FILE_NAME,
    SkillLockError,
    SkillParseError,
    SkillRoot,
    fingerprint_skill,
    initialize_skills,
    query_fingerprint,
    query_fingerprints,
)

SKILL_MD = """---
name: db-query
description: 对关系型数据库执行只读查询。
---

正文。
"""


def _make_skill(root: Path, dir_name: str = "db-query", content: str = SKILL_MD) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    (skill_dir / SKILL_FILE_NAME).write_text(content, encoding="utf-8")
    return skill_dir


def _pool(root: Path) -> tuple[SkillRoot, ...]:
    return (SkillRoot(path=root, rank=100, source="skill-ability"),)


class TestQueryFingerprint:
    def test_unregistered_skill_is_not_drifted(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)

        result = query_fingerprint(skill_dir)

        assert result.full_name == "db-query"
        assert result.recorded is None
        assert result.lock_issue is None
        assert result.drifted is False

    def test_recorded_equals_current_after_registration(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)
        initialize_skills(_pool(tmp_path))

        result = query_fingerprint(skill_dir)

        assert result.recorded == result.current == fingerprint_skill(skill_dir)
        assert result.drifted is False

    def test_content_change_is_reported_as_drift(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)
        initialize_skills(_pool(tmp_path))
        before = query_fingerprint(skill_dir).current

        (skill_dir / SKILL_FILE_NAME).write_text(f"{SKILL_MD}\n新增一行。\n", encoding="utf-8")

        result = query_fingerprint(skill_dir)

        assert result.drifted is True
        assert result.recorded == before
        assert result.current != before

    def test_query_is_read_only(self, tmp_path: Path) -> None:
        """查询不给未登记的 skill 补 lock。"""
        skill_dir = _make_skill(tmp_path)

        query_fingerprint(skill_dir)

        assert not (skill_dir / LOCK_FILE_NAME).exists()

    def test_corrupt_lock_raises(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)
        (skill_dir / LOCK_FILE_NAME).write_text("这不是映射", encoding="utf-8")

        with pytest.raises(SkillLockError):
            query_fingerprint(skill_dir)

    def test_lock_without_fingerprint_raises(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)
        (skill_dir / LOCK_FILE_NAME).write_text(
            "schema: 1\nfull_name: db-query\n", encoding="utf-8"
        )

        with pytest.raises(SkillLockError):
            query_fingerprint(skill_dir)

    def test_missing_skill_md_raises(self, tmp_path: Path) -> None:
        (tmp_path / "db-query").mkdir()

        with pytest.raises(SkillParseError):
            query_fingerprint(tmp_path / "db-query")

    def test_invalid_dir_name_raises(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "Bad_Dir")

        with pytest.raises(SkillParseError):
            query_fingerprint(skill_dir)


class TestQueryFingerprints:
    def test_sorted_by_full_name_and_matches_registration(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "bbb", SKILL_MD.replace("db-query", "bbb"))
        _make_skill(tmp_path, "aaa", SKILL_MD.replace("db-query", "aaa"))
        initialize_skills(_pool(tmp_path))

        results = query_fingerprints(_pool(tmp_path))

        assert [r.full_name for r in results] == ["aaa", "bbb"]
        assert all(r.recorded is not None and r.drifted is False for r in results)

    def test_does_not_create_missing_root(self, tmp_path: Path) -> None:
        root = tmp_path / "nope"

        assert query_fingerprints(_pool(root)) == ()
        assert not root.exists()

    def test_keeps_going_on_corrupt_lock(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "aaa", SKILL_MD.replace("db-query", "aaa"))
        bad = _make_skill(tmp_path, "bbb", SKILL_MD.replace("db-query", "bbb"))
        initialize_skills(_pool(tmp_path))
        (bad / LOCK_FILE_NAME).write_text("坏掉了", encoding="utf-8")

        results = query_fingerprints(_pool(tmp_path))

        by_name = {r.full_name: r for r in results}
        assert [r.full_name for r in results] == ["aaa", "bbb"]
        assert by_name["aaa"].lock_issue is None
        assert by_name["aaa"].recorded is not None
        assert by_name["bbb"].lock_issue is not None
        assert by_name["bbb"].recorded is None
        assert by_name["bbb"].drifted is False
