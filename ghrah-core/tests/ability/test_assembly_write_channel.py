# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""装配期授权写通道：只写事实、不投影，来源记为 ``assembly``。

覆盖三件事：``GrantSource`` 接受装配来源、写入落库后审计可见、以及这对能力在
``CORE_INTERFACES`` 里与带投影的 ``abilities.grant``/``revoke`` 是不同的声明。
"""

from pathlib import Path

import pytest
from ghrah.protocol.ability_control import AbilityGrantWriteView
from ghrah.protocol.ability_tree import GrantAbilityRequest

from ghrah.ability.access import AbilityAccessStore, AccessIdentity
from ghrah.ability.access.startup import register_package_instance
from ghrah.ability.loading.ability_catalog import load_ability_definitions
from ghrah.core.api.interfaces import (
    CORE_INTERFACES,
    AbilitiesWriteGrantRequest,
    AbilitiesWriteRevokeRequest,
)
from ghrah.core.read.ability import AbilityReadStore

PROJECT = "project"
AGENT = "a" * 32
ASSEMBLY = AccessIdentity(PROJECT, "deployment", "control", True, "assembly")


def test_grant_source_accepts_assembly_and_rejects_unknown() -> None:
    assert ASSEMBLY.source == "assembly"
    with pytest.raises(ValueError):
        AccessIdentity(PROJECT, "deployment", "control", True, "not-a-source")


def test_write_view_carries_no_projection() -> None:
    """写事实的回执只有修订号；投影视图需要受信 run，不能在这里伪造。"""
    assert "view" not in AbilityGrantWriteView.model_fields
    assert set(AbilityGrantWriteView.model_fields) == {"format", "agent_id", "revision"}


def test_read_adapter_marks_assembly_source() -> None:
    store = AbilityReadStore(db_path=Path("unused.db"), scope_id=PROJECT)
    operator = store._assembly_operator()
    assert operator.source == "assembly"
    assert operator.operator is True
    assert operator.project_id == PROJECT


def test_write_capabilities_are_registered_with_the_write_view() -> None:
    grant = CORE_INTERFACES.get("abilities.write_grant")
    revoke = CORE_INTERFACES.get("abilities.write_revoke")
    assert grant.request is AbilitiesWriteGrantRequest
    assert revoke.request is AbilitiesWriteRevokeRequest
    assert grant.response is AbilityGrantWriteView
    assert revoke.response is AbilityGrantWriteView


def test_write_capabilities_stay_distinct_from_projected_ones() -> None:
    """写事实与在线生效是两条路：不能复用同一个能力名。"""
    assert CORE_INTERFACES.get("abilities.grant").response is not AbilityGrantWriteView
    assert CORE_INTERFACES.get("abilities.revoke").response is not AbilityGrantWriteView


async def test_write_grant_persists_assembly_source(tmp_path: Path) -> None:
    """装配期写入在 Agent 尚未注册时落库，审计来源为 ``assembly``。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = await AbilityAccessStore.open(
        tmp_path / "ability.db",
        PROJECT,
        operators=frozenset({"deployment"}),
        runs=lambda agent: f"{agent}-run",
    )
    try:
        await store.ensure_agent(ASSEMBLY, AGENT)
        registered = await register_package_instance(
            store=store,
            actor=ASSEMBLY,
            catalog=load_ability_definitions(),
            package="ghrah.fs",
            members={"read_file"},
            workspace_root=str(workspace),
        )
        revision = await store.agent_revision(ASSEMBLY, AGENT)
        written = await store.write_grant(
            ASSEMBLY,
            AGENT,
            GrantAbilityRequest(
                alias=registered.alias,
                instance_id=registered.instance.instance_id,
                members=["read_file"],
                allowed_roots=[str(workspace)],
                expected_revision=revision,
            ),
        )
        assert written >= revision
        grants = await store.grants(ASSEMBLY, AGENT)
        assert [item.source for item in grants] == ["assembly"]
    finally:
        await store.close()
