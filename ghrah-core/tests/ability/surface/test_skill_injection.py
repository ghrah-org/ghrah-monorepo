# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""成员按 type 分流：技能进消息面（目录摘要），工具进工具面。

覆盖三件事：技能成员投影成统一成员记录、目录摘要的注入时机、以及空目录清空
提醒。工具面本身的行为由 ``test_surface_runtime`` 覆盖，这里不重复。
"""

from __future__ import annotations

import pytest
import yaml
from ghrah.protocol.ability_tree import AbilityNode, AbilityTreeView, SkillNode

from ghrah.ability.loading.ability_schema import parse_definition
from ghrah.ability.loading.skill_context import next_skill_reminder, render_skill_catalog
from ghrah.ability.member import Member, MemberError
from ghrah.ability.surface.catalog import (
    SkillSurfaceEntry,
    SurfaceCatalog,
    _skill_entries,
    catalog_from_view,
)
from ghrah.ability.surface.runtime import ToolSurfaceRuntime

DEFINITION = """\
format: ghrah.ability-definition
schema_version: '2'
identity:
  namespace: acme
  name: tools
  version: '1.0.0'
title: Tools
description: 技能注入夹具
members:
- namespace: acme
  name: tools
  member: acme.tools.render
  type: operation
  version: 1.0.0
  ability_type: external
  data:
    title: Render
    description: 渲染
    path: acme/tools/1.0.0/operations/render.py
    input_schema: {type: object, properties: {}, additionalProperties: false}
    output_schema: {type: object, properties: {}, additionalProperties: false}
    execution: {mode: request, timeout_ms: 1000, cancellation: unsupported,
                on_revoke: drain, retry: never}
- namespace: acme
  name: tools
  member: acme.tools.guide
  type: skill
  version: 1.0.0
  ability_type: external
  data:
    title: Guide
    description: 写作指南
    path: acme/tools/1.0.0/skills/guide/SKILL.md
    media_type: text/markdown
    tags: [writing]
    assets: {}
    references: {}
    scripts: {}
"""


def _definition():
    return parse_definition(yaml.safe_dump(yaml.safe_load(DEFINITION)), source="test")


def _node(*, body_allowed: bool = True, key: str = "guide") -> SkillNode:
    return SkillNode(
        key=key,
        title="Guide",
        description="写作指南",
        body_allowed=body_allowed,
        assets=[],
        references=[],
    )


def test_skill_member_is_projected_from_the_definition() -> None:
    entries = _skill_entries(_definition(), "docs", (_node(),))
    assert [entry.catalog_name for entry in entries] == ["docs/guide"]
    assert entries[0].description == "写作指南"
    assert entries[0].member.type == "skill"
    assert entries[0].member.member == "acme.tools.guide"
    assert entries[0].member.package_relative_path() == "skills/guide/SKILL.md"


def test_body_not_allowed_keeps_the_skill_out_of_the_catalog() -> None:
    assert _skill_entries(_definition(), "docs", (_node(body_allowed=False),)) == []


def test_skill_entry_rejects_non_skill_members() -> None:
    member = Member(
        namespace="acme",
        name="tools",
        member="acme.tools.render",
        type="operation",
        ability_type="external",
        version="1.0.0",
    )
    with pytest.raises((MemberError, ValueError), match="skill"):
        SkillSurfaceEntry(member=member, name="docs/render")


SKILL_ONLY_DEFINITION = """\
format: ghrah.ability-definition
schema_version: '2'
identity:
  namespace: acme
  name: guide
  version: '1.0.0'
title: Guide package
description: 纯技能包夹具
members:
- namespace: acme
  name: guide
  member: acme.guide.style
  type: skill
  version: 1.0.0
  ability_type: external
  data:
    title: Style
    description: 写作风格
    path: acme/guide/1.0.0/skills/style/SKILL.md
    media_type: text/markdown
    tags: [writing]
    assets: {}
    references: {}
    scripts: {}
"""


def _pure_skill_catalog() -> SurfaceCatalog:
    """纯技能包没有 operation，目录也必须列出它的技能。"""
    definition = parse_definition(
        yaml.safe_dump(yaml.safe_load(SKILL_ONLY_DEFINITION)), source="test"
    )
    digest = "sha256:" + "0" * 64
    node = AbilityNode(
        alias="guide",
        instance_id="acme-guide",
        instance_revision=1,
        definition_digest=digest,
        version_digest=digest,
        revision=1,
        allowed_roots=[],
        operations=[],
        skills=[
            SkillNode(
                key="style",
                title="Style",
                description="写作风格",
                body_allowed=True,
                assets=[],
                references=[],
                availability="ready",
            )
        ],
        availability="ready",
    )
    view = AbilityTreeView(
        project_id="project",
        agent_id="agent",
        run_id="run",
        revision=1,
        abilities=[node],
        bound_tools=[],
    )
    return catalog_from_view(view, {digest: definition})


def test_pure_skill_package_still_enters_the_catalog() -> None:
    catalog = _pure_skill_catalog()

    assert catalog.tools == ()
    assert [item.catalog_name for item in catalog.skills] == ["guide/style"]
    assert catalog.skills[0].read_path == "acme/guide/1.0.0/skills/style/SKILL.md"


def test_catalog_entry_carries_the_read_address() -> None:
    """目录条目附带正文读取地址（外部件带版本段）。"""
    entries = _skill_entries(_definition(), "docs", (_node(),))
    text = render_skill_catalog(entries, changed=False)
    assert "read: `acme/tools/1.0.0/skills/guide/SKILL.md`" in text


def test_first_injection_uses_the_intro_copy() -> None:
    entries = _skill_entries(_definition(), "docs", (_node(),))
    text, digest = next_skill_reminder(entries, None)
    assert text is not None
    assert "The following skills are available" in text
    assert "`docs/guide`" in text and "写作指南" in text
    # 目录每轮注入：摘要不变时仍是介绍口径（文案稳定，利于前缀缓存）。
    again, same = next_skill_reminder(entries, digest)
    assert again is not None
    assert "The following skills are available" in again
    assert same == digest


def test_changed_catalog_reinjects_with_replacement_copy() -> None:
    entries = _skill_entries(_definition(), "docs", (_node(),))
    _, digest = next_skill_reminder(entries, None)
    renamed = _skill_entries(_definition(), "other", (_node(),))
    text, new_digest = next_skill_reminder(renamed, digest)
    assert text is not None
    assert "This complete catalog replaces" in text
    assert "`other/guide`" in text
    assert new_digest != digest


def test_empty_catalog_is_silent_until_it_becomes_empty() -> None:
    # 一直没技能：不注入。
    assert next_skill_reminder([], None)[0] is None
    # 从有到无：注入一次"已清空"口径的提醒。
    entries = _skill_entries(_definition(), "docs", (_node(),))
    _, digest = next_skill_reminder(entries, None)
    text, empty_digest = next_skill_reminder([], digest)
    assert text is not None
    assert "<available_skills>" in text
    assert "no skills" in text.lower()
    assert next_skill_reminder([], empty_digest)[0] is None


async def test_runtime_injects_every_turn_and_announces_changes() -> None:
    """目录每轮注入；清单变化后改用"完整替换"口径，撤销到空只提醒一次。"""
    entries = _skill_entries(_definition(), "docs", (_node(),))
    catalog = SurfaceCatalog("project", "agent", "run", 1, (), tuple(entries))
    runtime = object.__new__(ToolSurfaceRuntime)
    runtime._skill_digest = None
    runtime._catalog = lambda: _catalog(catalog)  # type: ignore[method-assign]

    first = await runtime.skill_reminder()
    assert first is not None and "`docs/guide`" in first
    assert "The following skills are available" in first
    settled = await runtime.skill_reminder()
    assert settled is not None and "The following skills are available" in settled
    # 撤销后目录清空：给出一次"替换为空"的提醒，之后保持安静。
    empty = SurfaceCatalog("project", "agent", "run", 2, (), ())
    runtime._catalog = lambda: _catalog(empty)  # type: ignore[method-assign]
    assert await runtime.skill_reminder() is not None
    assert await runtime.skill_reminder() is None


async def test_runtime_filters_the_catalog_by_the_bind_axis() -> None:
    """绑定进正式目录；可见未绑定只在提示行里可发现，不展开摘要。"""
    entries = _skill_entries(_definition(), "docs", (_node(),))
    catalog = SurfaceCatalog("project", "agent", "run", 1, (), tuple(entries))
    runtime = object.__new__(ToolSurfaceRuntime)
    runtime._skill_digest = None
    runtime._bound = {"acme.tools.guide"}
    runtime._catalog = lambda: _catalog(catalog)  # type: ignore[method-assign]

    bound_text = await runtime.skill_reminder()
    assert bound_text is not None and "`docs/guide`" in bound_text and "写作指南" in bound_text
    assert "not bound" not in bound_text

    runtime._bound = set()
    runtime._skill_digest = None
    unbound_text = await runtime.skill_reminder()
    assert unbound_text is not None
    assert "not bound" in unbound_text and "`docs/guide`" in unbound_text
    # 未绑定技能不给摘要，避免"看得见却用不了"的错觉。
    assert "写作指南" not in unbound_text
    assert "No skills are currently available" not in unbound_text


async def _catalog(value: SurfaceCatalog) -> SurfaceCatalog:
    return value
