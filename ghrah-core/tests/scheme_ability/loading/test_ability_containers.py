# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""容器视图（``scan_abilities``）测试。

钉住「一次调用 → 一个对象列表，每个元素是一个 ability 包」这条契约：

- 容器是第一维度：名字、描述、三类成员（operations / skills / assets）都在同一个
  对象里，调用方不再按容器名做二次聚合；
- 成员按成员键升序、容器按容器名升序——跨文件系统、跨机器同一顺序；
- 单个包的毛病挂在该容器上（``issue.container``），根级毛病（扫描根缺失）不带容器；
- skill 成员只给声明面读数（键、标题、正文路径、媒体类型），不读正文。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme_ability.loading import ability_roots as roots
from ghrah.scheme_ability.loading.ability_scan import scan_abilities, scan_ability_definitions


def _operation(key: str, *, operation: str | None = None, timeout_ms: int = 1000) -> dict:
    """造一个 operation 成员声明。

    Args:
        key: ``operations`` 表里的键。
        operation: 槽内实现动作名；None 与键相同（最常见的一一对应）。
        timeout_ms: 执行约束里的超时上限。

    Returns:
        可直接 ``yaml.safe_dump`` 的成员映射。
    """
    return {
        "title": f"{key} 标题",
        "description": f"{key} 说明。",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "implementation": {"provider_slot": "backend", "operation": operation or key},
        "execution": {
            "mode": "request",
            "timeout_ms": timeout_ms,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
    }


def _skill(key: str, *, body_path: str = "skills/SKILL.md") -> dict:
    """造一个 skill 成员声明。

    Args:
        key: ``skills`` 表里的键。
        body_path: 正文相对路径。

    Returns:
        成员映射。
    """
    return {
        "title": f"{key} 标题",
        "description": f"{key} 技能说明。",
        "body": {"path": body_path, "media_type": "text/markdown"},
    }


def _definition(
    name: str,
    *,
    namespace: str | None = None,
    identity_name: str | None = None,
    title: str | None = None,
    operations: dict[str, dict] | None = None,
    skills: dict[str, dict] | None = None,
    assets: dict[str, object] | None = None,
) -> dict:
    """造一份合法的 ``ability.yaml`` 内容。

    Args:
        name: 包目录名（必须等于容器名）。
        namespace: ``identity.namespace``（单段）；None 取 ``name``。
        identity_name: ``identity.name``；None 表示省略（容器名回退到 namespace）。
        title: 容器标题；None 取 ``name``。
        operations: ``operations`` 表；None 表示不声明。
        skills: ``skills`` 表；None 表示不声明。
        assets: ``assets`` 表；None 表示不声明。

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
        "title": title or name,
        "description": f"{name} 容器的说明。",
    }
    if operations is not None:
        payload["provider_requirements"] = {"backend": {"contract": "ghrah.test.v1"}}
        payload["operations"] = operations
    if skills is not None:
        payload["skills"] = skills
    if assets is not None:
        payload["assets"] = assets
    return payload


def _write(root: Path, name: str, payload: dict, *, body: bool = True) -> Path:
    """在池根目录下落一个能力包。

    Args:
        root: 池根目录（调用方保证已建）。
        name: 包目录名。
        payload: ``ability.yaml`` 内容。
        body: 是否写入 ``skills/SKILL.md``（声明了技能时通常需要）。

    Returns:
        新建的包目录。
    """
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    if body:
        skill_dir = package / "skills"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    return package


class TestContainerShape:
    def test_one_object_per_package_with_identity(self, tmp_path: Path) -> None:
        """一次调用拿到每包一个对象，身份字段直取定义面。"""
        _write(
            tmp_path,
            "fs",
            _definition("fs", namespace="ghrah", identity_name="fs", title="文件系统"),
        )
        _write(tmp_path, "research", _definition("research"))
        pool = (roots.path_root(tmp_path, source="path"),)

        containers = scan_abilities(pool)

        assert [item.name for item in containers] == ["fs", "research"]
        fs = containers[0]
        assert fs.namespace == "ghrah"
        assert fs.full_name == "ghrah.fs"
        assert fs.title == "文件系统"
        assert fs.description == "fs 容器的说明。"
        assert fs.version == "0.1.0"
        assert fs.root.source == "path"

    def test_containers_are_sorted_by_name(self, tmp_path: Path) -> None:
        """容器名升序：与目录遍历顺序无关，可安全用于快照比对。"""
        for name in ("zeta", "alpha", "mike"):
            _write(tmp_path, name, _definition(name))

        containers = scan_abilities((roots.path_root(tmp_path),))

        assert [item.name for item in containers] == ["alpha", "mike", "zeta"]


class TestMembers:
    def test_operations_are_sorted_and_carry_declaration(self, tmp_path: Path) -> None:
        """operation 成员按键升序，全名 = 容器身份 + 成员键，字段直取声明。"""
        payload = _definition(
            "fs",
            namespace="ghrah",
            identity_name="fs",
            operations={
                "write_file": _operation("write_file", timeout_ms=5000),
                "read_file": _operation("read_file"),
            },
        )
        _write(tmp_path, "fs", payload, body=False)

        (container,) = scan_abilities((roots.path_root(tmp_path),))

        assert [op.key for op in container.operations] == ["read_file", "write_file"]
        read = container.operations[0]
        assert read.full_name == "ghrah.fs.read_file"
        assert read.handler == "read_file"
        assert read.provider_slot == "backend"
        assert read.timeout_ms == 1000
        assert read.title == "read_file 标题"
        assert container.operations[1].timeout_ms == 5000

    def test_skill_member_is_declaration_only(self, tmp_path: Path) -> None:
        """skill 成员只给声明面读数：有正文路径与媒体类型，正文由 ghrah.skills 读。"""
        payload = _definition("research", skills={"research": _skill("research")})
        _write(tmp_path, "research", payload)

        (container,) = scan_abilities((roots.path_root(tmp_path),))

        (skill,) = container.skills
        assert skill.key == "research"
        assert skill.full_name == "research"
        assert skill.body_path == "skills/SKILL.md"
        assert skill.media_type == "text/markdown"
        assert skill.source == container.root.source
        assert skill.body_locator is not None
        assert skill.body is not None
        assert skill.body.name == "SKILL.md"

    def test_mixed_container_reports_all_kinds(self, tmp_path: Path) -> None:
        """同一容器可同时有 operation 与 skill：两类成员都在，kinds 两者都列。"""
        payload = _definition(
            "mixed",
            operations={"ping": _operation("ping")},
            skills={"guide": _skill("guide")},
            assets={"readme": {"path": "README.md"}},
        )
        _write(tmp_path, "mixed", payload)

        (container,) = scan_abilities((roots.path_root(tmp_path),))

        assert container.kinds == ("operations", "skills", "assets")
        assert container.operation_count == 1
        assert container.skill_count == 1
        assert container.asset_count == 1
        assert container.member_count == 3
        assert container.is_operation_only is False
        assert container.is_skill_only is False
        assert container.assets[0].full_name == "mixed.readme"

    def test_skill_only_container_flags(self, tmp_path: Path) -> None:
        """只有 skills 的包（用户装进来的技能包）走 skill_only 分支。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )

        (container,) = scan_abilities((roots.path_root(tmp_path),))

        assert container.kinds == ("skills",)
        assert container.is_skill_only is True
        assert container.is_operation_only is False
        assert container.operation_count == 0
        assert container.member_count == 1


class TestIssues:
    def test_package_issue_is_attached_to_its_container(self, tmp_path: Path) -> None:
        """包内毛病记在容器上：容器仍出现在列表里（收集式，不丢包）。"""
        payload = _definition("escape", skills={"bad": _skill("bad", body_path="../outside.md")})
        _write(tmp_path, "escape", payload, body=False)

        (container,) = scan_abilities((roots.path_root(tmp_path),))

        assert container.name == "escape"
        assert [issue.container for issue in container.issues] == ["escape"]
        assert "不能越出容器目录" in container.issues[0].reason

    def test_root_issue_has_no_container(self, tmp_path: Path) -> None:
        """根级毛病（扫描根缺失）不带容器：它不属于任何包。"""
        pool = (
            roots.path_root(
                tmp_path / "gone",
                rank=roots.BUILTIN_ROOT_RANK,
                source="builtin",
                required=True,
            ),
        )

        containers = scan_abilities(pool)
        result = scan_ability_definitions(pool)

        assert containers == ()
        assert [issue.container for issue in result.issues] == [None]
        assert "扫描根不存在" in result.issues[0].reason

    def test_ignored_duplicate_issue_lands_on_winner(self, tmp_path: Path) -> None:
        """同名容器被优先级高者接管，落选那份在胜出容器上留一条记录。"""
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        builtin.mkdir()
        user.mkdir()
        _write(
            builtin,
            "fs",
            _definition("fs", namespace="ghrah", identity_name="fs"),
            body=False,
        )
        _write(user, "fs", _definition("fs"), body=False)
        pool = (
            roots.path_root(builtin, rank=0, source="builtin", required=True),
            roots.path_root(user, rank=100, source="skill-ability"),
        )

        containers = scan_abilities(pool)

        (container,) = containers
        assert container.root.source == "builtin"
        assert [issue.container for issue in container.issues] == ["fs"]
        assert "重复的容器名" in container.issues[0].reason


class TestSameScanAsDefinitions:
    def test_containers_view_matches_scan_result(self, tmp_path: Path) -> None:
        """``AbilityScanResult.containers`` 与 ``scan_abilities`` 是同一份读数。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )
        pool = (roots.path_root(tmp_path),)

        result = scan_ability_definitions(pool)

        assert [item.name for item in result.containers] == [
            item.name for item in scan_abilities(pool)
        ]
        assert result.containers[0].skills == scan_abilities(pool)[0].skills


if __name__ == "__main__":  # pragma: no cover
    sys_exit = pytest.main([__file__, "-v"])
    raise SystemExit(sys_exit)
