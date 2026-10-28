# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ContextManager 的技能目录摘要注入测试。

钉住六件事：未接线不注入、接线后**每轮**都拼在消息队列末尾、文案随清单变化
切换口径（首次 / 已变化）、同样的清单文案保持稳定、空目录且从未发布不注入、
扫描失败只记警告不阻断本轮问答。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from ghrah.chat.factory import ChatMessageFactory
from ghrah.chat.message import ChatMessage
from ghrah.context.manager import ContextManager
from ghrah.scheme.loading.ability_schema import SKILL_FILE_NAME
from ghrah.scheme.loading.skill_provider import SkillAbilityProvider

SKILL_MD = """---
name: db-query
description: 对关系型数据库执行只读查询。
---

正文。
"""


def _make_cm(**overrides) -> ContextManager:
    defaults = {
        "agent_name": "test-agent",
        "system_prompt": "",
        "message_factory": ChatMessageFactory(),
    }
    defaults.update(overrides)
    return ContextManager(**defaults)


def _write_skill(
    root: Path, dir_name: str, description: str = "对关系型数据库执行只读查询。"
) -> Path:
    """造一个新格式能力包（``ability.yaml`` + ``skills/SKILL.md``）。

    旧的"只有 SKILL.md frontmatter"格式已不再被识别，扫盘只认定义面，因此夹具
    必须写成完整的包——否则这些用例会静默变成"目录里没有技能"。

    Args:
        root: 目录池根。
        dir_name: 包目录名（同时作为容器名与成员键）。
        description: 技能描述。

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
        "description": "测试用能力包。",
        "provider_requirements": {},
        "operations": {},
        "skills": {
            dir_name: {
                "title": dir_name,
                "description": description,
                "body": {"path": "skills/SKILL.md", "media_type": "text/markdown"},
            }
        },
        "assets": {},
    }
    (package / "ability.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (package / "skills" / SKILL_FILE_NAME).write_text("正文。\n", encoding="utf-8")
    return package


class _BrokenProvider:
    """scan 抛错的 provider（注入必须 fail-soft，不能拖垮本轮问答）。"""

    def scan(self) -> None:
        raise RuntimeError("skill root unavailable")


def _commit_history(cm: ContextManager) -> None:
    cm.begin_iteration()
    cm.add_messages([ChatMessage.user(text_or_blocks="你好")])
    cm.commit_iteration(ability_names=["conversation"])


class TestSkillCatalogInjection:
    async def test_no_provider_injects_nothing(self) -> None:
        """未接线 → 消息队列原样（零隐式：不猜目录）。"""
        cm = _make_cm()
        _commit_history(cm)

        messages = await cm.get_llm_messages()

        assert [m.text for m in messages] == ["你好"]

    async def test_catalog_appended_as_last_message(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        _commit_history(cm)

        messages = await cm.get_llm_messages()

        assert [m.text for m in messages[:-1]] == ["你好"]
        catalog = messages[-1]
        assert catalog.role == "user"
        assert getattr(catalog, "source", None) == "skill:catalog"
        assert "`db-query`" in catalog.text
        assert "<available_skills>" in catalog.text

    async def test_catalog_appended_every_turn(self, tmp_path: Path) -> None:
        """注入物不写入消息链 → 每轮都拼接（不能像 DSH 那样"注入过就跳过"）。"""
        _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))

        first = await cm.get_llm_messages()
        second = await cm.get_llm_messages()

        assert len(first) == 1 and len(second) == 1
        assert first[-1].text == second[-1].text  # 清单未变 → 文案稳定（利于前缀缓存）

    async def test_changed_catalog_switches_wording(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))

        first = await cm.get_llm_messages()
        _write_skill(tmp_path, "log-scan")
        second = await cm.get_llm_messages()

        assert "The following skills are available" in first[-1].text
        assert "The available skill catalog changed." in second[-1].text
        assert "`log-scan`" in second[-1].text

    async def test_removed_skill_switches_wording(self, tmp_path: Path) -> None:
        """技能消失（目录热插拔）同样触发"完整替换"口径。"""
        skill_dir = _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        await cm.get_llm_messages()

        shutil.rmtree(skill_dir)
        messages = await cm.get_llm_messages()

        assert "The available skill catalog changed." in messages[-1].text
        assert "`db-query`" not in messages[-1].text

    async def test_empty_catalog_never_published_is_skipped(self, tmp_path: Path) -> None:
        """从未发布过且清单为空 → 无事可报，不注入。"""
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))

        assert await cm.get_llm_messages() == []

    async def test_empty_catalog_after_publish_is_injected(self, tmp_path: Path) -> None:
        """先有技能、后被清空 → 必须告知模型旧名字失效。"""
        skill_dir = _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        await cm.get_llm_messages()

        shutil.rmtree(skill_dir)
        messages = await cm.get_llm_messages()

        assert "No skills are currently available" in messages[-1].text

    async def test_broken_provider_does_not_break_turn(self) -> None:
        cm = _make_cm(skill_provider=_BrokenProvider())
        _commit_history(cm)

        messages = await cm.get_llm_messages()

        assert [m.text for m in messages] == ["你好"]

    async def test_missing_message_factory_skips_injection(self, tmp_path: Path) -> None:
        """接线了 provider 但没有 message_factory → 跳过注入（记警告，不抛错）。"""
        _write_skill(tmp_path, "db-query")
        cm = _make_cm(
            message_factory=None,
            skill_provider=SkillAbilityProvider.from_root(tmp_path),
        )

        assert await cm.get_llm_messages() == []

    async def test_head_change_resets_wording(self, tmp_path: Path) -> None:
        """Head 变更（历史视图重建）→ 文案回到"首次发布"口径。"""
        _write_skill(tmp_path, "db-query")
        cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))
        await cm.get_llm_messages()

        cm._restore_active_context()
        messages = await cm.get_llm_messages()

        assert "The following skills are available" in messages[-1].text

    async def test_catalog_survives_window_compaction(self, tmp_path: Path) -> None:
        """目录摘要在窗口管理之后追加 → 不被压缩裁掉，且始终在末尾。"""
        from ghrah.context.window import WindowManager

        _write_skill(tmp_path, "db-query")
        cm = _make_cm(
            skill_provider=SkillAbilityProvider.from_root(tmp_path),
            window_manager=WindowManager(strategies=[], max_tokens=10),
        )
        for index in range(8):
            cm.begin_iteration()
            cm.add_messages([ChatMessage.user(text_or_blocks=f"消息 {index}")])
            cm.commit_iteration(ability_names=["conversation"])

        messages = await cm.get_llm_messages()

        assert "<available_skills>" in messages[-1].text
        assert getattr(messages[-1], "source", None) == "skill:catalog"


@pytest.mark.parametrize("skill_count", [1, 12])
async def test_catalog_lists_all_skills(tmp_path: Path, skill_count: int) -> None:
    for index in range(skill_count):
        _write_skill(tmp_path, f"skill-{index:02d}", f"第 {index} 个技能")
    cm = _make_cm(skill_provider=SkillAbilityProvider.from_root(tmp_path))

    messages = await cm.get_llm_messages()

    assert all(f"`skill-{index:02d}`" in messages[-1].text for index in range(skill_count))


class _FakeUnitCtx:
    """CoreUnit.init 的最小 ctx（provide/emit 空实现）。"""

    def provide(self, name: str, value: Any) -> None:
        pass

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        pass


class TestSkillProviderWiring:
    """装配期接线：CoreUnitConfig.skill_root → Supervisor → ContextManager。"""

    def test_builder_passes_supervisor_provider(self) -> None:
        from types import SimpleNamespace

        from ghrah.agents.builder import _build_context_manager
        from ghrah.core.config import AgentConfig

        sentinel = object()
        cm = _build_context_manager(
            AgentConfig(name="wiring"), supervisor=SimpleNamespace(skill_provider=sentinel)
        )

        assert cm.skill_provider is sentinel

    def test_builder_without_supervisor_is_unwired(self) -> None:
        from ghrah.agents.builder import _build_context_manager
        from ghrah.core.config import AgentConfig

        cm = _build_context_manager(AgentConfig(name="wiring"))

        assert cm.skill_provider is None

    async def test_core_unit_builds_provider_from_skill_root(self, tmp_path: Path) -> None:
        from ghrah.core.unit import CoreUnitConfig, create_core_unit

        unit = create_core_unit(CoreUnitConfig(project_id="default", skill_root=str(tmp_path)))
        await unit.init(_FakeUnitCtx())

        provider = unit._supervisor.skill_provider
        assert provider is not None
        assert provider.pool[0].path == tmp_path

    async def test_core_unit_without_skill_root_is_unwired(self) -> None:
        """未配置 skill_root → 不接线（零隐式：不回落 home 默认目录）。"""
        from ghrah.core.unit import CoreUnitConfig, create_core_unit

        unit = create_core_unit(CoreUnitConfig(project_id="default"))
        await unit.init(_FakeUnitCtx())

        assert unit._supervisor.skill_provider is None
