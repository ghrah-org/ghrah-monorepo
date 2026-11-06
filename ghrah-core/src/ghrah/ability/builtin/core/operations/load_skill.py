# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""按获授目录的精确名称读取完整 Skill 正文或已登记的相对文本附件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ghrah.ability.access import AccessError
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import ABILITY_CONTENT
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall

PERMISSIONS = {}
TITLE = "Load Skill"
SUMMARY = (
    "Load the complete text of a granted Skill by its exact alias/skill catalog name. "
    "Pass path to read a granted text attachment relative to that Skill. "
    "Content comes from the committed version; returned paths are package-relative."
)
TAGS = ["core", "skill"]
EFFECTS = []
TIMEOUT_MS = 60_000

__all__ = ["LoadSkillAbility", "LoadSkillInput"]


class LoadSkillInput(BaseModel):
    """正文与附件共用获授 Skill 身份；path 不接受宿主文件路径。"""

    model_config = {"extra": "forbid"}

    full_name: str = Field(
        min_length=1,
        description="Exact alias/skill name from the available-skills catalog or list_abilities.",
    )
    path: str | None = Field(
        default=None,
        description=(
            "Optional registered attachment path relative to the Skill document, "
            "for example references/style.md. Omit to load the complete Skill document."
        ),
    )


Input = LoadSkillInput


class LoadSkillOutput(BaseModel):
    """LoadSkill 工具的成功结果契约。"""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(description="alias/skill that was loaded")
    instance_id: str = Field(description="Instance that owns the committed text")
    version_digest: str = Field(description="Committed version digest")
    member: str = Field(description="Member address of the document or attachment")
    path: str = Field(description="Package-relative path")
    media_type: str = Field(description="Media type")
    digest: str = Field(description="Content digest")
    total_bytes: int = Field(description="Total size in bytes")
    alias: str = Field(description="Package alias")
    uri: str = Field(description="ability: alias/member URI")
    content: str = Field(description="UTF-8 text")


Output = LoadSkillOutput  # v1 生成器入口别名（生成器只读 module.Output）


class LoadSkillAbility(Ability):
    """只读消费获授快照；成功结果经原工具结果通路进入 Context。"""

    @property
    def name(self) -> str:
        return "load_skill"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": SUMMARY,
                "parameters": LoadSkillInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        """返回工具的简短调用说明。"""
        return "load_skill(full_name, path=None): read a granted Skill or text attachment"

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        provider = services.get(ABILITY_CONTENT)
        if provider is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "Granted ability content service not wired"},
            )
        try:
            provider.validate_call(call)
            request = LoadSkillInput.model_validate(call.arguments)
            result = await provider.load_skill(request.full_name, path=request.path)
        except AccessError as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Granted content unavailable: {exc.code}"},
            )
        except ValueError as exc:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(exc)})
        return ActionResult(outcome=ActionOutcome.SUCCESS, data=result)
