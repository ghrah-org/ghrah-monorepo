# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""验证能力管理总览中的部署授权策略摘要。"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from ghrah.protocol.ability_control import AbilityGrantPolicyView

from ghrah.ability.access import AbilityAccessStore, AccessIdentity, GrantPolicy, GrantRule
from ghrah.core.read.ability_admin import build_admin_view


@pytest.fixture
async def access(
    tmp_path: Path,
) -> AsyncIterator[tuple[AbilityAccessStore, AccessIdentity]]:
    store = await AbilityAccessStore.open(
        tmp_path / "ability.db",
        "p1",
        operators=frozenset({"deployment"}),
        runs=lambda agent: None,
    )
    operator = AccessIdentity("p1", "deployment", None, operator=True, source="admin")
    try:
        await store.ensure_agent(operator, "agent-1")
        yield store, operator
    finally:
        await store.close()


async def test_domain_policy_is_summarised(
    access: tuple[AbilityAccessStore, AccessIdentity],
) -> None:
    policy = GrantPolicy(mode="merge", rules=(GrantRule(package="ghrah.fs"),))
    view = await build_admin_view(*access, "agent-1", runtime_tools=None, grant_policy=policy)

    assert isinstance(view.grant_policy, AbilityGrantPolicyView)
    assert view.grant_policy.mode == policy.mode
    assert view.grant_policy.digest == policy.digest
    assert view.grant_policy.rules == len(policy.rules) == 1


async def test_policy_view_passes_through(
    access: tuple[AbilityAccessStore, AccessIdentity],
) -> None:
    policy = AbilityGrantPolicyView(mode="replace", digest="sha256:x", rules=2)
    view = await build_admin_view(*access, "agent-1", runtime_tools=None, grant_policy=policy)

    assert view.grant_policy is policy


async def test_missing_policy_is_none(
    access: tuple[AbilityAccessStore, AccessIdentity],
) -> None:
    view = await build_admin_view(*access, "agent-1", runtime_tools=None, grant_policy=None)

    assert view.grant_policy is None


async def test_unknown_policy_type_is_rejected(
    access: tuple[AbilityAccessStore, AccessIdentity],
) -> None:
    with pytest.raises(TypeError, match="grant_policy"):
        await build_admin_view(
            *access,
            "agent-1",
            runtime_tools=None,
            grant_policy=object(),  # type: ignore[arg-type]
        )
