# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""LoadSkillAbility：按 full_name 加载某一个 skill 的 SKILL.md 原文（skill-ability）。

技能目录摘要（每轮注入上下文）只承载「名字 + 一句话描述」，**正文不在注入物里**
——需要正文时用本能力按需拉取：入参就是给模型的 ``full_name``（与目录摘要里的
名字逐字一致），返回 SKILL.md 原文与它在磁盘上的路径。

定位口径完全走 ``SkillAbilityProvider``（同一份清单索引）：不自己拼路径、不自己
解析 frontmatter、不缓存——因此目录热插拔后立刻生效，且不会绕过扫描校验去读一个
"不在清单里"的文件。

接线语义（零隐式）：provider 经装配期注入
（``AbilityExecutionContext.skill_provider``）；未接线时明确 FAILURE 说明缺席，
**不**静默当成"技能不存在"（那是两件不同的事）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.skills import SkillNotFoundError, SkillParseError
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.context import AbilityExecutionContext
    from ghrah.abilities.hooks import Hook

__all__ = ["LoadSkillAbility", "LoadSkillInput"]

logger = logging.getLogger(__name__)


class LoadSkillInput(BaseModel):
    """load_skill 工具入参契约（extra=forbid）。"""

    model_config = {"extra": "forbid"}

    full_name: str = Field(
        description=(
            "The skill's full name exactly as shown in the available-skills "
            "catalog (e.g. 'research'). Use list_abilities to see the names."
        ),
    )


class LoadSkillAbility(Ability):
    """加载单个 skill 的 SKILL.md 原文（只读）。

    输出经 ``data`` 返回：

    - SUCCESS：``{"full_name", "skill_dir", "path", "content", "note"}``
      ——``content`` 是 SKILL.md 完整原文（含 frontmatter 块），逐字返回；
    - FAILURE：``{"error"}``——入参缺失 / provider 未接线 / 技能不存在 /
      文档读不出来，四种情形文案各自区分。
    """

    @property
    def name(self) -> str:
        return "load_skill"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": (
                    "Load the full SKILL.md document of one skill from the "
                    "available-skills catalog, by its exact full name. Returns "
                    "the document text and its path. Read one when a task "
                    "matches its description."
                ),
                "parameters": LoadSkillInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "load_skill(full_name: str) -> dict: load one skill's SKILL.md "
            "content and path by its exact full name from the "
            "available-skills catalog"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, context: AbilityExecutionContext) -> ActionResult:
        tool_args = context.tool_args or {}
        full_name = str(tool_args.get("full_name", "") or "").strip()
        if not full_name:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "full_name is required"},
            )

        provider = context.skill_provider
        if provider is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "SkillAbilityProvider not wired — skill loading is "
                        "unavailable in this deployment (assembly-time "
                        "injection, not implicit)"
                    )
                },
            )

        try:
            document = provider.read_skill(full_name)
        except SkillNotFoundError:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": f"Skill not found: '{full_name}'",
                    "note": ("Use list_abilities to see the available skill full names."),
                },
            )
        except SkillParseError as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to read skill document: {exc}"},
            )
        except Exception as exc:
            logger.warning("skill document read failed for %r", full_name, exc_info=True)
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to read skill document: {exc}"},
            )

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "full_name": document.full_name,
                "skill_dir": str(document.skill_dir),
                "path": str(document.path),
                "content": document.content,
                "note": "skill-ability document — instructions to apply",
            },
        )
