# SPDX-License-Identifier: Apache-2.0
"""Ability 定义、内容与受控执行的领域入口。

Operation 是现有单动作执行接口的公开名称。扫描、读取或实例化不授予 grant；
运行时访问由独立 ghrah.scheme 与 Core 的受信接入层核定。
"""

from ghrah.ability.assembly import (
    AbilityAssembly,
    AbilityAssemblyError,
    AbilitySourceMode,
)
from ghrah.ability.execution.base import Ability as Operation
from ghrah.ability.loading.ability_catalog import (
    ability_class,
    create_ability,
    load_all_builtin_manifests,
)
from ghrah.ability.loading.ability_roots import AbilityRoot, default_pool
from ghrah.ability.loading.ability_scan import (
    AbilityAsset,
    AbilityContainer,
    AbilityOperation,
    AbilityScanResult,
    AbilitySkill,
    scan_abilities,
)
from ghrah.ability.loading.ability_schema import (
    AbilityDefinition,
    AbilityIdentity,
    ExecutionSpec,
    OperationDefinition,
    ProviderRequirement,
    SkillDefinition,
)
from ghrah.ability.loading.catalog import CatalogReport, scan_catalog
from ghrah.ability.loading.skill_context import SkillCatalog, load_skill_catalog
from ghrah.ability.loading.skill_provider import SkillAbilityProvider
from ghrah.ability.source import BuiltinAbilitySource

__all__ = [
    "AbilityAssembly",
    "AbilityAssemblyError",
    "AbilityAsset",
    "AbilityContainer",
    "AbilityDefinition",
    "AbilityIdentity",
    "AbilityOperation",
    "AbilityRoot",
    "AbilityScanResult",
    "AbilitySkill",
    "AbilitySourceMode",
    "BuiltinAbilitySource",
    "CatalogReport",
    "ExecutionSpec",
    "Operation",
    "OperationDefinition",
    "ProviderRequirement",
    "SkillAbilityProvider",
    "SkillCatalog",
    "SkillDefinition",
    "ability_class",
    "create_ability",
    "default_pool",
    "load_all_builtin_manifests",
    "load_skill_catalog",
    "scan_abilities",
    "scan_catalog",
]
