# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SpawnAgentAbility：以 manifest 实例化的方式动态创建新的平级 Agent。

能力面治理：LLM 工具面不提供自由 ``abilities`` 参数——新 agent
的能力集由 manifest 完全冻结，不继承也不由 spawner 现场拼装。程序化
路径 ``supervisor.spawn_agent(config, abilities=[...])`` 仍可用
（代码级显式，不经 LLM）。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.abilities.base import Ability
from ghrah.abilities.builtin._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.execution_services import MANIFEST_STORE, SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.abilities.hooks import Hook
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.invocation import OperationCall

logger = logging.getLogger(__name__)


class SpawnAgentInput(BaseModel):
    model_config = {"extra": "forbid"}

    manifest_ref: str = Field(
        min_length=1,
        description="Manifest reference of the agent template to instantiate",
    )
    name: str = Field(
        default="",
        description="Optional runtime name override for the new agent "
        "(defaults to the manifest-defined name)",
    )
    description: str = Field(
        default="",
        description="Optional description override for the new agent",
    )
    system_prompt: str = Field(
        default="",
        description="Optional system prompt override for the new agent",
    )


class SpawnAgentAbility(Ability):
    """以 manifest 实例化方式动态创建新的平级 Agent。

    通过 SupervisorActor.spawn_agent() 在集群中创建一个新 Agent。
    新 Agent 与创建者平级，无父子层级关系。

    主路径：``manifest_ref`` 经 ExecutionServices 显式
    接线的 manifest_store 解析（未接线 → FAILURE 指路，零隐式）；
    新 Agent 的能力面由 manifest 完全冻结。name/description/
    system_prompt 可覆写 manifest 人设（HITL 审批载荷会展示完整
    解析后配置）。
    """

    @property
    def name(self) -> str:
        return "spawn_agent"

    def bind_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "spawn_agent",
                "description": (
                    "Spawn a new peer agent in the cluster by instantiating "
                    "an agent manifest. The new agent is at the same level as "
                    "the spawner — there is no parent-child hierarchy. "
                    "The new agent's abilities are frozen by the manifest "
                    "(use query_manifests to inspect available manifests). "
                    "Optional name/description/system_prompt override the "
                    "manifest persona."
                ),
                "parameters": SpawnAgentInput.model_json_schema(),
            },
        }

    def to_prompt_description(self) -> str:
        return (
            "spawn_agent(manifest_ref: str, name: str = '', "
            "description: str = '', system_prompt: str = '') -> dict: "
            "Spawn a new peer agent in the cluster from an agent manifest"
        )

    def get_hooks(self) -> list[Hook]:
        return []

    async def execute(self, call: OperationCall, services: ExecutionServices) -> ActionResult:
        error = cluster_supervisor_error(services.get(SUPERVISOR))
        if error:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": error},
            )

        tool_args = call.arguments
        manifest_ref = tool_args.get("manifest_ref", "")
        name = tool_args.get("name", "")
        description = tool_args.get("description", "")
        system_prompt = tool_args.get("system_prompt", "")

        if not manifest_ref:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": "manifest_ref is required"},
            )

        store = services.get(MANIFEST_STORE)
        if store is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "manifest_store is not wired into this deployment — "
                        "spawn via manifest_ref requires an explicitly wired "
                        "ManifestStore (assemble-time injection, not implicit)"
                    )
                },
            )

        from ghrah.manifest.materialize import instantiate_resolved_abilities
        from ghrah.manifest.resolver import ManifestResolver

        try:
            manifest = store.load_agent(manifest_ref)
            resolved = ManifestResolver(store).resolve(manifest, runtime_name=name or None)
        except Exception as e:
            logger.warning(
                "SpawnAgentAbility: manifest resolve failed for '%s': %s", manifest_ref, e
            )
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"manifest resolve failed for '{manifest_ref}': {e}"},
            )

        ability_instances = instantiate_resolved_abilities(
            resolved.abilities,
            workspace_root=None,
            auto_approve_abilities=(),
        )
        if not ability_instances:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        f"manifest '{manifest_ref}' resolved 0 abilities — "
                        f"refusing to spawn a zero-ability (zombie) agent"
                    )
                },
            )

        resolved_config = resolved.config
        overrides: dict[str, Any] = {}
        if description:
            overrides["description"] = description
        if system_prompt:
            overrides["system_prompt"] = system_prompt
        if overrides:
            resolved_config = replace(resolved_config, **overrides)

        try:
            agent_name = await services.get(SUPERVISOR).spawn_agent(
                resolved_config,
                abilities=ability_instances,
            )
            return ActionResult(
                outcome=ActionOutcome.SUCCESS,
                data={
                    "agent_name": agent_name,
                    "status": "spawned",
                    "manifest_ref": manifest_ref,
                },
            )
        except Exception as e:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"Failed to spawn agent from '{manifest_ref}': {e}"},
            )
