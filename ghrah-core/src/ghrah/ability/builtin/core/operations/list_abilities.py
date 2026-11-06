# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""从当前 Agent 的同一获授投影发现 Operation 与 Skill，不扫描部署目录。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

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
