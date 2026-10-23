# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""skill 初始化（ability.lock.yaml 首次生成）的单元测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from ghrah.skills import (
    LOCK_FILE_NAME,
    LOCK_HEADER,
    SKILL_FILE_NAME,
    SkillRoot,
    build_lock,
    fingerprint_skill,
    initialize_skills,
    render_lock,
)

SKILL_MD_WITH_PERMISSION = """---
name: db-query
description: 对关系型数据库执行只读查询。
permission:
  approve: auto
  files:
    read: ["/data/**"]
  net: deny
---

正文。
"""

SKILL_MD_MINIMAL = """---
name: db-query
description: 对关系型数据库执行只读查询。
---

正文。
"""

SKILL_MD_EXTRA_PERMISSION = """---
name: db-query
description: 对关系型数据库执行只读查询。
permission:
  shell: deny
---

正文。
"""


def _make_skill(root: Path, dir_name: str, content: str) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True)
    (skill_dir / SKILL_FILE_NAME).write_text(content, encoding="utf-8")
    return skill_dir


def _pool(root: Path) -> tuple[SkillRoot, ...]:
    return (SkillRoot(path=root, rank=100, source="skill-ability"),)


def _read_lock(skill_dir: Path) -> dict:
    return yaml.safe_load((skill_dir / LOCK_FILE_NAME).read_text(encoding="utf-8"))


class TestInitializeSkills:
    def test_creates_missing_root(self, tmp_path: Path) -> None:
        root = tmp_path / "skill-ability"

        result = initialize_skills(_pool(root))

        assert root.is_dir()
        assert result.scan.skills == ()
        assert result.locks == ()
        assert result.lock_issues == ()

    def test_creates_lock_with_expected_content(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_WITH_PERMISSION)

        result = initialize_skills(_pool(tmp_path))

        assert len(result.locks) == 1
        outcome = result.locks[0]
        assert outcome.created is True
        assert outcome.reason == "首次生成"
        assert outcome.path == skill_dir / LOCK_FILE_NAME

        text = (skill_dir / LOCK_FILE_NAME).read_text(encoding="utf-8")
        assert text.startswith(LOCK_HEADER)
        assert "\r\n" not in text

        data = yaml.safe_load(text)
        assert data["schema"] == 1
        assert data["full_name"] == "db-query"
        assert data["version"] == "0.1.0"
        assert data["provenance"]["source"] == "skill-ability"
        assert data["provenance"]["fingerprint"].startswith("sha256:")
        assert data["permission"] == {
            "approve": "auto",
            "files": {"read": ["/data/**"]},
            "net": "deny",
        }
        assert data["protected"] is False

    def test_full_name_comes_from_package_dir_name(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "org-db-query", SKILL_MD_MINIMAL)

        initialize_skills(_pool(tmp_path))

        assert _read_lock(skill_dir)["full_name"] == "org-db-query"

    def test_permission_defaults_are_conservative_when_absent(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_MINIMAL)

        initialize_skills(_pool(tmp_path))

        assert _read_lock(skill_dir)["permission"] == {
            "approve": "hitl",
            "files": {},
            "net": "deny",
        }

    def test_keeps_extra_permission_keys(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_EXTRA_PERMISSION)

        initialize_skills(_pool(tmp_path))

        permission = _read_lock(skill_dir)["permission"]
        assert permission["shell"] == "deny"
        assert permission["approve"] == "hitl"

    def test_is_idempotent_and_never_overwrites(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_WITH_PERMISSION)
        initialize_skills(_pool(tmp_path))
        lock_path = skill_dir / LOCK_FILE_NAME
        first = lock_path.read_text(encoding="utf-8")

        (skill_dir / SKILL_FILE_NAME).write_text(SKILL_MD_MINIMAL, encoding="utf-8")
        result = initialize_skills(_pool(tmp_path))

        assert result.locks[0].created is False
        assert "已存在" in result.locks[0].reason
        assert lock_path.read_text(encoding="utf-8") == first

    def test_corrupt_lock_is_kept_and_reported(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_MINIMAL)
        lock_path = skill_dir / LOCK_FILE_NAME
        lock_path.write_text("这不是映射", encoding="utf-8")

        result = initialize_skills(_pool(tmp_path))

        assert lock_path.read_text(encoding="utf-8") == "这不是映射"
        assert result.locks[0].created is False
        assert "不可解析" in result.locks[0].reason
        assert len(result.lock_issues) == 1

    def test_invalid_skill_is_reported_without_lock(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "broken", "没有 frontmatter\n")

        result = initialize_skills(_pool(tmp_path))

        assert result.scan.skills == ()
        assert len(result.scan.issues) == 1
        assert result.locks == ()
        assert not (skill_dir / LOCK_FILE_NAME).exists()

    def test_ensure_dirs_false_reports_missing_root(self, tmp_path: Path) -> None:
        root = tmp_path / "missing"

        result = initialize_skills(_pool(root), ensure_dirs=False)

        assert not root.exists()
        assert len(result.scan.issues) == 1
        assert "目录不存在" in result.scan.issues[0].reason


class TestFingerprintAndRender:
    def test_fingerprint_tracks_content(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "db-query", SKILL_MD_MINIMAL)
        before = fingerprint_skill(skill_dir)

        (skill_dir / SKILL_FILE_NAME).write_text(SKILL_MD_WITH_PERMISSION, encoding="utf-8")

        assert before.startswith("sha256:")
        assert fingerprint_skill(skill_dir) != before

    def test_render_lock_keeps_header_and_key_order(self) -> None:
        data = build_lock(
            full_name="a-b",
            source="skill-ability",
            fingerprint="sha256:0",
            frontmatter={},
        )

        text = render_lock(data)

        assert text.startswith(LOCK_HEADER)
        top_level_keys = [
            line.split(":")[0]
            for line in text.splitlines()
            if line and not line.startswith((" ", "#"))
        ]
        assert top_level_keys == [
            "schema",
            "full_name",
            "version",
            "provenance",
            "permission",
            "protected",
        ]
