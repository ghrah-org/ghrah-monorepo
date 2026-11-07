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

from ghrah.ability.loading import ability_roots as roots
from ghrah.ability.loading.ability_scan import (
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
    """造一份合法的 v1 ``ability.yaml`` 内容（两段式：头段 + ``members``）。

    Args:
        name: 目录名（同时是默认容器名）。
        namespace: ``identity.namespace``；None 取 ``name``（用户级包的单段身份）。
        identity_name: ``identity.name``；None 表示省略（容器名回退到 namespace）。
        skills: ``members.skills`` 表内容；None 表示不声明技能。
        operations: 是否加一个可执行成员（用户包通常没有实现脚本）。

    Returns:
        可直接 ``yaml.safe_dump`` 的映射。
    """
    identity: dict[str, object] = {"namespace": namespace or name, "version": "0.1.0"}
    if identity_name is not None:
        identity["name"] = identity_name
    members: dict[str, object] = {}
    if skills is not None:
        members["skills"] = skills
    if operations:
        members["operations"] = {
            f"{name}_op": {
                "title": "Op",
                "description": "一个测试成员。",
                "entry": f"operations/{name}_op.py",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "execution": {
                    "mode": "request",
                    "timeout_ms": 1000,
                    "cancellation": "unsupported",
                    "on_revoke": "drain",
                    "retry": "never",
                },
            }
        }
    return {
        "format": "ghrah.ability-definition",
        "schema_version": "1",
        "identity": identity,
        "title": name,
        "description": f"{name} 容器。",
        "members": members,
    }


def _skill_member(name: str, *, body_path: str | None = None) -> dict[str, object]:
    """造一个 v1 skill 成员声明。

    Args:
        name: 成员键（也决定规范正文位置 ``skills/<键>/SKILL.md``）。
        body_path: 正文相对路径；None 取 v1 规范位置（只有刻意构造非法定义时才传别的值）。

    Returns:
        ``members.skills`` 表里的一项。
    """
    return {
        "title": name,
        "description": f"{name} 技能。",
        "body": {
            "path": body_path or f"skills/{name}/SKILL.md",
            "media_type": "text/markdown",
        },
    }


def _write_package(
    root: Path,
    name: str,
    *,
    payload: dict[str, object] | None = None,
    body: bool = True,
) -> Path:
    """在根目录下写一个能力包（可选按定义面声明写入 skill 正文）。

    Args:
        root: 池根目录。
        name: 包目录名。
        payload: 定义内容；None 造一个只含技能的合法包。
        body: 是否按声明位置写入正文文件（v1 为 ``skills/<成员键>/SKILL.md``）。

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
        skills = (data.get("members") or {}).get("skills") or {}
        for member in skills.values():
            target = package.joinpath(*member["body"]["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    return package


class TestRoots:
    def test_external_source_requires_an_explicit_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """历史用户目录只在显式传入时参与扫描。"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        old_root = tmp_path / ".ghrah" / "skill-ability"
        _write_package(old_root, "research")

        result = scan_ability_definitions()

        assert BUILTIN_CONTAINERS <= {entry.container for entry in result.entries}
        assert "research" not in result.definitions
        explicit = scan_ability_definitions((*roots.builtin_pool(), roots.path_root(old_root)))
        assert "research" in explicit.definitions

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

    def test_directory_name_is_only_a_locator(self, tmp_path: Path) -> None:
        """v1：目录名不参与身份判定——与 ``identity.name`` 不一致也照常收录。

        容器名取定义面身份（``identity.name`` 优先），目录名只作定位；
        以前"两者不等就记问题并跳过"的行为已退役。
        """
        payload = _definition(
            "alpha",
            namespace="ghrah",
            identity_name="other",
            skills={"alpha": _skill_member("alpha")},
        )
        _write_package(tmp_path, "alpha", payload=payload)

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["other"]
        assert result.definitions.keys() == {"other"}
        assert result.issues == ()

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
        assert skill.body_path == "skills/research/SKILL.md"
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
        """正文路径不能越出容器目录：v1 契约要求它必须是 ``skills/<键>/SKILL.md``。

        v1 把「正文位置」收紧成唯一规范路径，因此越界写法在解析期就被拒绝，不会
        进入扫描面（扫描层仍保留越界判定，作为纵深防御）。
        """
        payload = _definition(
            "research",
            skills={"research": _skill_member("research", body_path="../outside.md")},
        )
        _write_package(tmp_path, "research", payload=payload, body=False)
        (tmp_path / "outside.md").write_text("secret", encoding="utf-8")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert "body.path 必须是" in result.issues[0].reason
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
        """正文位置只认定义面声明（v1 即 ``skills/<键>/SKILL.md``）。"""
        payload = _definition(
            "research",
            skills={"research": _skill_member("research")},
        )
        _write_package(tmp_path, "research", payload=payload, body=False)
        skill_dir = tmp_path / "research" / "skills" / "research"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# guide\n", encoding="utf-8", newline="\n")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        (skill,) = result.skills
        assert skill.body_path == "skills/research/SKILL.md"
        assert skill.body == tmp_path / "research" / "skills" / "research" / "SKILL.md"
        assert result.issues == ()

    def test_multiple_skill_members_are_supported(self, tmp_path: Path) -> None:
        """一个能力包可以声明多个 skill：每个技能有独立目录与成员键。"""
        payload = _definition(
            "multi",
            skills={
                "a": _skill_member("a"),
                "b": _skill_member("b"),
            },
        )
        package = _write_package(tmp_path, "multi", payload=payload, body=False)
        for key in ("a", "b"):
            body = package / "skills" / key / "SKILL.md"
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text(f"# {key}\n", encoding="utf-8")

        result = scan_ability_definitions((roots.path_root(tmp_path),))

        assert [entry.container for entry in result.entries] == ["multi"]
        assert [skill.key for skill in result.skills] == ["a", "b"]
        # 多技能包的目录名必须带归属，否则模型无法区分同名技能。
        assert [skill.catalog_name for skill in result.skills] == ["multi/a", "multi/b"]
