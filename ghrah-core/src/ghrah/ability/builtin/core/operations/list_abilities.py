# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""从当前 Agent 的同一获授投影发现 Operation 与 Skill，不扫描部署目录。"""

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
TITLE = "List Abilities"
SUMMARY = (
    "List this Agent's granted Operations and Skills from the current authorization view. "
    "Pass an exact full_name for one entry. Skill names match load_skill's alias/skill input; "
    "Skill details include only granted attachment paths."
)
TAGS = ["core", "discovery"]
EFFECTS = []
TIMEOUT_MS = 60_000

__all__ = ["ListAbilitiesAbility"]


class ListAbilitiesInput(BaseModel):
    """发现只接受已获授目录中的精确条目名。"""

    model_config = {"extra": "forbid"}
    full_name: str | None = Field(
        default=None,
        description=(
            "Optional exact entry name returned by this tool; omit to list granted entries."
        ),
    )


Input = ListAbilitiesInput


class GrantedAttachmentOutput(BaseModel):
    """获授 Skill 的一个附件。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Attachment key")
    member: str = Field(description="Granted member address")
    path: str = Field(description="Package-relative path")
    media_type: str = Field(description="Media type")
    size: int = Field(description="Size in bytes")
    digest: str = Field(description="Content digest")
    available: bool = Field(description="Whether the attachment may be executed")


class ListedAbilityOutput(BaseModel):
    """目录模式下的一条 Operation。"""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(description="alias/operations/key")
    kind: str = Field(description="Entry kind")
    description: str = Field(description="Operation description")
    availability: str = Field(description="ready, unavailable, or unknown")


class ListedSkillOutput(BaseModel):
    """目录模式下的一条 Skill。"""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(description="alias/skill")
    kind: str = Field(description="Entry kind")
    description: str = Field(description="Skill description")


class ListAbilitiesOutput(BaseModel):
    """列出获授条目，或在给出 full_name 时返回那一条的详情。"""

    model_config = ConfigDict(extra="forbid")

    abilities: list[ListedAbilityOutput] | None = Field(
        default=None, description="Granted operations, when full_name was omitted"
    )
    skills: list[ListedSkillOutput] | None = Field(
        default=None, description="Granted skills, when full_name was omitted"
    )
    count: int | None = Field(default=None, description="Total granted entries before truncation")
    truncated: bool | None = Field(default=None, description="Whether either list was cut at 100")
    sources: dict[str, bool] | None = Field(
        default=None, description="Where the catalog was read from"
    )
    full_name: str | None = Field(default=None, description="Exact entry, when full_name was given")
    kind: str | None = Field(default=None, description="Entry kind of the exact match")
    description: str | None = Field(default=None, description="Description of the exact match")
    title: str | None = Field(default=None, description="Skill title of the exact match")
    alias: str | None = Field(default=None, description="Package alias of the exact match")
    member: str | None = Field(default=None, description="Member address of the exact match")
    instance_id: str | None = Field(default=None, description="Skill instance id")
    version_digest: str | None = Field(default=None, description="Committed version digest")
    input_schema: dict[str, Any] | None = Field(
        default=None, description="Operation input schema of the exact match"
    )
    output_schema: dict[str, Any] | None = Field(
        default=None, description="Operation output schema of the exact match"
    )
    availability: str | None = Field(default=None, description="Availability of the exact match")
    tool_names: list[str] | None = Field(
        default=None, description="Bound tool names of the exact operation"
    )
    assets: list[GrantedAttachmentOutput] | None = Field(
        default=None, description="Granted skill assets"
    )
    references: list[GrantedAttachmentOutput] | None = Field(
        default=None, description="Granted skill references"
    )
    scripts: list[GrantedAttachmentOutput] | None = Field(
        default=None, description="Granted skill scripts"
    )


Output = ListAbilitiesOutput  # v1 生成器入口别名（生成器只读 module.Output）


class ListAbilitiesAbility(Ability):
    """发现服务只读授权面，不产生授予、工具绑定或内容加载。"""

    @property
    def name(self) -> str:
        return "list_abilities"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": SUMMARY,
                "parameters": ListAbilitiesInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        """返回目录工具的简短调用说明。"""
        return "list_abilities(full_name=None): inspect this Agent's granted Operations and Skills"

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
            request = ListAbilitiesInput.model_validate(call.arguments)
            view = await provider.view()
        except AccessError as exc:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Granted content unavailable: {exc.code}"},
            )
        except ValueError as exc:
            return ActionResult(outcome=ActionOutcome.FAILURE, data={"error": str(exc)})
        abilities = [
            {
                "full_name": f"{node.alias}/operations/{operation.key}",
                "kind": "core-ability",
                "description": operation.description,
                "alias": node.alias,
                "member": operation.key,
                "version_digest": node.version_digest,
                "input_schema": operation.input_schema,
                "output_schema": operation.output_schema,
                "availability": operation.availability,
                "tool_names": [
                    tool.name
                    for tool in view.bound_tools
                    if tool.alias == node.alias and tool.member == operation.key
                ],
            }
            for node in view.abilities
            for operation in node.operations
        ]
        skills = provider.skill_rows(view)
        if request.full_name is not None:
            for row in (*abilities, *skills):
                if row["full_name"] == request.full_name:
                    return ActionResult(outcome=ActionOutcome.SUCCESS, data=row)
            return ActionResult(
                outcome=ActionOutcome.FAILURE, data={"error": "Granted entry not found"}
            )
        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "abilities": [
                    {key: row[key] for key in ("full_name", "kind", "description", "availability")}
                    for row in abilities[:100]
                ],
                "skills": [
                    {key: row[key] for key in ("full_name", "kind", "description")}
                    for row in skills[:100]
                ],
                "count": len(abilities) + len(skills),
                "truncated": len(abilities) > 100 or len(skills) > 100,
                "sources": {"granted": True},
            },
        )
