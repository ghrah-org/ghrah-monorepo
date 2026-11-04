# SPDX-License-Identifier: Apache-2.0
"""授权事实形状：脚本作用域、可见/可用两轴、来源与历史、关系库升级。"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from ghrah.protocol.ability_tree import (
    GrantAbilityRequest,
    MemberAxes,
    RevokeAbilityRequest,
    SkillReadScope,
)

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.access.policy import GrantPolicy, GrantRule
from ghrah.ability.content import ContentReadRequest
from ghrah.ability.versions import AbilityVersion, capture_ability_version

ADMIN = AccessIdentity("project", "admin", "control", True)
DEPLOY = AccessIdentity("project", "deploy", "control", True, "deployment")
ALICE = AccessIdentity("project", "alice", "alice-run")

PACKAGE_YAML = """\
format: ghrah.ability-definition
schema_version: '0.2'
identity:
  namespace: demo
  version: '1'
title: Demo
description: 授权事实测试包。
provider_requirements:
  fs_backend:
    contract: ghrah.fs.v1
    contract_digest: null
    required_capabilities:
    - request
operations:
  read_file:
    title: Read File
    description: 读取文件。
    input_schema:
      type: object
      properties:
        file_path:
          type: string
      required:
      - file_path
      additionalProperties: false
    output_schema:
      type: object
      additionalProperties: true
    implementation:
      provider_slot: fs_backend
      operation: read_file
    execution:
      mode: request
      timeout_ms: 1000
      cancellation: unsupported
      on_revoke: drain
      retry: never
skills:
  guide:
    body:
      path: SKILL.md
      media_type: text/markdown
    references:
      style:
        path: references/style.md
        media_type: text/markdown
    scripts:
      run:
        path: scripts/run.py
        media_type: text/x-python
"""

SKILL_MD = """\
---
name: guide
description: 写作指南。
---

# 写作指南
"""


def write_package(root: Path) -> AbilityVersion:
    """落一个含 1 个 Operation 与 1 个带脚本 skill 的包并捕获版本。

    Args:
        root: 包根目录。

    Returns:
        捕获后的确定版本。
    """
    (root / "references").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "ability.yaml").write_text(PACKAGE_YAML, encoding="utf-8", newline="\n")
    (root / "SKILL.md").write_text(SKILL_MD, encoding="utf-8", newline="\n")
    (root / "references" / "style.md").write_text("# 风格\n", encoding="utf-8", newline="\n")
    (root / "scripts" / "run.py").write_text("print('run')\n", encoding="utf-8", newline="\n")
    return capture_ability_version((root / "ability.yaml").read_text(encoding="utf-8"), root=root)


@pytest.fixture
async def store(tmp_path):
    """提交版本、选定实例并登记 Agent；返回可写关系库。"""
    root = tmp_path / "demo"
    version = write_package(root)
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin", "deploy"}),
        runs={"alice": "alice-run"}.get,
    )
    try:
        await db.commit_version(ADMIN, version)
        await db.select_instance(ADMIN, "demo", version.digest, tmp_path)
        await db.register_agent(ADMIN, "alice")
        yield db, version, root
    finally:
        await db.close()


async def _grant(store, request: GrantAbilityRequest):
    """以管理员身份提交一次获授变更。"""
    return await store.grant(ADMIN, "alice", request)


def _whole_container(root: Path, revision: int = 0, **kwargs) -> GrantAbilityRequest:
    """整包获授请求；kwargs 用于覆盖字段。"""
    return GrantAbilityRequest(
        alias="demos",
        instance_id="demo",
        whole_container=True,
        allowed_roots=[str(root)],
        expected_revision=revision,
        **kwargs,
    )


async def test_whole_container_materializes_script_visibility_without_execution(store):
    """整包展开包含脚本可见性，但脚本执行仍需显式轴。"""
    db, _, root = store
    view = await _grant(db, _whole_container(root))
    node = view.abilities[0]
    assert [item.key for item in node.skill.scripts] == ["run"]
    assert node.skill.scripts[0].available is False
    assert node.skill.body_allowed
    assert node.skill.references[0].member == "skill/guide/references/style"
    row = await db._row("ability_grants", "agent=? AND alias=?", "alice", "demos")
    axes = json.loads(row["axes"])
    assert axes["operations/read_file"] == {"visible": True, "available": True}
    assert axes["skill/guide/scripts/run"] == {"visible": True, "available": False}
    assert row["source"] == "admin"


async def test_explicit_script_axis_grants_execution_and_content_is_readable(store):
    """脚本可见即正文可读；显式 available 才获得执行权。"""
    db, _, _ = store
    view = await _grant(
        db,
        GrantAbilityRequest(
            alias="demos",
            instance_id="demo",
            skill=SkillReadScope(key="guide"),
            axes=[MemberAxes(member="skill/guide/scripts/run", available=True)],
            expected_revision=0,
        ),
    )
    assert view.abilities[0].skill.scripts[0].available is True
    fence = await db.resolve(ALICE, "demos", "skill/guide/scripts/run")
    result = await db.read_content(ALICE, fence, ContentReadRequest())
    assert result.content == "print('run')\n"


async def test_operation_axes_hide_or_disable_and_deny_execution(store):
    """不可见 Operation 不进入投影；不可用 Operation 明确不可执行。"""
    db, _, root = store
    hidden = await _grant(
        db,
        GrantAbilityRequest(
            alias="demos",
            instance_id="demo",
            members=["read_file"],
            allowed_roots=[str(root)],
            axes=[MemberAxes(member="operations/read_file", visible=False, available=False)],
            expected_revision=0,
        ),
    )
    assert not hidden.abilities[0].operations
    with pytest.raises(AccessError, match="access_denied"):
        await db.resolve(ALICE, "demos", "read_file")

    disabled = await db.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="demos",
            instance_id="demo",
            members=["read_file"],
            allowed_roots=[str(root)],
            axes=[MemberAxes(member="operations/read_file", available=False)],
            expected_revision=hidden.revision,
        ),
    )
    assert disabled.abilities[0].operations[0].availability == "unavailable"
    with pytest.raises(AccessError, match="unavailable"):
        await db.resolve(ALICE, "demos", "read_file")


@pytest.mark.parametrize(
    "axes",
    [
        [MemberAxes(member="operations/missing")],
        [MemberAxes(member="skill/guide/assets/unknown")],
        [MemberAxes(member="skill/guide/references/style", available=False)],
        [MemberAxes(member="operations/read_file", visible=False, available=True)],
    ],
)
async def test_axes_cannot_add_or_express_impossible_combinations(store, axes):
    """成员轴只能收紧本次授予，且拒绝不存在的组合。"""
    db, _, root = store
    with pytest.raises(AccessError, match="invalid_change"):
        await _grant(
            db,
            GrantAbilityRequest(
                alias="demos",
                instance_id="demo",
                members=["read_file"],
                allowed_roots=[str(root)],
                axes=axes,
                expected_revision=0,
            ),
        )
    assert await db.view(ADMIN, "alice") == await db.view(ADMIN, "alice")


async def test_axes_do_not_carry_invisible_skill_content(store):
    """skill 没有可见性轴：内容要么授予（可见）要么不授予。"""
    db, _, _ = store
    with pytest.raises(AccessError, match="invalid_change"):
        await _grant(
            db,
            GrantAbilityRequest(
                alias="demos",
                instance_id="demo",
                skill=SkillReadScope(key="guide"),
                axes=[MemberAxes(member="skill/guide", visible=False, available=False)],
                expected_revision=0,
            ),
        )


async def test_idempotent_grant_only_bumps_when_facts_change(store):
    """同一事实重复核定不产生新历史；轴变化才算变化。"""
    db, _, root = store
    first = await _grant(db, _whole_container(root))
    identical = await _grant(db, _whole_container(root, revision=first.revision))
    assert identical.revision == first.revision
    changed = await _grant(
        db,
        _whole_container(
            root,
            revision=first.revision,
            axes=[MemberAxes(member="skill/guide/scripts/run", available=True)],
        ),
    )
    assert changed.revision == first.revision + 1
    changes = await db.changes(ADMIN)
    assert [item["reason"] for item in changes] == ["grant", "grant"]


async def test_source_and_policy_are_recorded_in_facts_and_history(store):
    """来源与策略摘要进入关系行与变更历史，便于回答“谁依据什么写入”。"""
    db, _, root = store
    policy = GrantPolicy(mode="replace", rules=(GrantRule(package="demo"),))
    await db.grant(
        DEPLOY,
        "alice",
        GrantAbilityRequest(
            alias="demos",
            instance_id="demo",
            whole_container=True,
            allowed_roots=[str(root)],
            policy=policy.digest,
            expected_revision=0,
        ),
    )
    row = await db._row("ability_grants", "agent=? AND alias=?", "alice", "demos")
    assert row["source"] == "deployment"
    changes = await db.changes(ADMIN)
    assert changes[-1]["source"] == "deployment"
    assert changes[-1]["policy"] == policy.digest


async def test_revoke_prunes_axes_with_members_and_content(store):
    """撤销成员时同步删除对应轴；脚本执行权随脚本内容一起消失。"""
    db, _, root = store
    granted = await _grant(db, _whole_container(root))
    assert granted.revision == 1
    revoked = await db.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(
            alias="demos",
            members=["operations/read_file", "skill/guide/scripts/run"],
            expected_revision=granted.revision,
        ),
    )
    node = revoked.abilities[0]
    assert not node.operations
    assert node.skill.scripts == []
    assert node.skill.body_allowed is True
    assert node.skill.references[0].member == "skill/guide/references/style"
    row = await db._row("ability_grants", "agent=? AND alias=?", "alice", "demos")
    axes = json.loads(row["axes"])
    assert "operations/read_file" not in axes
    assert "skill/guide/scripts/run" not in axes
    assert axes["skill/guide"] == {"visible": True, "available": True}
    with pytest.raises(AccessError, match="access_denied"):
        await db.resolve(ALICE, "demos", "skill/guide/scripts/run")


def test_unknown_grant_source_is_rejected() -> None:
    """来源是受控枚举，未知值不得写入关系库。"""
    with pytest.raises(ValueError, match="未知的授权写入来源"):
        AccessIdentity("project", "admin", "control", True, "guess")


async def test_v3_relation_database_gains_axis_source_and_history_columns(tmp_path):
    """v3 关系库升级保留既有事实，补齐来源、策略与成员轴列。"""
    path = tmp_path / "legacy.db"
    db = await aiosqlite.connect(path)
    try:
        await db.executescript("""
            CREATE TABLE ability_tree_schema(version INTEGER PRIMARY KEY CHECK(version=3));
            INSERT INTO ability_tree_schema VALUES(3);
            CREATE TABLE ability_agents(project TEXT,id TEXT,revision INTEGER,
                PRIMARY KEY(project,id));
            INSERT INTO ability_agents VALUES('project','alice',1);
            CREATE TABLE ability_grants(project TEXT,agent TEXT,alias TEXT,instance TEXT,
                members TEXT,roots TEXT,revision INTEGER,content TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(project,agent,alias));
            INSERT INTO ability_grants VALUES('project','alice','demos','demo',
                '["read_file"]','[]',1,'[]');
            CREATE TABLE ability_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT,agent TEXT,revision INTEGER,reason TEXT,actor TEXT,
                before_revision INTEGER,version_digest TEXT);
            INSERT INTO ability_changes(project,agent,revision,reason)
                VALUES('project','alice',1,'grant');
        """)
    finally:
        await db.close()

    store = await AbilityAccessStore.open(
        path, "project", operators=frozenset({"admin"}), runs={"alice": "alice-run"}.get
    )
    try:
        grant = await store._row("ability_grants", "agent=? AND alias=?", "alice", "demos")
        assert json.loads(grant["axes"]) == {}
        assert grant["source"] == "admin"
        changes = await store.changes(ADMIN)
        assert changes[0]["source"] == "admin" and changes[0]["policy"] is None
        async with store._db.execute("SELECT version FROM ability_tree_schema") as cursor:
            assert (await cursor.fetchone())[0] == 4
    finally:
        await store.close()
