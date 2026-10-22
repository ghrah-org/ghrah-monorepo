# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ListAbilitiesAbility 测试：ability 发现面。

- 一次查询全查（没有 source 参数）；行内 kind 区分 core-ability / skill-ability
- 列表（档案面为行主键 + in_registry 标注）/ 单条详情（tool schema）
- diff 显式暴露两面差异（registered_without_manifest 等）
- ManifestStore / SkillAbilityProvider 未接线 → sources 说明缺席（零隐式）
- 文件系统 store 的自定义定义并入、损坏项跳过不阻断
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ghrah.abilities.base import ActionOutcome
from ghrah.abilities.builtin.list_abilities import ListAbilitiesAbility
from ghrah.abilities.context import AbilityExecutionContext
from ghrah.manifest.ability import AbilityManifest, AbilityMetadata
from ghrah.manifest.types import ImplementationDef, PermissionFlags
from ghrah.skills import SKILL_FILE_NAME, SkillAbilityProvider


def _ability_manifest(
    name: str = "demo",
    namespace: str = "custom.pkg",
    handler: str = "read_file",
    description: str = "custom ability for tests",
) -> AbilityManifest:
    return AbilityManifest(
        manifest="ability",
        version="1",
        metadata=AbilityMetadata(
            namespace=namespace,
            name=name,
            description=description,
            permissions=PermissionFlags(),
        ),
        implementation=ImplementationDef(type="builtin", handler=handler),
    )


class _FakeStore:
    """最小 ManifestStore stub：load_ability / list_abilities。"""

    def __init__(self, abilities: dict[str, AbilityManifest]) -> None:
        self._abilities = abilities

    def load_ability(self, full_name: str) -> AbilityManifest:
        if full_name not in self._abilities:
            raise KeyError(f"ability manifest not found: {full_name}")
        return self._abilities[full_name]

    def list_abilities(self) -> list[str]:
        return list(self._abilities)


class _BrokenStore:
    """list_abilities 抛错的 store（列表层应跳过文件系统面，不阻断）。"""

    def list_abilities(self) -> list[str]:
        raise RuntimeError("fs store unavailable")


def _make_context(
    store: Any = None,
    tool_args: dict[str, Any] | None = None,
    skill_provider: Any = None,
) -> AbilityExecutionContext:
    return AbilityExecutionContext(
        current_ability_name="list_abilities",
        tool_args=tool_args or {},
        manifest_store=store,
        skill_provider=skill_provider,
    )


def _write_skill(
    root: Path, dir_name: str, description: str = "对关系型数据库执行只读查询。"
) -> Path:
    """写一个最小 skill 包（SKILL.md with frontmatter）。"""
    skill_dir = root / dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / SKILL_FILE_NAME).write_text(
        f"---\nname: {dir_name}\ndescription: {description}\n---\n\n正文。\n",
        encoding="utf-8",
    )
    return skill_dir


def _rows_by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["full_name"]: row for row in data["abilities"]}


class TestListAbilitiesAbility:
    def test_name(self) -> None:
        assert ListAbilitiesAbility().name == "list_abilities"

    def test_bind_tool_schema(self) -> None:
        schema = ListAbilitiesAbility().bind_tool()
        func = schema["function"]
        assert func["name"] == "list_abilities"
        props = func["parameters"]["properties"]
        assert set(props) == {"full_name"}
        assert "required" not in func["parameters"]

    # ----------------------------------------------------------------
    # 列表层
    # ----------------------------------------------------------------

    async def test_list_all_builtin_manifests(self) -> None:
        """未接线 store：内置 manifest 面完整可用 + sources 显式说明缺席。"""
        result = await ListAbilitiesAbility().execute(_make_context())
        assert result.outcome == ActionOutcome.SUCCESS
        data = result.data
        rows = _rows_by_name(data)
        # 内置定义在列（18 项里加载白名单的每一项都应出现）
        assert "ghrah.fs.read_file" in rows
        assert "ghrah.core.list_abilities" in rows
        # 行标记：core-ability（skill-ability 行在 data["skills"]）
        assert all(row["kind"] == "core-ability" for row in data["abilities"])
        # in_registry 标注：read_file 的 handler 已注册
        assert rows["ghrah.fs.read_file"]["in_registry"] is True
        # 自身也在（本特性新增的静态声明）
        assert rows["ghrah.core.list_abilities"]["in_registry"] is True
        # sources 说明文件系统面缺席（零隐式）
        assert data["sources"]["manifests_fs_store"] is False
        assert "not wired" in data["sources"]["note"]

    async def test_list_includes_registry_only_diff(self) -> None:
        """registry-only 项（无 manifest 的注册 handler）进 diff 段。"""
        result = await ListAbilitiesAbility().execute(_make_context())
        diff = result.data["diff"]
        # 现状已知：command_proxy / send 有注册无 yaml
        assert "command_proxy" in diff["registered_without_manifest"]
        assert "send" in diff["registered_without_manifest"]

    async def test_list_with_fs_store_merges_custom(self) -> None:
        """接线的 store：文件系统自定义定义并入列表。"""
        store = _FakeStore({"custom.pkg.demo": _ability_manifest()})
        result = await ListAbilitiesAbility().execute(_make_context(store=store))
        assert result.outcome == ActionOutcome.SUCCESS
        rows = _rows_by_name(result.data)
        assert "custom.pkg.demo" in rows
        assert rows["custom.pkg.demo"]["description"] == "custom ability for tests"
        # handler=read_file 已注册 → in_registry True
        assert rows["custom.pkg.demo"]["in_registry"] is True
        assert result.data["sources"]["manifests_fs_store"] is True

    async def test_list_broken_store_falls_back_to_builtin(self) -> None:
        """store list_abilities 抛错 → 只剩内置面，不整体失败。"""
        result = await ListAbilitiesAbility().execute(_make_context(store=_BrokenStore()))
        assert result.outcome == ActionOutcome.SUCCESS
        rows = _rows_by_name(result.data)
        assert "ghrah.fs.read_file" in rows
        assert "custom.pkg.demo" not in rows

    # ----------------------------------------------------------------
    # 详情层
    # ----------------------------------------------------------------

    async def test_detail_builtin_with_tool_schema(self) -> None:
        result = await ListAbilitiesAbility().execute(
            _make_context(tool_args={"full_name": "ghrah.fs.read_file"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        data = result.data
        assert data["full_name"] == "ghrah.fs.read_file"
        assert data["kind"] == "core-ability"
        assert data["handler"] == "read_file"
        assert data["in_registry"] is True
        assert data["tool"]["function"]["name"] == "read_file"

    async def test_detail_no_tool_manifest(self) -> None:
        """无 tool 声明的 manifest（如 conversation）→ tool 为 None 不报错。"""
        result = await ListAbilitiesAbility().execute(
            _make_context(tool_args={"full_name": "ghrah.core.conversation"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["tool"] is None

    async def test_detail_from_fs_store(self) -> None:
        store = _FakeStore({"custom.pkg.demo": _ability_manifest()})
        result = await ListAbilitiesAbility().execute(
            _make_context(store=store, tool_args={"full_name": "custom.pkg.demo"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["full_name"] == "custom.pkg.demo"

    async def test_detail_registry_only_handler(self) -> None:
        """registry-only 项按 handler 名查询 → SUCCESS + note 说明无档案。"""
        result = await ListAbilitiesAbility().execute(
            _make_context(tool_args={"full_name": "command_proxy"})
        )
        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["in_registry"] is True
        assert result.data["kind"] == "core-ability"
        assert "registry-only" in result.data["note"]

    async def test_detail_not_found(self) -> None:
        result = await ListAbilitiesAbility().execute(
            _make_context(tool_args={"full_name": "no.such.ability"})
        )
        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    # ----------------------------------------------------------------
    # 注册与档案面（三层清单一致性）
    # ----------------------------------------------------------------

    async def test_manifest_without_registered_handler_flagged(self) -> None:
        """有档案但 handler 未注册 → diff.manifest_without_registered 标出。"""
        store = _FakeStore(
            {"custom.pkg.ghost": _ability_manifest(name="ghost", handler="no_such_handler")}
        )
        result = await ListAbilitiesAbility().execute(_make_context(store=store))
        diff = result.data["diff"]
        assert "custom.pkg.ghost" in diff["manifest_without_registered"]
        rows = _rows_by_name(result.data)
        assert rows["custom.pkg.ghost"]["in_registry"] is False

    def test_registered_in_registry(self) -> None:
        """本能力自身完成两级注册：Registry + 内置 yaml 白名单。"""
        from ghrah.abilities.registry import AbilityRegistry
        from ghrah.manifest.builtins import _BUILTIN_YAML_NAMES, load_all_builtin_manifests

        assert AbilityRegistry.has("list_abilities")
        assert "ghrah.core.list_abilities" in _BUILTIN_YAML_NAMES
        manifests = load_all_builtin_manifests()
        assert "ghrah.core.list_abilities" in manifests
        assert manifests["ghrah.core.list_abilities"].implementation.handler == "list_abilities"

    async def test_builtin_manifest_parses_and_binds(self) -> None:
        """新增 yaml 与类实现相互对齐：tool name 一致、handler 可解析。"""
        from ghrah.manifest.builtins import load_all_builtin_manifests

        manifest = load_all_builtin_manifests()["ghrah.core.list_abilities"]
        tool = ListAbilitiesAbility().bind_tool()
        assert manifest.tool is not None
        assert manifest.tool.name == tool["function"]["name"] == "list_abilities"


class TestListAbilitiesSkillAbility:
    """skill-ability 行：与 core-ability 同一次查询返回、kind 区分、未接线说明缺席。"""

    async def test_both_kinds_returned_and_marked(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")
        result = await ListAbilitiesAbility().execute(
            _make_context(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        )

        assert result.outcome == ActionOutcome.SUCCESS
        # 同一次查询里两种 kind 并存：core 行在 abilities，skill 行在 skills
        assert {row["kind"] for row in result.data["abilities"]} == {"core-ability"}
        rows = {row["full_name"]: row for row in result.data["skills"]}
        assert rows["db-query"]["kind"] == "skill-ability"
        assert rows["db-query"]["root"] == "skill-ability"
        assert rows["db-query"]["description"] == "对关系型数据库执行只读查询。"
        assert result.data["sources"]["skill_ability"] is True

    async def test_ability_rows_unchanged_and_count_includes_skills(self, tmp_path: Path) -> None:
        """ability 行契约不变；count = ability 行 + skill 行 + registry-only 项。"""
        baseline = await ListAbilitiesAbility().execute(_make_context())
        _write_skill(tmp_path, "db-query")
        wired = await ListAbilitiesAbility().execute(
            _make_context(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        )

        assert [row["full_name"] for row in wired.data["abilities"]] == [
            row["full_name"] for row in baseline.data["abilities"]
        ]
        assert wired.data["count"] == baseline.data["count"] + 1

    async def test_unwired_provider_reports_absence(self) -> None:
        result = await ListAbilitiesAbility().execute(_make_context())

        assert result.data["skills"] == []
        assert result.data["sources"]["skill_ability"] is False
        assert "not wired" in result.data["sources"]["skills_absent_reason"]

    async def test_skill_detail_by_full_name(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")
        result = await ListAbilitiesAbility().execute(
            _make_context(
                tool_args={"full_name": "db-query"},
                skill_provider=SkillAbilityProvider.from_root(tmp_path),
            )
        )

        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["kind"] == "skill-ability"
        assert result.data["skill_dir"] == str(skill_dir)
        assert result.data["frontmatter"]["name"] == "db-query"
        # 尚无 lock → 未登记（recorded None），不是漂移
        assert result.data["fingerprint"] == {
            "current": result.data["fingerprint"]["current"],
            "recorded": None,
            "drifted": False,
            "lock_issue": None,
        }

    async def test_unknown_full_name_still_fails(self, tmp_path: Path) -> None:
        """skill 面接线后，真正的未命中仍是 FAILURE（不为 skill 放宽 ability 文案）。"""
        _write_skill(tmp_path, "db-query")
        result = await ListAbilitiesAbility().execute(
            _make_context(
                tool_args={"full_name": "no.such.anything"},
                skill_provider=SkillAbilityProvider.from_root(tmp_path),
            )
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()

    async def test_broken_provider_does_not_break_list(self) -> None:
        class _BrokenProvider:
            def scan(self) -> None:
                raise RuntimeError("skill root unavailable")

        result = await ListAbilitiesAbility().execute(
            _make_context(skill_provider=_BrokenProvider())
        )

        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["skills"] == []
        assert result.data["abilities"]  # ability 面不受 skill 面故障影响

    async def test_unreadable_skill_is_omitted_not_fatal(self, tmp_path: Path) -> None:
        """坏 SKILL.md 不进清单（由 provider 的 issues 承载），其余技能照常列出。"""
        _write_skill(tmp_path, "db-query")
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / SKILL_FILE_NAME).write_text("没有 frontmatter\n", encoding="utf-8")

        result = await ListAbilitiesAbility().execute(
            _make_context(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        )

        assert [row["full_name"] for row in result.data["skills"]] == ["db-query"]
