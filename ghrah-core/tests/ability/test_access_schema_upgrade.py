# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""v5 关系库（``project`` 租户列）到 v6（``scope``）的原地升级。

只验证「标识改名、不丢授权」：老库打开后列名切换为 ``scope``，已有行保留，
版本标记升到 6；其余业务语义由既有 ability 用例覆盖。
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from ghrah.ability.access.store import AbilityAccessStore, AccessIdentity

_V5_SCHEMA = """
CREATE TABLE ability_tree_schema (version INTEGER PRIMARY KEY CHECK(version=5));
INSERT INTO ability_tree_schema VALUES(5);
CREATE TABLE ability_versions (
 project TEXT NOT NULL, digest TEXT NOT NULL, definition TEXT NOT NULL,
 implementation TEXT NOT NULL, manifest TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(project,digest));
CREATE TABLE ability_content (
 project TEXT NOT NULL, digest TEXT NOT NULL, data BLOB NOT NULL,
 PRIMARY KEY(project,digest));
CREATE TABLE ability_instances (
 project TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, root TEXT NOT NULL,
 revision INTEGER NOT NULL, PRIMARY KEY(project,id));
CREATE TABLE ability_agents (
 project TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,id));
CREATE TABLE ability_grants (
 project TEXT NOT NULL, agent TEXT NOT NULL, alias TEXT NOT NULL, instance TEXT NOT NULL,
 members TEXT NOT NULL, roots TEXT NOT NULL, revision INTEGER NOT NULL,
 content TEXT NOT NULL DEFAULT '[]', axes TEXT NOT NULL DEFAULT '{}',
 source TEXT NOT NULL DEFAULT 'admin',
 PRIMARY KEY(project,agent,alias));
CREATE TABLE ability_tools (
 project TEXT NOT NULL, agent TEXT NOT NULL, name TEXT NOT NULL, alias TEXT NOT NULL,
 member TEXT NOT NULL, grant_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(project,agent,name));
CREATE TABLE ability_changes (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, agent TEXT NOT NULL,
 revision INTEGER NOT NULL, reason TEXT NOT NULL, actor TEXT,
 before_revision INTEGER, version_digest TEXT,
 source TEXT NOT NULL DEFAULT 'admin', policy TEXT);
CREATE TABLE ability_registrations (
 project TEXT NOT NULL, agent TEXT NOT NULL, token TEXT NOT NULL, state TEXT NOT NULL,
 owned_revision INTEGER NOT NULL, PRIMARY KEY(project,agent));
CREATE INDEX idx_ability_changes_project_seq ON ability_changes(project,seq);
"""


async def test_v5_to_v6_renames_tenant_column_and_keeps_grants(tmp_path: Path) -> None:
    """老库打开时把 project 列改名 scope，授权行与版本标记同步保留/升级。"""
    db_path = tmp_path / "action_chain.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_V5_SCHEMA)
        await db.execute(
            "INSERT INTO ability_grants VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "scope-a",
                "alice",
                "files",
                "files-alice",
                '["read_file"]',
                '["/tmp"]',
                1,
                "[]",
                "{}",
                "admin",
            ),
        )
        await db.execute("INSERT INTO ability_agents VALUES(?,?,?)", ("scope-a", "alice", 7))
        await db.commit()

    store = await AbilityAccessStore.open(
        db_path,
        "scope-a",
        operators=frozenset({"deployment"}),
        runs=lambda agent: "alice-run",
    )
    try:
        assert store.scope_id == "scope-a"
        grants = await store.grants(
            AccessIdentity("scope-a", "deployment", None, operator=True), "alice"
        )
        assert [grant.alias for grant in grants] == ["files"]
        async with store._db.execute("PRAGMA table_info(ability_grants)") as cursor:
            columns = {row["name"] for row in await cursor.fetchall()}
        assert "scope" in columns and "project" not in columns
        async with store._db.execute("SELECT version FROM ability_tree_schema") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row["version"] == 6
        async with store._db.execute("PRAGMA index_list(ability_changes)") as cursor:
            indexes = {row["name"] for row in await cursor.fetchall()}
        assert "idx_ability_changes_scope_seq" in indexes
    finally:
        await store.close()
