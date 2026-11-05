# SPDX-License-Identifier: Apache-2.0
"""启动核定：模板与策略折算、幂等写入、CAS 重试与陈旧关系撤销。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from test_grant_facts import write_package

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessIdentity,
    GrantInstance,
    GrantPolicy,
    GrantReconcileError,
    GrantRule,
    apply_grants,
    desired_grants,
)
from ghrah.ability.versions import AbilityVersion

ADMIN = AccessIdentity("project", "admin", "control", True)
DEPLOY = AccessIdentity("project", "deployment", "control", True, "deployment")


def _instance(version: AbilityVersion, root: Path) -> GrantInstance:
    """由真实版本构造一个启动实例。"""
    return GrantInstance(
        alias="demos",
        package="demo",
        instance_id="demo",
        version_digest=version.digest,
        definition=version.definition().model_dump(mode="json"),
        allowed_roots=(str(root),),
    )


@pytest.fixture
async def store(tmp_path):
    """提交版本、选定实例并返回已打开的关系库。"""
    root = tmp_path / "demo"
    version = write_package(root)
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin", "deployment"}),
        runs=lambda agent: "alice-run" if agent == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" else None,
    )
    try:
        await db.commit_version(ADMIN, version)
        await db.select_instance(ADMIN, "demo", version.digest, root)
        yield db, version, root
    finally:
        await db.close()


async def test_template_grants_only_requested_operations(store):
    """模板点名的 Operation 才进入目标事实；未点名的成员不授予。"""
    db, version, root = store
    instances = [_instance(version, root)]
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert desired["demos"].members == frozenset({"read_file"})
    assert desired["demos"].content == frozenset()

    result = await apply_grants(
        db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances
    )
    assert result.granted == ("demos",) and result.revoked == ()
    record = (await db.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))[0]
    assert record.members == ("read_file",)
    assert record.source == "deployment"
    changes = await db.changes(ADMIN)
    assert changes[-1]["reason"] == "grant" and changes[-1]["source"] == "deployment"


async def test_merge_policy_revokes_and_adds_script_visibility(store):
    """merge 在模板结果上先移除后授予；脚本可见与执行分开表达。"""
    db, version, root = store
    instances = [_instance(version, root)]
    policy = GrantPolicy(
        mode="merge",
        rules=(
            GrantRule(package="demo", operation="read_file", effect="revoke"),
            GrantRule(package="demo", skill="guide", script="run", available=True),
        ),
    )
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=policy,
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    facts = desired["demos"]
    assert facts.members == frozenset()
    assert facts.content == frozenset({"skill/guide/scripts/run"})
    assert facts.axes["skill/guide/scripts/run"] == (True, True)

    await apply_grants(
        db,
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        desired,
        instances=instances,
        policy_digest=policy.digest,
    )
    changes = await db.changes(ADMIN)
    assert changes[-1]["policy"] == policy.digest


async def test_replace_mode_ignores_template_and_expands_package(store):
    """replace 只认策略集合；包级规则展开整包，脚本默认不可执行。"""
    db, version, root = store
    instances = [_instance(version, root)]
    policy = GrantPolicy(mode="replace", rules=(GrantRule(package="demo"),))
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=policy,
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    facts = desired["demos"]
    assert facts.members == frozenset({"read_file"})
    assert "skill/guide/references/style" in facts.content
    assert facts.axes["skill/guide/scripts/run"] == (True, False)


async def test_policy_for_unknown_package_fails_closed(store):
    """策略引用本次启动未解析的包时直接报错，不回退到模板。"""
    _, version, root = store
    instances = [_instance(version, root)]
    policy = GrantPolicy(rules=(GrantRule(package="other.package"),))
    with pytest.raises(GrantReconcileError, match="未解析的包"):
        desired_grants(
            instances=instances,
            template={"demos": {"read_file"}},
            policy=policy,
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )


async def test_template_with_unknown_operation_fails_closed(store):
    """模板申请不存在的成员属于装配错误，不能静默丢弃。"""
    _, version, root = store
    instances = [_instance(version, root)]
    with pytest.raises(GrantReconcileError, match="不可暴露"):
        desired_grants(
            instances=instances,
            template={"demos": {"missing"}},
            policy=GrantPolicy(),
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )


async def test_repeated_reconcile_is_idempotent(store):
    """相同目标重复核定不写新历史，也不改变修订。"""
    db, version, root = store
    instances = [_instance(version, root)]
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    first = await apply_grants(
        db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances
    )
    before = await db.changes(ADMIN)
    second = await apply_grants(
        db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances
    )
    assert second.granted == () and second.revoked == ()
    assert second.revision == first.revision
    assert await db.changes(ADMIN) == before


async def test_stale_alias_is_revoked(store):
    """目标集合之外的现有获授关系被撤销，而不是静默保留。"""
    db, version, root = store
    instances = [_instance(version, root)]
    request = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )["demos"].to_request(
        alias="demos",
        instance_id="demo",
        expected_revision=0,
        allowed_roots=(str(root),),
    )
    await db.ensure_agent(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await db.write_grant(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", request)
    stale = {**_instance(version, root).__dict__}
    stale_instance = GrantInstance(
        alias="stale",
        package="demo.stale",
        instance_id="demo",
        version_digest=version.digest,
        definition=stale["definition"],
        allowed_roots=(str(root),),
    )
    stale_facts = desired_grants(
        instances=[stale_instance],
        template={"stale": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )["stale"]
    revision = await db.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await db.write_grant(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        stale_facts.to_request(
            alias="stale",
            instance_id="demo",
            expected_revision=revision,
            allowed_roots=(str(root),),
        ),
    )
    assert {
        record.alias for record in await db.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    } == {"demos", "stale"}

    result = await apply_grants(
        db,
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        desired_grants(
            instances=instances,
            template={"demos": {"read_file"}},
            policy=GrantPolicy(),
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        instances=instances,
    )
    assert result.revoked == ("stale",)
    assert [
        record.alias for record in await db.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ] == ["demos"]


async def test_conflicting_revision_retries_after_reread(store, monkeypatch):
    """CAS 冲突时重读当前事实并重试，不盲目覆盖。"""
    db, version, root = store
    instances = [_instance(version, root)]
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    real_revision = db.agent_revision
    calls = 0

    async def flaky_revision(actor, agent):
        nonlocal calls
        calls += 1
        revision = await real_revision(actor, agent)
        return revision + 1 if calls == 1 else revision

    monkeypatch.setattr(db, "agent_revision", flaky_revision)
    result = await apply_grants(
        db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances
    )
    assert result.granted == ("demos",)
    assert calls >= 2


async def test_operator_may_reconcile_before_agent_run_exists(store):
    """启动核定在运行实例出现前写授权：操作员不依赖 run 身份。"""
    db, version, root = store
    instances = [_instance(version, root)]
    desired = desired_grants(
        instances=instances,
        template={"demos": {"read_file"}},
        policy=GrantPolicy(),
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    await apply_grants(db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances)
    # alice 尚未注册运行实例（runs 回调仍返回 alice-run，但未 register_agent）
    assert [
        record.alias for record in await db.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ] == ["demos"]


async def test_axes_survive_roundtrip_through_store(store):
    """核定写下的轴与读回的事实一致，脚本执行权可显式授予。"""
    db, version, root = store
    instances = [_instance(version, root)]
    policy = GrantPolicy(
        rules=(GrantRule(package="demo", skill="guide", script="run", available=True),)
    )
    desired = desired_grants(
        instances=instances, template={}, policy=policy, agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    await apply_grants(db, DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", desired, instances=instances)
    record = (await db.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))[0]
    assert record.axes == {"skill/guide/scripts/run": {"visible": True, "available": True}}
    assert json.loads(json.dumps(record.axes)) == record.axes


def test_template_and_instances_must_align() -> None:
    """模板 alias 未解析时拒绝；实例集合重复也不能靠猜测收敛。"""
    definition: Mapping[str, Any] = {
        "operations": {"read_file": {"expose": True}},
        "skills": {},
    }
    instance = GrantInstance("demos", "demo", "demo", "sha256:x", dict(definition))
    with pytest.raises(GrantReconcileError, match="未解析的包实例"):
        desired_grants(
            instances=[instance], template={"other": {"read_file"}}, policy=GrantPolicy(), agent="a"
        )
    with pytest.raises(GrantReconcileError, match="别名重复"):
        desired_grants(
            instances=[instance, GrantInstance("demos", "demo2", "demo2", "sha256:y", {})],
            template={},
            policy=GrantPolicy(),
            agent="a",
        )
