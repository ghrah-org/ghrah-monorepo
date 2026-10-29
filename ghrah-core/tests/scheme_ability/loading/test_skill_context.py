# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""模型技能面（``load_skill_catalog``）测试。

钉住「目录文案 + 正文按需读取」这条契约：

- 目录里显示的名字是**成员键**（``research``），不是限定名——模型拿它调加载入口；
- 目录摘要只看「名字 + 一行描述」，正文改动不触发重新注入；
- 正文逐字返回，读不出来（被删 / 不是 UTF-8）统一抛 ``SkillContextError``；
- 只有声明了 ``skills`` 的包进技能面，纯 operations 包不出现；
- 渲染函数只依赖 ``catalog_name`` / ``description`` 两个属性，因此
  ``ghrah.skills`` 的 ``SkillSummary`` 也能直接喂进来。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghrah.scheme_ability.loading import ability_roots as roots
from ghrah.scheme_ability.loading.skill_context import (
    CATALOG_DESCRIPTION_MAX_CHARS,
    SkillContextError,
    catalog_description,
    digest_skill_catalog,
    load_skill_catalog,
    read_skill_text,
    render_skill_catalog,
)


def _operation(key: str) -> dict:
    """造一个 operation 成员声明（只为验证"纯操作包不进技能面"）。"""
    return {
        "title": f"{key} 标题",
        "description": f"{key} 说明。",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "implementation": {"provider_slot": "backend", "operation": key},
        "execution": {
            "mode": "request",
            "timeout_ms": 1000,
            "cancellation": "unsupported",
            "on_revoke": "drain",
            "retry": "never",
        },
    }


def _skill(key: str, *, description: str | None = None) -> dict:
    """造一个 skill 成员声明。"""
    return {
        "title": f"{key} 标题",
        "description": description or f"{key} 技能说明。",
        "body": {"path": "skills/SKILL.md", "media_type": "text/markdown"},
    }


def _definition(
    name: str,
    *,
    operations: dict[str, dict] | None = None,
    skills: dict[str, dict] | None = None,
) -> dict:
    """造一份合法的 ``ability.yaml`` 内容。"""
    payload: dict[str, object] = {
        "format": "ghrah.ability-definition",
        "schema_version": "0.1-draft",
        "identity": {"namespace": name, "version": "0.1.0"},
        "title": name,
        "description": f"{name} 容器的说明。",
    }
    if operations is not None:
        payload["provider_requirements"] = {"backend": {"contract": "ghrah.test.v1"}}
        payload["operations"] = operations
    if skills is not None:
        payload["skills"] = skills
    return payload


def _write(root: Path, name: str, payload: dict, *, body: str | None = "# body\n") -> Path:
    """在池根目录下落一个能力包。"""
    package = root / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    if body is not None:
        skill_dir = package / "skills"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_text(body, encoding="utf-8", newline="\n")
    return package


def _pool(path: Path) -> tuple[roots.AbilityRoot, ...]:
    """把临时目录包成单个池根。"""
    return (roots.path_root(path, source="path"),)


class TestSkillCatalogShape:
    def test_names_are_member_keys_not_qualified(self, tmp_path: Path) -> None:
        """目录名取成员键；限定名只用于去重与定位。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )

        catalog = load_skill_catalog(_pool(tmp_path))

        assert catalog.names == ("research",)
        skill = catalog.get("research")
        assert skill is not None
        assert skill.catalog_name == "research"
        assert skill.full_name == "research"
        assert skill.container == "research"

    def test_operations_only_package_stays_out(self, tmp_path: Path) -> None:
        """纯 operations 包不贡献技能。"""
        _write(tmp_path, "fs", _definition("fs", operations={"read_file": _operation("read_file")}))

        catalog = load_skill_catalog(_pool(tmp_path))

        assert catalog.names == ()
        assert catalog.skills == ()
        assert catalog.issues == ()

    def test_skills_from_two_packages_are_sorted(self, tmp_path: Path) -> None:
        """跨包成员按限定名升序，名字不撞。"""
        _write(tmp_path, "zeta", _definition("zeta", skills={"tool": _skill("tool")}))
        _write(tmp_path, "alpha", _definition("alpha", skills={"learn": _skill("learn")}))

        catalog = load_skill_catalog(_pool(tmp_path))

        assert catalog.names == ("learn", "tool")
        assert [skill.full_name for skill in catalog.skills] == [
            "alpha.learn",
            "zeta.tool",
        ]

    def test_user_root_wins_on_duplicate_member(self, tmp_path: Path) -> None:
        """同名成员两条来源：rank 小者胜出，后到的记问题。"""
        builtin = tmp_path / "builtin"
        user = tmp_path / "user"
        payload = _definition("research", skills={"research": _skill("research")})
        _write(builtin, "research", payload, body="# 内置\n")
        _write(user, "research", payload, body="# 用户\n")
        pool = (
            roots.path_root(user, rank=100, source="skill-ability"),
            roots.path_root(builtin, rank=0, source="builtin"),
        )

        catalog = load_skill_catalog(pool)

        skill = catalog.get("research")
        assert skill is not None
        assert skill.source == "builtin"
        assert catalog.read("research").content == "# 内置\n"


class TestSkillDigest:
    def test_digest_ignores_order(self, tmp_path: Path) -> None:
        """顺序无关——同一集合必须有同一摘要。"""
        _write(tmp_path, "a", _definition("a", skills={"learn": _skill("learn")}))
        _write(tmp_path, "b", _definition("b", skills={"tool": _skill("tool")}))

        first = load_skill_catalog(_pool(tmp_path))
        second = load_skill_catalog(_pool(tmp_path))

        assert first.digest == second.digest
        assert first.digest == digest_skill_catalog(tuple(reversed(first.skills)))

    def test_body_change_keeps_digest(self, tmp_path: Path) -> None:
        """正文变化不改目录摘要（否则每次编辑技能都会重新注入）。"""
        package = _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
            body="# 一\n",
        )
        before = load_skill_catalog(_pool(tmp_path)).digest

        (package / "skills" / "SKILL.md").write_text("# 二\n", encoding="utf-8", newline="\n")

        assert load_skill_catalog(_pool(tmp_path)).digest == before

    def test_description_change_moves_digest(self, tmp_path: Path) -> None:
        """清单文案变化才改摘要。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )
        before = load_skill_catalog(_pool(tmp_path)).digest

        _write(
            tmp_path,
            "research",
            _definition(
                "research",
                skills={"research": _skill("research", description="换了说明。")},
            ),
        )

        assert load_skill_catalog(_pool(tmp_path)).digest != before

    def test_empty_catalog_digest_is_stable(self) -> None:
        """空白名单也有确定摘要，可用来判断"从有到无"。"""
        catalog = load_skill_catalog(())

        assert catalog.digest == digest_skill_catalog(())
        assert catalog.names == ()


class TestSkillRender:
    def test_first_render_uses_intro_header(self, tmp_path: Path) -> None:
        """首次注入用介绍口径。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )

        text = load_skill_catalog(_pool(tmp_path)).render()

        assert text.startswith("<system-reminder>\n")
        assert "The following skills are available" in text
        assert "- `research`: research 技能说明。" in text
        assert "skill bodies are not included" in text
        assert text.endswith("</system-reminder>")

    def test_changed_render_replaces_earlier_lists(self, tmp_path: Path) -> None:
        """清单变化时用"完整替换"口径，覆盖历史清单。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )

        text = load_skill_catalog(_pool(tmp_path)).render(changed=True)

        assert "This complete catalog replaces" in text

    def test_empty_render_warns_old_names_invalid(self) -> None:
        """空清单：告知模型旧名字全部失效（不静默消失）。"""
        text = render_skill_catalog((), changed=False)

        assert "No skills are currently available" in text
        assert "This complete catalog replaces" in text
        assert "<available_skills>\n\n</available_skills>" in text

    def test_description_is_escaped_and_single_line(self, tmp_path: Path) -> None:
        """多行 / 尖括号的描述被折叠转义，不破坏 reminder 边界。"""
        _write(
            tmp_path,
            "research",
            _definition(
                "research",
                skills={"research": _skill("research", description="第一行\n第二行 <b>x</b>")},
            ),
        )

        text = load_skill_catalog(_pool(tmp_path)).render()

        assert "- `research`: 第一行 第二行 &lt;b&gt;x&lt;/b&gt;" in text
        assert "<b>x</b>" not in text

    def test_foreign_summary_shape_is_accepted(self) -> None:
        """只要求 catalog_name + description，因此 SkillSummary 可直接复用渲染。"""

        class _Foreign:
            catalog_name = "research"
            description = "外部包的说明。"

        text = render_skill_catalog((_Foreign(),), changed=False)

        assert "- `research`: 外部包的说明。" in text


class TestSkillDescription:
    def test_short_text_is_unchanged(self) -> None:
        """未超长时不加省略号。"""
        assert catalog_description("短描述") == "短描述"

    def test_long_text_is_truncated_to_limit(self) -> None:
        """超长截断到上限，含 ``...`` 共 max_length 个字符。"""
        text = "长" * (CATALOG_DESCRIPTION_MAX_CHARS + 50)

        rendered = catalog_description(text)

        assert len(rendered) == CATALOG_DESCRIPTION_MAX_CHARS
        assert rendered.endswith("...")

    def test_whitespace_is_collapsed(self) -> None:
        """换行与连续空白折叠成单个空格。"""
        assert catalog_description("a\n\n  b\t c") == "a b c"

    def test_custom_limit_is_respected(self) -> None:
        """可显式指定更短的上限。"""
        assert catalog_description("abcdefgh", max_length=5) == "ab..."


class TestSkillRead:
    def test_read_returns_verbatim_body(self, tmp_path: Path) -> None:
        """正文逐字返回，附带定义面归属与来源根。"""
        body = "# 研究\n\n- 步骤一\n- 步骤二\n"
        _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
            body=body,
        )
        catalog = load_skill_catalog(_pool(tmp_path))

        document = catalog.read("research")

        assert document.content == body
        assert document.name == "research"
        assert document.key == "research"
        assert document.container == "research"
        assert document.media_type == "text/markdown"
        assert document.source == "path"
        assert document.full_name == "research"

    def test_read_unknown_name_raises(self, tmp_path: Path) -> None:
        """未知名字直接报错——不按名字猜路径。"""
        _write(
            tmp_path, "research", _definition("research", skills={"research": _skill("research")})
        )
        catalog = load_skill_catalog(_pool(tmp_path))

        with pytest.raises(SkillContextError) as excinfo:
            catalog.read("nope")

        assert "未知的技能成员" in str(excinfo.value)

    def test_read_missing_body_raises(self, tmp_path: Path) -> None:
        """正文被删：读时才发现，抛 SkillContextError。"""
        package = _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
        )
        catalog = load_skill_catalog(_pool(tmp_path))
        (package / "skills" / "SKILL.md").unlink()

        with pytest.raises(SkillContextError) as excinfo:
            catalog.read("research")

        assert "读取技能文档失败" in str(excinfo.value)

    def test_non_utf8_body_raises(self, tmp_path: Path) -> None:
        """非 UTF-8 正文：抛 SkillContextError，不返回乱码。"""
        package = _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
            body=None,
        )
        skill_dir = package / "skills"
        skill_dir.mkdir(exist_ok=True)
        (skill_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00bad")
        catalog = load_skill_catalog(_pool(tmp_path))

        with pytest.raises(SkillContextError):
            catalog.read("research")

    def test_missing_body_is_reported_and_member_excluded(self, tmp_path: Path) -> None:
        """正文缺失：记一条问题，成员不进清单（读不出来的入口不该给模型）。"""
        _write(
            tmp_path,
            "research",
            _definition("research", skills={"research": _skill("research")}),
            body=None,
        )

        catalog = load_skill_catalog(_pool(tmp_path))

        assert catalog.names == ()
        assert len(catalog.issues) == 1
        assert catalog.issues[0].container == "research"
        assert "正文不存在" in catalog.issues[0].reason
        with pytest.raises(SkillContextError):
            catalog.read("research")

    def test_read_skill_text_wraps_oserror(self, tmp_path: Path) -> None:
        """缺文件统一包成 SkillContextError。"""
        with pytest.raises(SkillContextError):
            read_skill_text(tmp_path / "absent.md")


if __name__ == "__main__":  # pragma: no cover - 便于单文件运行
    pytest.main([__file__])
