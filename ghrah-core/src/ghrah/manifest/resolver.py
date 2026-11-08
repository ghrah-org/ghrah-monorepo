# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Manifest 解析器：将 AgentManifest 解析为可执行的 AgentConfig + Ability 列表。

核心逻辑：
1. AgentManifest.model → ModelOverrides（覆盖 agentconf 解析的 LLM 配置）
2. AgentManifest.abilities → ResolvedAbility 列表（ref 和 type 均由显式能力目录解析）
3. AgentManifest.context → WindowConfig / ContextConfig
4. 权限合并：AbilityManifest.permissions.merge(AbilityRef.permissions) → 最终权限
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ghrah.core.config.builders import (
    build_context_from_overrides,
    build_model_overrides_from_config,
    build_window_from_overrides,
)
from ghrah.manifest.ability import AbilityHooks
from ghrah.manifest.agent import (
    AbilityRef,
    AgentManifest,
)
from ghrah.manifest.errors import ManifestValidationError
from ghrah.manifest.protocols import AbilityCatalogPort
from ghrah.manifest.types import ImplementationDef, PermissionFlags, SurfaceConfig, ToolSchema
from ghrah.types.config_types import AgentConfig, ContextConfig, WindowConfig

__all__ = [
    "ResolvedAbility",
    "ResolvedAgent",
    "ManifestResolver",
]


@dataclass
class ResolvedAbility:
    """解析后的能力：包含完整定义。"""

    ability_name: str
    tool_schema: ToolSchema | None
    permissions: PermissionFlags
    implementation: ImplementationDef
    hooks: AbilityHooks
    requested_permissions: PermissionFlags | None = None
    """Agent 模板对本次实例单独声明的申请值；``None`` 表示未声明。

    ``permissions`` 仍是包声明与模板申请合并后的兼容视图；需要按“声明只能
    收紧”重新合并时，用本字段保留原始模板声明，不必从合并结果反推。
    """

    declared_permissions: PermissionFlags | None = None
    """原始包声明；执行物化与原始模板约束共同计算有效权限。"""

    @classmethod
    def from_builtin(cls, ability_type: str, *, catalog: AbilityCatalogPort) -> ResolvedAbility:
        """从显式 v1 来源读取内置成员。"""
        manifest = catalog.load_builtin(ability_type)

        return cls(
            ability_name=manifest.full_name,
            tool_schema=manifest.tool,
            permissions=manifest.metadata.permissions,
            implementation=manifest.implementation,
            hooks=manifest.hooks,
        )


@dataclass
class ResolvedAgent:
    """解析后的 Agent：可直接用于 spawn。"""

    config: AgentConfig
    abilities: list[ResolvedAbility]
    surface: SurfaceConfig | None = None


class ManifestResolver:
    """用同一个 v1 目录解析 Agent 模板中的 type 和 ref。"""

    def __init__(self, catalog: AbilityCatalogPort) -> None:
        self._catalog = catalog

    def resolve(self, manifest: AgentManifest, runtime_name: str | None = None) -> ResolvedAgent:
        """将 AgentManifest 解析为 ResolvedAgent。

        Args:
            manifest: 已校验的 AgentManifest 对象
            runtime_name: 运行时名称，覆盖 manifest.metadata.name

        Returns:
            包含 AgentConfig 和 ResolvedAbility 列表的 ResolvedAgent

        Raises:
            ManifestNotFoundError: ref 引用的能力不存在
            ManifestValidationError: type 引用的内置成员不存在
        """
        config = self._build_config(manifest, runtime_name)
        abilities = self._resolve_abilities(manifest.abilities)
        return ResolvedAgent(config=config, abilities=abilities, surface=manifest.surface)

    def _build_config(self, manifest: AgentManifest, runtime_name: str | None) -> AgentConfig:
        """从 AgentManifest 构建 AgentConfig。"""
        name = runtime_name or manifest.metadata.name
        endpoint_name = manifest.model.endpoint_name
        system_prompt = manifest.system_prompt
        max_iterations = manifest.max_iterations
        communication_timeout = manifest.communication_timeout

        window: WindowConfig | None = None
        context: ContextConfig | None = None

        if manifest.context:
            if manifest.context.window:
                window = build_window_from_overrides(manifest.context.window)
            if manifest.context.persistence:
                context = build_context_from_overrides(manifest.context.persistence)

        model_overrides = build_model_overrides_from_config(manifest.model)

        return AgentConfig(
            name=name,
            endpoint_name=endpoint_name,
            description=manifest.description,
            system_prompt=system_prompt,
            max_iterations=max_iterations,
            communication_timeout=communication_timeout,
            window=window,
            context=context,
            model_overrides=model_overrides,
        )

    def _resolve_abilities(self, ability_refs: list[AbilityRef]) -> list[ResolvedAbility]:
        """解析 AbilityRef 列表为 ResolvedAbility 列表。

        对于 ref 引用：从 store 加载 AbilityManifest，合并权限。
        对于 type 引用：从 builtin 加载。
        """
        resolved: list[ResolvedAbility] = []
        for ref in ability_refs:
            if ref.ref:
                ability_manifest = self._catalog.load_ability(ref.ref)
                permissions = ability_manifest.metadata.permissions.merge(ref.permissions)
                r = ResolvedAbility(
                    ability_name=ability_manifest.full_name,
                    tool_schema=ability_manifest.tool,
                    permissions=permissions,
                    implementation=ability_manifest.implementation,
                    hooks=ability_manifest.hooks,
                    declared_permissions=ability_manifest.metadata.permissions,
                    requested_permissions=ref.permissions,
                )
            elif ref.type:
                r = ResolvedAbility.from_builtin(ref.type, catalog=self._catalog)
                if ref.permissions:
                    r = dataclasses.replace(
                        r,
                        declared_permissions=r.permissions,
                        permissions=r.permissions.merge(ref.permissions),
                        requested_permissions=ref.permissions,
                    )
            else:
                raise ManifestValidationError("AbilityRef must have either 'ref' or 'type'")
            resolved.append(r)
        return resolved
