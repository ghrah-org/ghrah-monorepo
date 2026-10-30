# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Manifest 解析器：将 AgentManifest 解析为可执行的 AgentConfig + Ability 列表。

核心逻辑：
1. AgentManifest.model → ModelOverrides（覆盖 agentconf 解析的 LLM 配置）
2. AgentManifest.abilities → ResolvedAbility 列表（ref 从 store 加载，type 从 builtin 加载）
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
from ghrah.scheme_ability.agent_manifest import AbilityRef, AgentManifest
from ghrah.scheme_ability.loading.ability_catalog import (
    builtin_handlers,
    has_handler,
    load_all_builtin_manifests,
)
from ghrah.scheme_ability.loading.ability_view import AbilityHooks
from ghrah.scheme_ability.loading.manifest_fields import (
    ImplementationDef,
    ManifestNotFoundError,
    ManifestValidationError,
    PermissionFlags,
    ToolSchema,
)
from ghrah.scheme_ability.loading.manifest_io import ManifestStoreProtocol
from ghrah.types.config_types import AgentConfig, ContextConfig, WindowConfig

__all__ = [
    "ManifestResolver",
    "ResolvedAbility",
    "ResolvedAgent",
]


@dataclass
class ResolvedAbility:
    """解析后的能力：包含完整定义。"""

    ability_name: str
    tool_schema: ToolSchema | None
    permissions: PermissionFlags
    implementation: ImplementationDef
    hooks: AbilityHooks

    @classmethod
    def from_builtin(cls, ability_type: str) -> ResolvedAbility:
        """从 builtin 类型名构建 ResolvedAbility。

        校验权威是**定义面**（扫盘）：handler 必须落在某个容器的 ``operations``
        表里，且能投影出一份 view。1. 校验 handler 已声明
        2. 从 builtins 包加载对应的 AbilityManifest
        3. 构建 ResolvedAbility（权限来自 manifest，hooks 来自 manifest）

        Args:
            ability_type: builtin 能力类型名

        Raises:
            ManifestValidationError: handler 未在定义面声明
            ManifestNotFoundError: builtin manifest 不存在
        """
        if not has_handler(ability_type):
            raise ManifestValidationError(
                f"Unknown builtin ability type: '{ability_type}'. "
                f"Available types: {list(builtin_handlers())}"
            )

        builtins = load_all_builtin_manifests()
        manifest = None
        for m in builtins.values():
            if m.implementation.handler == ability_type:
                manifest = m
                break

        if manifest is None:
            raise ManifestNotFoundError(
                f"No builtin manifest found for ability type: '{ability_type}'"
            )

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


class ManifestResolver:
    """Agent Manifest 解析器。

    将 AgentManifest 解析为 ResolvedAgent，包含 AgentConfig 和
    解析后的 ResolvedAbility 列表。
    """

    def __init__(self, ability_store: ManifestStoreProtocol) -> None:
        self._store = ability_store

    def resolve(self, manifest: AgentManifest, runtime_name: str | None = None) -> ResolvedAgent:
        """将 AgentManifest 解析为 ResolvedAgent。

        Args:
            manifest: 已校验的 AgentManifest 对象
            runtime_name: 运行时名称，覆盖 manifest.metadata.name

        Returns:
            包含 AgentConfig 和 ResolvedAbility 列表的 ResolvedAgent

        Raises:
            ManifestNotFoundError: ref 引用的能力不存在
            ManifestValidationError: type 引用的能力未注册
        """
        config = self._build_config(manifest, runtime_name)
        abilities = self._resolve_abilities(manifest.abilities)
        return ResolvedAgent(config=config, abilities=abilities)

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
                ability_manifest = self._store.load_ability(ref.ref)
                permissions = ability_manifest.metadata.permissions.merge(ref.permissions)
                r = ResolvedAbility(
                    ability_name=ability_manifest.full_name,
                    tool_schema=ability_manifest.tool,
                    permissions=permissions,
                    implementation=ability_manifest.implementation,
                    hooks=ability_manifest.hooks,
                )
            elif ref.type:
                r = ResolvedAbility.from_builtin(ref.type)
                if ref.permissions:
                    r = dataclasses.replace(r, permissions=r.permissions.merge(ref.permissions))
            else:
                raise ManifestValidationError("AbilityRef must have either 'ref' or 'type'")
            resolved.append(r)
        return resolved
