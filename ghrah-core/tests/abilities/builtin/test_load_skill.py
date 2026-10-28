# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LoadSkillAbility 测试：入参校验、成功读取、四种失败、注册与清单对齐。

钉住：入参是给模型的 ``full_name``（逐字精确查表）、成功返回 SKILL.md 原文与
路径、失败四种情形各自有可区分的文案（缺参 / provider 未接线 / 技能不存在 /
文档读不出来），以及 registry 与 builtin yaml 两侧登记对齐（部署解析路径）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from ghrah.scheme.ability.core.operations.load_skill import LoadSkillAbility, LoadSkillInput
from ghrah.scheme.agent_resolver import ResolvedAbility
from ghrah.scheme.execution.base import ActionOutcome
from ghrah.scheme.execution.context import AbilityExecutionContext
from ghrah.scheme.loading.ability_catalog import (
    builtin_full_names,
    has_handler,
    load_all_builtin_manifests,
)
from ghrah.scheme.loading.ability_schema import SKILL_FILE_NAME
from ghrah.scheme.loading.skill_errors import SkillParseError
from ghrah.scheme.loading.skill_provider import SkillAbilityProvider

BODY_TEXT = "# 步骤\n\n1. 先看 schema。\n"
"""正文内容：新格式的 ``skills/SKILL.md`` 是纯 Markdown，不再含 frontmatter。"""


def _write_skill(root: Path, dir_name: str) -> Path:
    """造一个新格式能力包（``ability.yaml`` + ``skills/SKILL.md``）。

    旧格式（只有带 frontmatter 的 ``SKILL.md``）已不再被扫盘识别。

    Args:
        root: 目录池根。
        dir_name: 包目录名（同时作为容器名与技能成员键）。

    Returns:
        新建的包目录。
    """
    package = root / dir_name
    (package / "skills").mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "ghrah.ability-definition",
        "schema_version": "0.1-draft",
        "identity": {"namespace": dir_name, "version": "0.1.0"},
        "title": dir_name,
        "description": "对关系型数据库执行只读查询。",
        "provider_requirements": {},
        "operations": {},
        "skills": {
            dir_name: {
                "title": dir_name,
                "description": "对关系型数据库执行只读查询。",
                "body": {"path": "skills/SKILL.md", "media_type": "text/markdown"},
            }
        },
        "assets": {},
    }
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (package / "skills" / SKILL_FILE_NAME).write_text(BODY_TEXT, encoding="utf-8")
    return package


def _make_context(
    provider: Any = None,
    tool_args: dict[str, Any] | None = None,
) -> AbilityExecutionContext:
    return AbilityExecutionContext(
        current_ability_name="load_skill",
        tool_args=tool_args or {},
        skill_provider=provider,
    )


class _ParseFailingProvider:
    """read_skill 抛 SkillParseError（模拟扫描后文档变得读不出来）。"""

    def read_skill(self, full_name: str) -> None:
        raise SkillParseError(f"读取技能文档失败: /skills/{full_name}/SKILL.md")


class _BrokenProvider:
    """read_skill 抛非预期异常（必须 fail-soft 成 FAILURE，不能冒泡）。"""

    def read_skill(self, full_name: str) -> None:
        raise RuntimeError("skill root unavailable")


class TestLoadSkillAbilityContract:
    def test_name(self) -> None:
        assert LoadSkillAbility().name == "load_skill"

    def test_tool_schema(self) -> None:
        tool = LoadSkillAbility().bind_tool()

        assert tool["type"] == "function"
        func = tool["function"]
        assert func["name"] == "load_skill"
        assert func["parameters"]["properties"]["full_name"]["type"] == "string"
        assert func["parameters"]["required"] == ["full_name"]

    def test_prompt_description(self) -> None:
        # 签名含两个可选参数（按需取 MCP 参数 schema），与工具 schema 保持一致。
        description = LoadSkillAbility().to_prompt_description()
        assert description.startswith("load_skill(full_name: str, server?: str, tool?: str)")

    def test_no_hooks(self) -> None:
        assert LoadSkillAbility().get_hooks() == []

    def test_input_forbids_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            LoadSkillInput.model_validate({"full_name": "db-query", "extra": 1})


class TestLoadSkillAbilityExecute:
    async def test_load_success(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "db-query")

        result = await LoadSkillAbility().execute(
            _make_context(
                provider=SkillAbilityProvider.from_root(tmp_path),
                tool_args={"full_name": "db-query"},
            )
        )

        assert result.outcome == ActionOutcome.SUCCESS
        assert result.data["full_name"] == "db-query"
        assert result.data["skill_dir"] == str(skill_dir)
        assert result.data["path"] == str(skill_dir / "skills" / SKILL_FILE_NAME)
        assert result.data["content"] == BODY_TEXT
        assert result.data["note"] == "skill-ability document — instructions to apply"

    async def test_missing_full_name(self) -> None:
        result = await LoadSkillAbility().execute(_make_context(tool_args={}))

        assert result.outcome == ActionOutcome.FAILURE
        assert result.data["error"] == "full_name is required"

    async def test_blank_full_name_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")
        result = await LoadSkillAbility().execute(
            _make_context(
                provider=SkillAbilityProvider.from_root(tmp_path),
                tool_args={"full_name": "   "},
            )
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert result.data["error"] == "full_name is required"

    async def test_unknown_skill(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")

        result = await LoadSkillAbility().execute(
            _make_context(
                provider=SkillAbilityProvider.from_root(tmp_path),
                tool_args={"full_name": "no-such-skill"},
            )
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert "Skill not found" in result.data["error"]
        assert "list_abilities" in result.data["note"]

    async def test_unwired_provider_reports_absence(self) -> None:
        """未接线 ≠ 技能不存在：文案必须说清是装配期没注入。"""
        result = await LoadSkillAbility().execute(
            _make_context(tool_args={"full_name": "db-query"})
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert "not wired" in result.data["error"]
        assert "not implicit" in result.data["error"]

    async def test_unreadable_document(self) -> None:
        result = await LoadSkillAbility().execute(
            _make_context(
                provider=_ParseFailingProvider(),
                tool_args={"full_name": "db-query"},
            )
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert "Failed to read skill document" in result.data["error"]

    async def test_unexpected_provider_error_is_fail_soft(self) -> None:
        result = await LoadSkillAbility().execute(
            _make_context(
                provider=_BrokenProvider(),
                tool_args={"full_name": "db-query"},
            )
        )

        assert result.outcome == ActionOutcome.FAILURE
        assert "Failed to read skill document" in result.data["error"]


class TestLoadSkillRegistration:
    def test_registered_in_registry(self) -> None:
        assert has_handler("load_skill")

    def test_builtin_manifest_declared(self) -> None:
        assert "ghrah.core.load_skill" in builtin_full_names()

        manifest = load_all_builtin_manifests()["ghrah.core.load_skill"]
        assert manifest.implementation.handler == "load_skill"
        assert manifest.metadata.permissions.model_dump(exclude_defaults=True) == {}

    def test_manifest_and_tool_schema_aligned(self) -> None:
        manifest = load_all_builtin_manifests()["ghrah.core.load_skill"]
        tool = LoadSkillAbility().bind_tool()

        assert manifest.tool is not None
        assert manifest.tool.name == tool["function"]["name"] == "load_skill"

    def test_resolvable_as_builtin_ability_type(self) -> None:
        """部署解析路径：按 handler 名解析 → 必须命中 builtin manifest。"""
        resolved = ResolvedAbility.from_builtin("load_skill")

        assert resolved.ability_name == "ghrah.core.load_skill"
        assert resolved.tool_schema is not None
        assert resolved.tool_schema.name == "load_skill"
