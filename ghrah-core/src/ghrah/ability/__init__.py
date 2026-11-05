# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Ability 定义、内容与受控执行的领域入口。

Operation 是现有单动作执行接口的公开名称。扫描、读取或实例化不授予 grant；
本地运行时访问由 AbilityAccessStore 与 Core 的受信装配层核定；
Scheme 资源切片是可选接入，不是本地内容读取的前置。
"""

from ghrah.ability.assembly import (
    AbilityAssembly,
    AbilityAssemblyError,
    AbilitySourceMode,
)
from ghrah.ability.execution.base import Ability as Operation
from ghrah.ability.loading.ability_roots import AbilityRoot, default_pool
from ghrah.ability.loading.ability_scan import (
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
    SkillDefinition,
)
from ghrah.ability.loading.catalog import CatalogReport, scan_catalog
from ghrah.ability.loading.skill_provider import SkillAbilityProvider
from ghrah.ability.source import BuiltinAbilitySource

__all__ = [
    "AbilityAssembly",
    "AbilityAssemblyError",
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
    "SkillAbilityProvider",
    "SkillDefinition",
    "default_pool",
    "scan_abilities",
    "scan_catalog",
]
