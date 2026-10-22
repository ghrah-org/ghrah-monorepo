# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SkillUnit 装配期初始化的单元测试。

根目录一律显式指向 ``tmp_path``（conftest 的 dataclass 默认值隔离只是兜底，
本文件不依赖它），确保测试绝不写真实 ``~/.ghrah/skill-ability``。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import SkillConfig, SubjectConfig
from ghrah.subject.units.skill import SkillUnit

SKILL_MD = """---
name: db-query
description: 对关系型数据库执行只读查询。
permission:
  approve: auto
---

正文。
"""


def _config(tmp_path: Path) -> SubjectConfig:
    return SubjectConfig(
        workspace_root=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "subject.db"),
        manifest_root=str(tmp_path / "manifests"),
        skill_slice=SkillConfig(root=str(tmp_path / "skill-ability")),
    )


def _make_skill(root: Path, name: str, content: str = SKILL_MD) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_meta_declares_no_routes_and_no_services(tmp_path: Path) -> None:
    """零隐式行为：不发布服务、不注册命令、不订阅事件、不依赖任何服务。"""
    meta = SkillUnit(_config(tmp_path)).meta

    assert meta.name == "skill"
    assert meta.requires == frozenset()
    assert meta.provides == frozenset()
    assert meta.routes.commands == frozenset()
    assert meta.routes.long_running_commands == frozenset()
    assert meta.routes.events == frozenset()


async def test_init_creates_root_when_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    root = Path(config.skill.root)
    assert not root.exists()

    async with Context() as ctx:
        await SkillUnit(config).init(ctx)

    assert root.is_dir()


async def test_init_creates_lock_for_new_skill(tmp_path: Path) -> None:
    config = _config(tmp_path)
    skill_dir = _make_skill(Path(config.skill.root), "db-query")

    async with Context() as ctx:
        await SkillUnit(config).init(ctx)

    lock_path = skill_dir / "ability.lock.yaml"
    assert lock_path.is_file()
    data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["full_name"] == "db-query"
    assert data["permission"]["approve"] == "auto"
    assert data["provenance"]["fingerprint"].startswith("sha256:")


async def test_init_is_idempotent(tmp_path: Path) -> None:
    """重复装配不覆盖既有 lock（缓存/重启不改变已登记状态）。"""
    config = _config(tmp_path)
    skill_dir = _make_skill(Path(config.skill.root), "db-query")
    lock_path = skill_dir / "ability.lock.yaml"

    async with Context() as ctx:
        unit = SkillUnit(config)
        await unit.init(ctx)
        first = lock_path.read_text(encoding="utf-8")
        await unit.init(ctx)

    assert lock_path.read_text(encoding="utf-8") == first


async def test_init_logs_warning_for_broken_skill(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """扫描问题必须显式告警（fail-closed：不静默变成空目录池）。"""
    config = _config(tmp_path)
    _make_skill(Path(config.skill.root), "broken", "没有 frontmatter\n")

    caplog.set_level(logging.WARNING, logger="ghrah.subject.units.skill")
    async with Context() as ctx:
        await SkillUnit(config).init(ctx)

    assert "Skill issue at" in caplog.text
    assert not (Path(config.skill.root) / "broken" / "ability.lock.yaml").exists()


async def test_init_logs_summary(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config = _config(tmp_path)
    _make_skill(Path(config.skill.root), "db-query")

    caplog.set_level(logging.INFO, logger="ghrah.subject.units.skill")
    async with Context() as ctx:
        await SkillUnit(config).init(ctx)

    assert "skills=1" in caplog.text
    assert "locks_created=1" in caplog.text
