# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""QueryManifestsAbility：只读查询 agent manifest 定义（含能力清单）。

spawn 的配套查询面：LLM 在 spawn_agent 前经此查看可用 manifest 与
各 manifest 冻结的能力清单。manifest_store 经
ExecutionServices 显式接线（装配期注入）——
未接线 → FAILURE 指路（零隐式：绝不猜测 store 位置）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.ability.execution.execution_services import MANIFEST_STORE
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.hooks import Hook
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall

_MAX_ABILITIES_PER_MANIFEST = 50
_MAX_DESCRIPTION_CHARS = 200


class QueryManifestsInput(BaseModel):
    model_config = {"extra": "forbid"}

    manifest_ref: str | None = Field(
        default=None,
        description=(
            "Optional full name of a single agent manifest to inspect "
            "(e.g. 'ghrah.designer'). Omit to list all available manifests."
        ),
    )


class QueryManifestsAbility(Ability):
    """只读查询 agent manifest：列全部 / 读单条（含能力清单）。

    输出经 ``data`` 返回：
    - 无 manifest_ref：``{"manifests": [{"full_name", "description"}], "count"}``
    - 有 manifest_ref：单条 ``{"full_name", "description", "system_prompt",
      "abilities": [...]}``（能力含 ref/type 与权限摘要，截断上限内）
    """

    @property
    def name(self) -> str:
        return "query_manifests"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "query_manifests",
                "description": (
                    "Query available agent manifests (read-only). "
                    "Without manifest_ref, lists all manifests with names "
                    "and descriptions. With manifest_ref, returns the full "
                    "definition including its frozen ability list — use "
                    "this before spawn_agent to pick the right manifest."
                ),
                "parameters": QueryManifestsInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "query_manifests(manifest_ref: str | None = None) -> dict: "
            "Query available agent manifests and their ability lists"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        tool_args = call.arguments
        manifest_ref = tool_args.get("manifest_ref")

        store = services.get(MANIFEST_STORE)
        if store is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "manifest_store is not wired into this deployment — "
                        "query_manifests requires an explicitly wired "
                        "ManifestStore (assemble-time injection, not implicit)"
                    )
                },
            )

        if manifest_ref:
            return self._read_single(store, manifest_ref)
        return self._list_all(store)

    def _read_single(self, store: Any, manifest_ref: str) -> ActionResult:
        try:
            manifest = store.load_agent(manifest_ref)
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Agent manifest not found: '{manifest_ref}' ({e})"},
            )

        abilities: list[dict[str, Any]] = []
        for ref in list(manifest.abilities)[:_MAX_ABILITIES_PER_MANIFEST]:
            ability_entry: dict[str, Any] = ref.ref or ref.type or "unknown"
            item: dict[str, Any] = {"ability": ability_entry}
            if ref.permissions is not None:
                item["require_hitl"] = ref.permissions.require_hitl
                if ref.permissions.allowed_paths:
                    item["allowed_paths"] = ref.permissions.allowed_paths
            abilities.append(item)

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={
                "full_name": manifest.full_name,
                "description": manifest.description[:_MAX_DESCRIPTION_CHARS],
                "system_prompt": manifest.system_prompt,
                "abilities": abilities,
                "truncated": len(manifest.abilities) > _MAX_ABILITIES_PER_MANIFEST,
            },
        )

    def _list_all(self, store: Any) -> ActionResult:
        try:
            names = store.list_agents()
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to list agent manifests: {e}"},
            )

        manifests: list[dict[str, Any]] = []
        for full_name in names:
            entry: dict[str, Any] = {"full_name": full_name}
            try:
                manifest = store.load_agent(full_name)
                entry["description"] = manifest.description[:_MAX_DESCRIPTION_CHARS]
            except Exception:
                # 列表层单个 manifest 损坏不阻断整体（读单条时给出可诊断错误）
                entry["description"] = ""
                entry["unavailable"] = True
            manifests.append(entry)

        return ActionResult(
            outcome=ActionOutcome.SUCCESS,
            data={"manifests": manifests, "count": len(manifests)},
        )
