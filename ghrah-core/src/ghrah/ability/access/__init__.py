# SPDX-License-Identifier: Apache-2.0
"""Ability 实例、获授关联、授权策略与工具选择的领域入口。"""

from ghrah.ability.access.facts import GrantFacts
from ghrah.ability.access.policy import GrantEffect, GrantMode, GrantPolicy, GrantRule
from ghrah.ability.access.reconcile import (
    GrantInstance,
    GrantReconcileError,
    ReconciliationResult,
    apply_grants,
    desired_grants,
)
from ghrah.ability.access.startup import (
    ProjectedAbility,
    StartupGrantError,
    StartupGrantMaterializer,
    StartupMaterialization,
    StartupToolBinding,
)
from ghrah.ability.access.store import (
    AbilityAccessStore,
    AccessError,
    AccessIdentity,
    GrantRecord,
    MemberFence,
)
from ghrah.ability.permissions.scope import (
    EffectivePermission,
    command_in_scope,
    resolve_effective_permission,
)

__all__ = [
    "AccessIdentity",
    "EffectivePermission",
    "AbilityAccessStore",
    "AccessError",
    "GrantEffect",
    "GrantFacts",
    "GrantInstance",
    "GrantMode",
    "GrantPolicy",
    "GrantReconcileError",
    "GrantRecord",
    "GrantRule",
    "MemberFence",
    "ProjectedAbility",
    "ReconciliationResult",
    "StartupGrantError",
    "StartupGrantMaterializer",
    "StartupMaterialization",
    "StartupToolBinding",
    "apply_grants",
    "command_in_scope",
    "desired_grants",
    "resolve_effective_permission",
]
