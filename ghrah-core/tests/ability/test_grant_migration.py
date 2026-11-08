# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""一次性迁移 bootstrap：已部署 Agent 的初始获授写入与幂等边界。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ghrah.protocol.ability_tree import GrantAbilityRequest, RevokeAbilityRequest

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessIdentity,
    MigrationError,
    MigrationGrant,
    migrate_agent,
    migrate_agents,
)
from ghrah.ability.access.startup import register_package_instance
from ghrah.ability.loading.ability_catalog import load_ability_definitions

PROJECT = "project"
MIGRATE = AccessIdentity(PROJECT, "migration", "migration", True, "migration")
DEPLOY = AccessIdentity(PROJECT, "deployment", "control", True, "deployment")
ADMIN = AccessIdentity(PROJECT, "admin", "admin", True, "admin")


@pytest.fixture
async def store(tmp_path: Path):
    session = await AbilityAccessStore.open(
        tmp_path / "ability.db",
        PROJECT,
        operators=frozenset({"migration", "deployment", "admin"}),
        runs=lambda agent: f"{agent}-run",
    )
    try:
        yield session
    finally:
        await session.close()


def _workspace(tmp_path: Path) -> str:
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return str(root)


async def _grant_as_admin(store: AbilityAccessStore, agent: str, workspace: str) -> None:
    """模拟“已有获授事实但不是迁移写入”的前置状态。"""
    await store.ensure_agent(ADMIN, agent)
    registered = await register_package_instance(
        store=store,
        actor=ADMIN,
        catalog=load_ability_definitions(),
        package="ghrah.fs",
        members={"read_file"},
        workspace_root=workspace,
    )
    revision = await store.agent_revision(ADMIN, agent)
    await store.write_grant(
        ADMIN,
        agent,
        GrantAbilityRequest(
            alias=registered.alias,
            instance_id=registered.instance.instance_id,
            members=["read_file"],
            allowed_roots=[workspace],
            expected_revision=revision,
        ),
    )


async def test_migration_writes_initial_grants_once(
    store: AbilityAccessStore, tmp_path: Path
) -> None:
    workspace = _workspace(tmp_path)

    result = await migrate_agent(
        store,
        "cccccccccccccccccccccccccccccccc",
        grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
        workspace_root=workspace,
    )

    assert result.already_migrated is False
    assert result.aliases == ("fs",)
    records = await store.grants(MIGRATE, "cccccccccccccccccccccccccccccccc")
    assert [record.alias for record in records] == ["fs"]
    assert records[0].members == ("read_file",)
    assert records[0].roots == (workspace,)
    assert records[0].source == "migration"
    assert await store.migrated(MIGRATE, "cccccccccccccccccccccccccccccccc")
    # 迁移是显式快照：没有模板展开，写文件成员没有因此获得
    assert "write_file" not in records[0].members


async def test_migration_is_idempotent(store: AbilityAccessStore, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    grants = [MigrationGrant(package="ghrah.fs", members=("read_file",))]

    first = await migrate_agent(
        store, "cccccccccccccccccccccccccccccccc", grants=grants, workspace_root=workspace
    )
    second = await migrate_agent(
        store, "cccccccccccccccccccccccccccccccc", grants=grants, workspace_root=workspace
    )

    assert first.already_migrated is False
    assert second.already_migrated is True
    assert second.revision == first.revision
    assert second.aliases == ("fs",)
    records = await store.grants(MIGRATE, "cccccccccccccccccccccccccccccccc")
    assert len(records) == 1


async def test_migration_refuses_existing_grants(store: AbilityAccessStore, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    await _grant_as_admin(store, "cccccccccccccccccccccccccccccccc", workspace)

    with pytest.raises(MigrationError, match="已有获授事实"):
        await migrate_agent(
            store,
            "cccccccccccccccccccccccccccccccc",
            grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
            workspace_root=workspace,
        )


async def test_revoke_after_migration_does_not_resurrect(
    store: AbilityAccessStore, tmp_path: Path
) -> None:
    workspace = _workspace(tmp_path)
    grants = [MigrationGrant(package="ghrah.fs", members=("read_file",))]
    await migrate_agent(
        store, "cccccccccccccccccccccccccccccccc", grants=grants, workspace_root=workspace
    )

    revision = await store.agent_revision(ADMIN, "cccccccccccccccccccccccccccccccc")
    await store.revoke(
        ADMIN,
        "cccccccccccccccccccccccccccccccc",
        RevokeAbilityRequest(alias="fs", expected_revision=revision),
    )
    assert await store.grants(MIGRATE, "cccccccccccccccccccccccccccccccc") == []

    # 撤销后迁移标记仍在：下一次启动不会把已撤销的授权补回来
    again = await migrate_agent(
        store, "cccccccccccccccccccccccccccccccc", grants=grants, workspace_root=workspace
    )
    assert again.already_migrated is True
    assert await store.grants(MIGRATE, "cccccccccccccccccccccccccccccccc") == []


async def test_migration_requires_explicit_snapshot(
    store: AbilityAccessStore, tmp_path: Path
) -> None:
    with pytest.raises(MigrationError, match="没有可写入的获授事实"):
        await migrate_agent(
            store,
            "cccccccccccccccccccccccccccccccc",
            grants=[],
            workspace_root=_workspace(tmp_path),
        )


async def test_migration_requires_workspace_for_file_members(
    store: AbilityAccessStore,
) -> None:
    with pytest.raises(MigrationError, match="workspace_root"):
        await migrate_agent(
            store,
            "cccccccccccccccccccccccccccccccc",
            grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
            workspace_root=None,
        )


async def test_migration_handles_multiple_agents(store: AbilityAccessStore, tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    results = await migrate_agents(
        store,
        {
            "cccccccccccccccccccccccccccccccc": [
                MigrationGrant(package="ghrah.fs", members=("read_file",))
            ],
            "dddddddddddddddddddddddddddddddd": [
                MigrationGrant(package="ghrah.fs", members=("list_directory",))
            ],
        },
        workspace_root=workspace,
    )

    assert set(results) == {"cccccccccccccccccccccccccccccccc", "dddddddddddddddddddddddddddddddd"}
    assert all(result.already_migrated is False for result in results.values())
    assert len(await store.grants(MIGRATE, "cccccccccccccccccccccccccccccccc")) == 1
    assert len(await store.grants(MIGRATE, "dddddddddddddddddddddddddddddddd")) == 1


async def test_migration_refuses_display_name_before_writing(store, tmp_path):
    with pytest.raises(ValueError, match="stable Agent ID"):
        await migrate_agent(
            store,
            "coder",
            grants=[MigrationGrant(package="ghrah.fs")],
            workspace_root=_workspace(tmp_path),
        )
    assert await store._row("ability_agents", "id=?", "coder") is None


async def test_migration_does_not_revoke_other_agents_on_instance_change(
    store: AbilityAccessStore, tmp_path: Path
) -> None:
    """迁移是给无事实 Agent 补初始授予的 bootstrap，不替全项目失效旧许可。

    第二个 Agent 的迁移若触发实例根变化，旁观 Agent 的既有授予必须保留；
    撤销它们会让无策略部署失去唯一重建源。
    """
    first = Path(_workspace(tmp_path))
    other = tmp_path / "another"
    other.mkdir()
    agent_a = "a" * 32
    agent_b = "b" * 32

    await migrate_agent(
        store,
        agent_b,
        grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
        workspace_root=str(first),
    )
    changes = await store.changes(MIGRATE)
    before_seq = changes[-1]["seq"] if changes else 0

    await migrate_agent(
        store,
        agent_a,
        grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
        workspace_root=str(other),
    )

    records_b = await store.grants(MIGRATE, agent_b)
    assert [record.alias for record in records_b] == ["fs"]
    assert records_b[0].roots == (str(first),)
    new_changes = await store.changes(MIGRATE, after_seq=before_seq)
    assert not any(
        item["agent"] == agent_b and item["reason"] == "instance_changed" for item in new_changes
    )
