# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""技能包生命周期：扫描 / 查表 / 正文 / 登记 / 指纹 / 重建（全走新格式）。

这组测试替代原先 ``tests/skills/`` 里以旧格式（包根 ``SKILL.md`` + frontmatter）
造包的用例：旧格式已停用，技能包统一是"声明了 ``skills`` 成员的能力包"，扫描
与登记都落在 :mod:`ghrah.scheme.loading.ability_scan`。

覆盖的边界：池的优先级与去重、扫描只读、精确查表、正文按需读取、缺正文/坏 lock
的"记问题而不是抛异常"、``initialize()`` 幂等且不覆盖、漂移检测与显式重建。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme.loading.ability_roots import path_root
from ghrah.scheme.loading.ability_scan import scan_ability_definitions
from ghrah.scheme.loading.skill_errors import SkillError, SkillNotFoundError, SkillParseError
from ghrah.scheme.loading.skill_lock import LOCK_FILE_NAME, LOCK_SCHEMA_VERSION
from ghrah.scheme.loading.skill_provider import SkillAbilityProvider

BODY = "# 步骤\n\n1. 先看 schema。\n"


def _package(
    root: Path,
    name: str,
    *,
    key: str | None = None,
    body: str | None = BODY,
    description: str = "对关系型数据库执行只读查询。",
    body_path: str = "skills/SKILL.md",
) -> Path:
    """造一个新格式能力包（``ability.yaml`` + 声明的正文文件）。

    Args:
        root: 目录池根。
        name: 包目录名，同时作为 ``identity.namespace``（故成员全名为 ``<name>.<key>``）。
        key: 技能成员键；None 取包名（"一个包至多一个技能"的约定）。
        body: 正文内容；None 表示故意不写正文文件（用于测缺正文）。
        description: 技能描述。
        body_path: 定义面声明的正文相对路径（默认本版约定的 ``skills/SKILL.md``）。

    Returns:
        新建的包目录。
    """
    member = name if key is None else key
    package = root / name
    (package / "skills").mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "ghrah.ability-definition",
        "schema_version": "0.1-draft",
        "identity": {"namespace": name, "version": "0.1.0"},
        "title": name,
        "description": "测试用能力包。",
        "provider_requirements": {},
        "operations": {},
        "skills": {
            member: {
                "title": name,
                "description": description,
                "body": {"path": body_path, "media_type": "text/markdown"},
            }
        },
        "assets": {},
    }
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if body is not None:
        target = package.joinpath(*body_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return package


def _provider(root: Path) -> SkillAbilityProvider:
    """由单个根目录造 provider。"""
    return SkillAbilityProvider.from_root(root)


class TestConstruction:
    """池与目录：构造不碰盘，池为空要报错（否则会静默返回空清单）。"""

    def test_empty_pool_is_rejected(self) -> None:
        with pytest.raises(SkillError):
            SkillAbilityProvider(pool=())

    def test_from_root_does_not_touch_filesystem(self, tmp_path: Path) -> None:
        root = tmp_path / "not-yet"

        provider = _provider(root)

        assert provider.pool[0].locator == root
        assert not root.exists()

    def test_ensure_roots_creates_nested_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / "a" / "b"

        assert _provider(root).ensure_roots() == (root,)

        assert root.is_dir()

    def test_default_provider_uses_default_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ghrah.scheme.loading import skill_provider as provider_module

        sentinel = (path_root(Path("/sentinel"), rank=1, source="s"),)
        monkeypatch.setattr(provider_module, "default_pool", lambda: sentinel)

        assert provider_module.default_provider().pool == sentinel


class TestScanAndLookup:
    """扫描口径：只读、按全名排序、精确查表、热插拔。"""

    def test_scan_returns_member_with_fingerprint(self, tmp_path: Path) -> None:
        _package(tmp_path, "db-query")

        result = _provider(tmp_path).scan()

        assert result.issues == ()
        assert len(result.skills) == 1
        skill = result.skills[0]
        assert skill.full_name == "db-query"
        assert skill.description == "对关系型数据库执行只读查询。"
        assert skill.body == tmp_path / "db-query" / "skills" / "SKILL.md"
        # 未登记：入口哨兵存在但不算漂移
        assert skill.fingerprint is not None
        assert skill.drifted is False

    def test_scan_is_read_only(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")

        _provider(tmp_path).scan()

        assert not (package / LOCK_FILE_NAME).exists()

    def test_missing_body_is_an_issue_not_a_crash(self, tmp_path: Path) -> None:
        _package(tmp_path, "db-query", body=None)

        result = _provider(tmp_path).scan()

        assert result.skills == ()
        assert len(result.issues) == 1
        assert "正文" in result.issues[0].reason

    def test_get_skill_accepts_full_name_and_catalog_name(self, tmp_path: Path) -> None:
        """全名、成员键、容器名都认：同一个技能在不同场合被叫成不同的名字。"""
        _package(tmp_path, "db-query")
        provider = _provider(tmp_path)

        assert provider.get_skill("db-query").key == "db-query"
        assert provider.get_skill("db-query").key == "db-query"
        with pytest.raises(SkillNotFoundError):
            provider.get_skill("db-que")

    def test_lower_rank_wins_on_duplicate_container(self, tmp_path: Path) -> None:
        top = tmp_path / "top"
        bottom = tmp_path / "bottom"
        _package(top, "db-query", body="# 高优先级\n")
        _package(bottom, "db-query", body="# 低优先级\n")
        provider = SkillAbilityProvider(pool=(path_root(top, rank=1), path_root(bottom, rank=2)))

        skills = provider.scan().skills

        assert len(skills) == 1
        assert skills[0].rank == 1
        assert provider.read_skill("db-query").content == "# 高优先级\n"

    def test_hot_plug_without_cache(self, tmp_path: Path) -> None:
        provider = _provider(tmp_path)
        assert provider.scan().skills == ()

        _package(tmp_path, "db-query")

        assert len(provider.scan().skills) == 1


class TestReadSkill:
    """正文按需读取：扫描有问题的包读不到，缺文件/非 UTF-8 报解析错。"""

    def test_returns_raw_content_and_paths(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")

        document = _provider(tmp_path).read_skill("db-query")

        assert document.full_name == "db-query"
        assert document.skill_dir == package
        assert document.path == package / "skills" / "SKILL.md"
        assert document.content == BODY

    def test_declared_body_path_is_used_not_the_default(self, tmp_path: Path) -> None:
        """读正文只认声明：正文叫 ``skills/GUIDE.md`` 时就去读那个文件。"""
        package = _package(tmp_path, "db-query", body_path="skills/GUIDE.md")

        document = _provider(tmp_path).read_skill("db-query")

        assert document.path == package / "skills" / "GUIDE.md"
        assert document.body_path == "skills/GUIDE.md"
        assert document.content == BODY

    def test_unknown_full_name_raises_not_found(self, tmp_path: Path) -> None:
        _package(tmp_path, "db-query")

        with pytest.raises(SkillNotFoundError):
            _provider(tmp_path).read_skill("nope.nope")

    def test_body_with_problem_is_not_readable(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        (package / "skills" / "SKILL.md").unlink()

        with pytest.raises(SkillNotFoundError):
            _provider(tmp_path).read_skill("db-query")

    def test_non_utf8_body_raises_parse_error(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        (package / "skills" / "SKILL.md").write_bytes(b"\xff\xfe\x00")

        with pytest.raises(SkillParseError):
            _provider(tmp_path).read_skill("db-query")


class TestInitializeAndDrift:
    """登记与漂移：initialize 幂等不覆盖，改正文能被看见，relock 才允许覆盖。"""

    def test_initialize_creates_lock_once(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)

        first = provider.initialize()
        second = provider.initialize()

        lock = package / LOCK_FILE_NAME
        assert lock.is_file()
        assert [outcome.created for outcome in first.locks] == [True]
        assert [outcome.created for outcome in second.locks] == [False]
        assert second.lock_issues == ()
        # 登记记录与扫描读数一致
        assert second.scan.skills[0].drifted is False

    def test_declared_body_path_registers_and_detects_drift(self, tmp_path: Path) -> None:
        """哨兵覆盖声明的那份正文：别名正文登记后不误报，改动能被看见。"""
        package = _package(tmp_path, "db-query", body_path="skills/GUIDE.md")
        provider = _provider(tmp_path)

        provider.initialize()

        assert provider.scan().skills[0].drifted is False
        (package / "skills" / "GUIDE.md").write_text("# 改过\n", encoding="utf-8")
        assert provider.scan().skills[0].drifted is True

    def test_body_change_shows_drift(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()

        (package / "skills" / "SKILL.md").write_text("# 改过\n", encoding="utf-8")

        assert provider.scan().skills[0].drifted is True

    def test_relock_clears_confirmed_drift(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()
        (package / "skills" / "SKILL.md").write_text("# 改过\n", encoding="utf-8")

        record = provider.relock("db-query")

        assert record.full_name == "db-query"
        assert provider.scan().skills[0].drifted is False

    def test_relock_unknown_member_raises_not_found(self, tmp_path: Path) -> None:
        _package(tmp_path, "db-query")

        with pytest.raises(SkillNotFoundError):
            _provider(tmp_path).relock("nope.nope")

    def test_corrupt_lock_is_reported_not_raised(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()
        (package / LOCK_FILE_NAME).write_text(": not yaml", encoding="utf-8")

        result = provider.scan()

        assert result.skills[0].lock_issue is not None
        assert result.issues == ()
        assert [issue.container for issue in result.lock_issues] == ["db-query"]

    def test_legacy_schema_lock_is_an_issue(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()
        (package / LOCK_FILE_NAME).write_text(
            yaml.safe_dump({"lock_schema": 1, "full_name": "db-query"}),
            encoding="utf-8",
        )

        skill = provider.scan().skills[0]

        assert skill.lock_issue is not None
        assert LOCK_SCHEMA_VERSION == 2

    def test_verify_reports_no_drift_after_initialize(self, tmp_path: Path) -> None:
        _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()

        verification = provider.verify("db-query")

        assert verification.content_drifted is False

    def test_verify_reports_content_drift(self, tmp_path: Path) -> None:
        package = _package(tmp_path, "db-query")
        provider = _provider(tmp_path)
        provider.initialize()
        (package / "notes.txt").write_text("附加文件\n", encoding="utf-8")

        verification = provider.verify("db-query")

        assert verification.content_drifted is True


class TestPoolIsTheOnlyAuthority:
    """目录池是"技能在哪"的唯一权威：扫描、查表、登记都读同一份池。"""

    def test_scan_uses_pool_order(self, tmp_path: Path) -> None:
        first = tmp_path / "first"
        second = tmp_path / "second"
        _package(first, "alpha")
        _package(second, "beta")
        provider = SkillAbilityProvider(pool=(path_root(first, rank=1), path_root(second, rank=2)))

        result = scan_ability_definitions(provider.pool)

        assert [skill.full_name for skill in result.skills] == ["alpha", "beta"]
