# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""能力目录面扫描（``ability_scan``）测试。

目录面与实现面的差别，这里逐条钉住：

- 扫**整个池**（内置树 + 用户目录），不只内置树；
- **收集式**：单个包坏了记一条问题继续扫（外部目录是输入，不能因为一份坏文件
  就让整面不可读）；
- skill 成员在这里是**声明面读数**：给身份、标题、正文路径与来源，不读正文。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme_ability.loading import ability_roots as roots
from ghrah.scheme_ability.loading.ability_scan import (
    load_ability_skills,
    scan_ability_definitions,
)

BUILTIN_CONTAINERS = {"fs", "core", "cluster", "shell"}


def _definition(
    name: str,
    *,
    namespace: str | None = None,
    identity_name: str | None = None,
    skills: dict[str, object] | None = None,
    operations: bool = False,
) -> dict[str, object]:
    """造一份合法的 ``ability.yaml`` 内容。

    Args:
        name: 目录名（同时是默认容器名）。
        namespace: ``identity.namespace``；None 取 ``name``（用户级包的单段身份）。
        identity_name: ``identity.name``；None 表示省略（容器名回退到 namespace）。
        skills: ``skills`` 表内容；None 表示不声明技能。
        operations: 是否加一个可执行成员（用户包通常没有实现脚本）。

    Returns:
        可直接 ``yaml.safe_dump`` 的映射。
    """
    identity: dict[str, object] = {"namespace": namespace or name, "version": "0.1.0"}
    if identity_name is not None:
        identity["name"] = identity_name
    payload: dict[str, object] = {
        "format": "ghrah.ability-definition",
        "schema_version": "0.1-draft",
        "identity": identity,
        "title": name,
        "description": f"{name} 容器。",
    }
    if skills is not None:
        payload["skills"] = skills
    if operations:
        payload["provider_requirements"] = {"backend": {"contract": "ghrah.test.v1"}}
        payload["operations"] = {
            f"{name}_op": {
                "title": "Op",
                "description": "一个测试成员。",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
                "implementation": {"provider_slot": "backend", "operation": f"{name}_op"},
                "execution": {
                    "mode": "request",
                    "timeout_ms": 1000,
                    "cancellation": "unsupported",
                    "on_revoke": "drain",
                    "retry": "never",
                },
            }
        }
    return payload


def _skill_member(name: str, *, body_path: str = "skills/SKILL.md") -> dict[str, object]:
    """造一个 skill 成员声明。

    Args:
        name: 成员键。
        body_path: 正文相对路径。

    Returns:
        ``skills`` 表里的一项。
    """
    return {
        "title": name,
        "description": f"{name} 技能。",
        "body": {"path": body_path, "media_type": "text/markdown"},
    }


def _write_package(
    root: Path,
    name: str,
    *,
    payload: dict[str, object] | None = None,
    body: bool = True,
) -> Path:
    """在根目录下写一个能力包（可选写入 ``skills/SKILL.md``）。

    Args:
        root: 池根目录。
        name: 包目录名。
        payload: 定义内容；None 造一个只含技能的合法包。
        body: 是否写入声明的正文文件。

    Returns:
        新建的包目录。
    """
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    data = payload if payload is not None else _definition(name, skills={name: _skill_member(name)})
    (package / "ability.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    if body:
        skill_dir = package / "skills"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    return package


class TestRoots:
    def test_default_pool_sees_user_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """默认池 = 内置树 + 用户目录：装进去的用户包能被扫到。"""
        monkeypatch.setattr(roots, "default_user_root", lambda: tmp_path)
        _write_package(tmp_path, "research")

        result = scan_ability_definitions()

        assert BUILTIN_CONTAINERS <= {entry.container for entry in result.entries}
        assert "research" in result.definitions
        assert [root.source for root in result.roots] == ["builtin", "skill-ability"]

    def test_missing_required_root_is_reported(self, tmp_path: Path) -> None:
        """``required`` 根缺失要报出来：那是环境坏了，不是"空目录"。"""
        pool = (
            roots.path_root(
                tmp_path / "gone",
                rank=roots.BUILTIN_ROOT_RANK,
                source="builtin",
                required=True,
            ),
        )

        result = scan_ability_definitions(pool)

        assert result.entries == ()
        assert [str(issue) for issue in result.issues] == [f"{tmp_path / 'gone'}: 扫描根不存在"]

    def test_missing_optional_root_is_silent(self, tmp_path: Path) -> None:
        """用户目录尚未创建不是问题（首次安装的常态）。"""
        result = scan_ability_definitions((roots.path_root(tmp_path / "gone"),))

        assert result.entries == ()
        assert result.issues == ()


class TestCollection:
    def test_both_roots_are_scanned(self, tmp_path: Path) -> None:
        """两个根里的包都进结果，``roots`` 按优先级排序。"""
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        _write_package(builtin, "alpha")
        _write_package(user, "beta")
        pool = (
            roots.path_root(user, rank=roots.USER_ROOT_RANK, source="user"),
            roots.path_root(builtin, rank=roots.BUILTIN_ROOT_RANK, source="builtin"),
        )

        result = scan_ability_definitions(pool)

        assert [entry.container for entry in result.entries] == ["alpha", "beta"]
        assert [root.source for root in result.roots] == ["builtin", "user"]

    def test_lower_rank_wins_and_the_loss_is_visible(self, tmp_path: Path) -> None:
        """同名容器由 rank 小者胜出，被忽略的那份记一条问题（不静默覆盖）。"""
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        _write_package(
            builtin, "alpha", payload=_definition("alpha", skills={"a": _skill_member("a")})
        )
        _write_package(
            user, "alpha", payload=_definition("alpha", skills={"b": _skill_member("b")})
        )
        pool = (
            roots.path_root(builtin, rank=roots.BUILTIN_ROOT_RANK, source="builtin"),
            roots.path_root(user, rank=roots.USER_ROOT_RANK, source="user"),
        )

        result = scan_ability_definitions(pool)

        assert [entry.container for entry in result.entries] == ["alpha"]
        assert result.entries[0].root.source == "builtin"
        assert [skill.key for skill in result.skills] == ["a"]
        assert len(result.issues) == 1
        assert "重复的容器名" in result.issues[0].reason
        assert "builtin" in result.issues[0].reason

    def test_bad_definition_does_not_stop_the_scan(self, tmp_path: Path) -> None:
        """一份坏定义只记问题，其余包照常扫出来。"""
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "ability.yaml").write_text("format: nope\n", encoding="utf-8")
        _write_package(tmp_path, "ok")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["ok"]
        assert len(result.issues) == 1
        assert "broken" in result.issues[0].path

    def test_container_name_must_match_directory(self, tmp_path: Path) -> None:
        """目录名只作定位，身份以定义面为准；两者不等就记问题并跳过。"""
        _write_package(tmp_path, "alpha", payload=_definition("alpha", namespace="other"))

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert result.entries == ()
        assert "容器名" in result.issues[0].reason

    def test_directories_without_definition_are_skipped(self, tmp_path: Path) -> None:
        """普通子目录（``operations/`` 之类）不是能力包，静默跳过。"""
        _write_package(tmp_path, "alpha")
        (tmp_path / "operations").mkdir()

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["alpha"]
        assert result.issues == ()

    def test_hidden_and_private_directories_are_skipped(self, tmp_path: Path) -> None:
        """``_`` / ``.`` 开头的目录是工作区暂存，不当能力包。"""
        _write_package(tmp_path, "alpha")
        for name in ("_draft", ".cache"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "ability.yaml").write_text("format: x\n", encoding="utf-8")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["alpha"]
        assert result.issues == ()


class TestSkills:
    def test_skill_member_carries_identity_and_source(self, tmp_path: Path) -> None:
        """skill 成员给出全名、正文路径、来源与优先级。"""
        _write_package(tmp_path, "research")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        (skill,) = result.skills
        assert skill.container == "research"
        assert skill.key == "research"
        assert skill.full_name == "research"
        assert skill.body_path == "skills/SKILL.md"
        assert skill.media_type == "text/markdown"
        assert skill.source == "skill-ability"
        assert skill.rank == roots.USER_ROOT_RANK
        assert skill.body_locator.is_file()

    def test_missing_body_is_a_scan_issue(self, tmp_path: Path) -> None:
        """声明了正文但文件不在 → 记问题（仍保留包本身）。"""
        _write_package(tmp_path, "research", body=False)

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["research"]
        assert "声明的正文不存在" in result.issues[0].reason

    def test_body_may_not_escape_the_package(self, tmp_path: Path) -> None:
        """正文路径不能越出容器目录，否则一份外部 yaml 就能读到包外文件。"""
        payload = _definition(
            "research",
            skills={"research": _skill_member("research", body_path="../outside.md")},
        )
        _write_package(tmp_path, "research", payload=payload)
        (tmp_path / "outside.md").write_text("secret", encoding="utf-8")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert "正文路径非法" in result.issues[0].reason
        assert result.skills == ()

    def test_skills_sorted_by_full_name(self, tmp_path: Path) -> None:
        """成员按全名升序，跨根也稳定。"""
        first = tmp_path / "a"
        second = tmp_path / "b"
        _write_package(first, "zeta")
        _write_package(second, "alpha")

        result = scan_ability_definitions(
            (roots.path_root(first), roots.path_root(second, rank=1, source="other"))
        )

        assert [skill.full_name for skill in result.skills] == ["alpha", "zeta"]

    def test_load_ability_skills_shortcut(self, tmp_path: Path) -> None:
        """薄封装只返回成员声明。"""
        _write_package(tmp_path, "research")

        skills = load_ability_skills((roots.path_root(tmp_path),))

        assert [skill.full_name for skill in skills] == ["research"]

    def test_definition_without_skills_yields_none(self, tmp_path: Path) -> None:
        """纯操作包不产出 skill 成员。"""
        _write_package(tmp_path, "alpha", payload=_definition("alpha", operations=True))

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["alpha"]
        assert result.skills == ()

    def test_body_follows_declared_path(self, tmp_path: Path) -> None:
        """正文位置只认声明：声明别处时读取就落到那个文件，框架不拼默认路径。"""
        payload = _definition(
            "research",
            skills={"research": _skill_member("research", body_path="skills/GUIDE.md")},
        )
        _write_package(tmp_path, "research", payload=payload, body=False)
        package = tmp_path / "research" / "skills"
        package.mkdir()
        (package / "GUIDE.md").write_text("# guide\n", encoding="utf-8", newline="\n")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        (skill,) = result.skills
        assert skill.body_path == "skills/GUIDE.md"
        assert skill.body == tmp_path / "research" / "skills" / "GUIDE.md"
        assert result.issues == ()

    def test_second_skill_member_is_rejected(self, tmp_path: Path) -> None:
        """一个能力包只支持一个 skill 成员：声明两个时整份定义非法。

        容器与技能一一对应（技能是容器的插件包），多条声明没有可表达的落点，
        因此在契约层 fail-closed——扫描把它记成问题，而不是只收其中一条。
        """
        payload = _definition(
            "multi",
            skills={"a": _skill_member("a"), "b": _skill_member("b")},
        )
        _write_package(tmp_path, "multi", payload=payload, body=False)

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert result.entries == ()
        assert result.skills == ()
        assert "只支持一个 skill 成员" in result.issues[0].reason
