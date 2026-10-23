# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""``SkillAbilityProvider`` 的单元测试。

钉住四件事：**构造不碰文件系统**、空池被拒、按 ``full_name`` 精确查表（不切分
字符串）、**不缓存**（每次调用重扫 → 热插拔自然生效）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ghrah.skills import (
    LOCK_FILE_NAME,
    SKILL_FILE_NAME,
    SkillAbilityProvider,
    SkillError,
    SkillNotFoundError,
    SkillRoot,
    default_provider,
)

SKILL_MD = """---
name: db-query
description: 对关系型数据库执行只读查询。
---

正文。
"""


def _write_skill(root: Path, dir_name: str, content: str = SKILL_MD) -> Path:
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / SKILL_FILE_NAME).write_text(content, encoding="utf-8")
    return skill_dir


class TestConstruction:
    def test_empty_pool_is_rejected(self) -> None:
        with pytest.raises(SkillError):
            SkillAbilityProvider(pool=())

    def test_from_root_does_not_touch_filesystem(self, tmp_path: Path) -> None:
        root = tmp_path / "not-yet"

        provider = SkillAbilityProvider.from_root(root)

        assert provider.pool[0].path == root
        assert not root.exists()

    def test_default_provider_uses_default_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghrah.skills import provider as provider_module

        sentinel = (SkillRoot(path=Path("/sentinel"), rank=1, source="s"),)
        monkeypatch.setattr(provider_module, "default_pool", lambda: sentinel)

        assert default_provider().pool == sentinel

    def test_ensure_roots_creates_nested_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / "a" / "b"
        provider = SkillAbilityProvider.from_root(root)

        ensured = provider.ensure_roots()

        assert ensured == (root,)
        assert root.is_dir()


class TestScanAndLookup:
    def test_scan_returns_skills_and_issues(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")
        _write_skill(tmp_path, "broken", "没有 frontmatter\n")

        result = SkillAbilityProvider.from_root(tmp_path).scan()

        assert [skill.full_name for skill in result.skills] == ["db-query"]
        assert len(result.issues) == 1

    def test_pool_priority_across_roots(self, tmp_path: Path) -> None:
        """池里的多根目录按 rank 决胜：rank 小者胜出，落败者记 issue。"""
        high = tmp_path / "high"
        low = tmp_path / "low"
        _write_skill(high, "db-query")
        _write_skill(low, "db-query", SKILL_MD.replace("只读查询", "低优先级版本"))
        provider = SkillAbilityProvider(
            pool=(
                SkillRoot(path=low, rank=200, source="low"),
                SkillRoot(path=high, rank=100, source="high"),
            )
        )

        result = provider.scan()

        assert len(result.skills) == 1
        assert result.skills[0].source == "high"
        assert len(result.issues) == 1

    def test_get_skill_looks_up_by_full_name(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "org-db-query")
        provider = SkillAbilityProvider.from_root(tmp_path)

        skill = provider.get_skill("org-db-query")

        assert skill.name == "db-query"  # 目录名与 frontmatter 名允许不同
        assert skill.skill_dir == tmp_path / "org-db-query"

    def test_get_skill_does_not_split_full_name(self, tmp_path: Path) -> None:
        """精确查表：`-` 的切分有歧义，所以只认完整 full_name。"""
        _write_skill(tmp_path, "ai-for-everyone-research", SKILL_MD.replace("db-query", "research"))
        provider = SkillAbilityProvider.from_root(tmp_path)

        assert provider.get_skill("ai-for-everyone-research").name == "research"
        with pytest.raises(SkillNotFoundError):
            provider.get_skill("research")

    def test_get_skill_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SkillNotFoundError):
            SkillAbilityProvider.from_root(tmp_path).get_skill("nope")

    def test_hot_plug_without_cache(self, tmp_path: Path) -> None:
        provider = SkillAbilityProvider.from_root(tmp_path)
        assert provider.scan().skills == ()

        _write_skill(tmp_path, "db-query")

        assert [skill.full_name for skill in provider.scan().skills] == ["db-query"]


class TestFingerprintsAndInitialize:
    def test_initialize_creates_lock_then_fingerprint_matches(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")
        provider = SkillAbilityProvider.from_root(tmp_path)

        result = provider.initialize()
        entry = provider.get_fingerprint("db-query")

        assert sum(1 for outcome in result.locks if outcome.created) == 1
        assert entry.recorded == entry.current
        assert entry.drifted is False
        assert (skill_dir / LOCK_FILE_NAME).is_file()

    def test_get_fingerprint_reports_drift_after_change(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")
        provider = SkillAbilityProvider.from_root(tmp_path)
        provider.initialize()

        (skill_dir / SKILL_FILE_NAME).write_text(f"{SKILL_MD}\n新增一行。\n", encoding="utf-8")

        assert provider.get_fingerprint("db-query").drifted is True

    def test_get_fingerprint_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SkillNotFoundError):
            SkillAbilityProvider.from_root(tmp_path).get_fingerprint("nope")

    def test_query_fingerprints_is_read_only(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")
        provider = SkillAbilityProvider.from_root(tmp_path)

        provider.query_fingerprints()

        assert not (skill_dir / LOCK_FILE_NAME).exists()
