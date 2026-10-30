# SPDX-License-Identifier: Apache-2.0
"""Ability 定义与来源的公开入口；实现对象由 scheme_ability 唯一提供。

Operation 是现有单动作执行接口的公开名称。扫描、读取或实例化不授予 grant；
运行时访问由独立 ghrah.scheme 与 Core 的受信接入层核定。
"""

from ghrah.ability.source import BuiltinAbilitySource
from ghrah.scheme_ability.execution.base import Ability as Operation
from ghrah.scheme_ability.loading.ability_roots import AbilityRoot, default_pool
from ghrah.scheme_ability.loading.ability_scan import (
    AbilityAsset,
    AbilityContainer,
    AbilityOperation,
    AbilityScanResult,
    AbilitySkill,
    scan_abilities,
)
from ghrah.scheme_ability.loading.ability_schema import (
    AbilityDefinition,
    AbilityIdentity,
    ExecutionSpec,
    OperationDefinition,
    ProviderRequirement,
    SkillDefinition,
)
from ghrah.scheme_ability.loading.catalog import CatalogReport, scan_catalog
from ghrah.scheme_ability.loading.skill_provider import SkillAbilityProvider

__all__ = [
    "AbilityAsset",
    "AbilityContainer",
    "AbilityDefinition",
    "AbilityIdentity",
    "AbilityOperation",
    "AbilityRoot",
    "AbilityScanResult",
    "AbilitySkill",
    "BuiltinAbilitySource",
    "CatalogReport",
    "ExecutionSpec",
    "Operation",
    "OperationDefinition",
    "ProviderRequirement",
    "SkillAbilityProvider",
    "SkillDefinition",
    "default_pool",
    "scan_abilities",
    "scan_catalog",
]
