# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SpawnAgentAbility：以 manifest 实例化的方式动态创建新的平级 Agent。

能力面治理：LLM 工具面不提供自由 ``abilities`` 参数——新 Agent
的能力集由 manifest 完全冻结，不继承也不由 spawner 现场拼装。builtin
模式下子 Agent 与顶层 spawn 走同一条授权装配边界（关系库 + 启动策略 +
执行接纳）；``supervisor.spawn_agent(config, abilities=[...])`` 只对
legacy 模式保留，builtin 模式调用方自拼工具表会被拒绝。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ghrah.ability._cluster_common import cluster_supervisor_error
from ghrah.ability.execution.base import Ability
from ghrah.ability.execution.execution_services import MANIFEST_STORE, SUPERVISOR
from ghrah.types.results import ActionOutcome, ActionResult

if TYPE_CHECKING:
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.hooks import Hook
    from ghrah.ability.execution.invocation import OperationCall


# ---------------------------------------------------------------------------
# scheme 运行时声明（随实现搬迁；定义文件 ability.yaml 不承载这些事实）
#   来源：ghrah/abilities/builtin/spawn_agent.py
#   PERMISSIONS <- 旧 manifest 的 metadata.permissions
# 切换时由 scheme 加载器合并回旧 manifest 形态；在此之前这一项不是权威。
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "require_hitl": True,
}

# --- catalogue 声明（旧 manifest 的元信息，草案格式里暂无字段承载）
#   SUMMARY <- metadata.description（目录摘要）
#   TAGS    <- metadata.tags（发现/过滤用）
# 草案补上对应字段后，这两项应迁回 ability.yaml。
SUMMARY = "Spawn a new peer agent in the cluster by instantiating an agent manifest."
TAGS = [
    "cluster",
    "lifecycle",
]
# --- catalogue 声明结束

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


async def _assemble_child_abilities(
    *,
    services: Any,
    manifest_ref: str,
    resolved: Any,
) -> Any:
    """按装配模式产出子 Agent 的工具表。

    builtin 模式必须经授权装配边界（与顶层 spawn 同一入口）：从 Supervisor
    取启动核定器，按子 Agent 模板写关系库并实例化；未接线或核定失败直接
    FAILURE，不回退按模板实例化。legacy 模式保持旧装配路径。

    Args:
        services: 执行服务表（含 supervisor）。
        manifest_ref: 本次 spawn 的 manifest 引用（错误信息用）。
        resolved: 已解析的子 Agent 模板。

    Returns:
        ``(工具表, 启动物化结果或 None, 工具表投影器或 None)``；失败时返回
        ``ActionResult``。
    """
    from ghrah.types.results import ActionOutcome, ActionResult

    supervisor = services.get(SUPERVISOR)
    if getattr(supervisor, "_builtin_abilities", False):
        materializer = getattr(supervisor, "ability_materializer", None)
        if materializer is None:
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={
                    "error": (
                        "builtin 模式缺少授权装配边界（启动授权关系库未接线）；"
                        "拒绝在关系库之外实例化子 Agent 工具"
                    )
                },
            )
        try:
            materialized = await materializer.materialize(
                agent=resolved.config.name, resolved=resolved
            )
        except Exception as error:
            logger.warning(
                "SpawnAgentAbility: authorized assembly failed for '%s': %s",
                manifest_ref,
                error,
            )
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"authorized assembly failed for '{manifest_ref}': {error}"},
            )
        projector = materializer.tool_table_projector(resolved.config.name, materialized)
        return materialized.abilities, materialized, projector
    from ghrah.ability.execution.ability_build import instantiate_resolved_abilities

    abilities = instantiate_resolved_abilities(
        resolved.abilities,
        workspace_root=None,
        auto_approve_abilities=(),
    )
    return abilities, None, None


class SpawnAgentAbility(Ability):
    """以 manifest 实例化方式动态创建新的平级 Agent。

    通过 SupervisorActor.spawn_agent() 在集群中创建一个新 Agent。
    新 Agent 与创建者平级，无父子层级关系。

    主路径：``manifest_ref`` 经 ``MANIFEST_STORE`` 执行服务显式
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
        supervisor = services.get(SUPERVISOR)
        error = cluster_supervisor_error(supervisor)
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

        try:
            manifest = store.load_agent(manifest_ref)
            from ghrah.ability.source import BuiltinAbilitySource

            resolved = (
                BuiltinAbilitySource(agent_store=store)
                .resolver()
                .resolve(manifest, runtime_name=name or None)
            )
        except Exception as e:
            logger.warning(
                "SpawnAgentAbility: manifest resolve failed for '%s': %s", manifest_ref, e
            )
            return ActionResult(
                outcome=ActionOutcome.FAILURE,
                data={"error": f"manifest resolve failed for '{manifest_ref}': {e}"},
            )

        assembled = await _assemble_child_abilities(
            services=services,
            manifest_ref=manifest_ref,
            resolved=resolved,
        )
        if isinstance(assembled, ActionResult):
            return assembled
        ability_instances, materialized, tool_table_projector = assembled
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
            agent_name = await supervisor.spawn_agent(
                resolved_config,
                abilities=ability_instances,
                tool_table_projector=tool_table_projector,
            )
            materializer = getattr(supervisor, "ability_materializer", None)
            if materialized is not None and materializer is not None:
                try:
                    await materializer.bind_run(agent_name, materialized)
                except Exception as error:  # 绑定失败不掩盖“已 spawn 成功”的事实
                    logger.warning(
                        "SpawnAgentAbility: run binding failed for '%s': %s",
                        agent_name,
                        error,
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
