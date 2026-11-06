# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""真实快照、关系库、Scheme 和 Context 的内容消费闭环及失败边界。

容器 fixture 使用 ``examples/ability_content/reporting``：一个 v1 单 Skill 内容包
（``members.operations`` 为空）。外部源包的 Operation 由 P2 的进程内 Provider 提供，
P1 不执行，因此本文件只断言内容/技能/附件/授权轴/快照/升级/损坏检测；真实文件操作
执行由 ``test_resource_tree.py``（内建 ``ghrah.fs`` 文件切片）覆盖。
"""

import asyncio
import base64
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import aiosqlite
import pytest
import yaml
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    SkillReadScope,
)
from ghrah.scheme import Principal, SchemeError

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.consumer import GrantedContentProvider
from ghrah.ability.content import CaptureLimits, ContentReadRequest
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.ability.versions import capture_ability_version
from ghrah.chat.factory import ChatMessageFactory
from ghrah.context.manager import ContextManager
from ghrah.manifest.errors import ManifestValidationError

ADMIN = AccessIdentity("project", "admin", "control", True)
ALICE = AccessIdentity("project", "alice", "alice-run")
EXAMPLE = Path(__file__).resolve().parents[2] / "examples/ability_content/reporting"


async def assert_denied(client, reference):
    try:
        invocation = await client.session.submit(reference, action="read", arguments={})
    except SchemeError:
        return
    try:
        snapshot = await client.session.observe(invocation, wait=1)
        while snapshot.pending:
            snapshot = await client.session.observe(
                invocation, wait=1, after_version=snapshot.version
            )
        assert snapshot.error is not None and snapshot.result is None
    finally:
        await client.session.release(invocation)


@pytest.fixture
async def container(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    runs = {"alice": "alice-run", "bob": "bob-run"}
    store = await AbilityAccessStore.open(
        tmp_path / "project.db", "project", operators=frozenset({"admin"}), runs=runs.get
    )
    host = AbilitySchemeHost(store)
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    try:
        await store.commit_version(ADMIN, version)
        await store.select_instance(ADMIN, "reporting", version.digest, tmp_path)
        for agent in runs:
            await store.register_agent(ADMIN, agent)
            await store.grant(
                ADMIN,
                agent,
                GrantAbilityRequest(
                    alias="reports",
                    instance_id="reporting",
                    whole_container=True,
                    allowed_roots=[str(tmp_path)],
                    expected_revision=0,
                ),
            )
        await host.install("reporting")
        client = host.session(Principal("project", "alice", "alice-run"))
        yield store, host, client, version, source, runs
    finally:
        await host.close()
        await store.close()


async def test_content_tree_bytes_context_and_no_operations(container):
    """内容包可读：正文/附件/二进制都进 Context，且没有 Operation 与工具面。"""
    store, host, client, version, source, _ = container
    view = await client.read("alice")
    node = view.abilities[0]
    assert node.skill.key == "guide"
    assert node.skill.title == "reporting-guide"
    assert {item.key for item in node.skill.assets} == {"template", "mark"}
    assert {item.key for item in node.skill.references} == {"style", "mark"}
    provider = GrantedContentProvider(client, "alice")
    cm = ContextManager(
        agent_name="alice", message_factory=ChatMessageFactory(), ability_content=provider
    )
    messages = await cm.get_llm_messages()
    assert "reports/guide" in messages[-1].text
    assert "使用 assets/template" not in messages[-1].text
    await provider.load_into_context(cm, "reports", "skill/guide")
    attachment = await provider.read_relative("reports", "guide", "references/style.md")
    assert "短段落" in attachment.content
    await provider.load_into_context(cm, "reports", attachment.member)
    messages = await cm.get_llm_messages()
    assert any("# 报告编写" in message.text for message in messages)
    assert any("短段落" in message.text for message in messages)
    for role, expected in (
        ("assets", bytes([0, 255, 16, 128])),
        ("references", bytes([255, 0, 128, 16])),
    ):
        result = await provider.read("reports", f"skill/guide/{role}/mark")
        assert result.encoding == "base64"
        assert base64.b64decode(result.content) == expected
    assert not node.operations
    assert not view.bound_tools
    assert not any(key.startswith("operations/") for key in json.loads(version.manifest_json))
    script = next(item for item in node.skill.scripts if item.key == "render")
    assert script.available is False
    with pytest.raises(AccessError):
        await store.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="reports", member="guide", tool_name="guide", expected_revision=view.revision
            ),
        )


async def test_scope_clipping_revoke_and_old_references(container):
    store, host, client, version, source, runs = container
    old = await client.acquire(
        "/scheme/ability/reports/skill/guide/references/style", actions=frozenset({"read"})
    )
    view = await store.view(ADMIN, "alice")
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="reports",
            instance_id="reporting",
            skill=SkillReadScope(key="guide", assets=["template"], references=[]),
            expected_revision=view.revision,
        ),
    )
    await assert_denied(client, old)
    await client.session.release(old)
    body = await client.call("/scheme/agent/alice/ability/reports/skill/guide/", "read", {})
    assert body["version_digest"] == version.digest
    for member in (
        "skill/guide/references/style",
        "skill/guide/assets/mark",
        "operations/read_file",
    ):
        with pytest.raises(SchemeError):
            await client.acquire(f"/scheme/ability/reports/{member}", actions=frozenset({"read"}))
    snapshot = await client.read("alice")
    assert not snapshot.abilities[0].operations
    assert not snapshot.abilities[0].allowed_roots
    current = snapshot.revision
    await store.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(
            alias="reports", expected_revision=current, members=["skill/guide/assets/template"]
        ),
    )
    assert (await client.read("alice")).abilities[0].skill.body_allowed
    current = (await client.read("alice")).revision
    await store.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(alias="reports", expected_revision=current, members=["skill/guide"]),
    )
    assert not (await client.read("alice")).abilities
    bob = host.session(Principal("project", "bob", "bob-run"))
    assert (await bob.call("/scheme/ability/reports/skill/guide", "read", {}))["content"]


async def test_snapshot_survives_source_deletion_and_host_restore(container):
    store, host, client, version, source, runs = container
    before = await client.call("/scheme/ability/reports/skill/guide", "read", {})
    (source / "skills/guide/SKILL.md").write_text("uncommitted")
    assert await client.call("/scheme/ability/reports/skill/guide", "read", {}) == before
    shutil.rmtree(source)
    other = AbilitySchemeHost(store)
    try:
        await other.restore(ADMIN)
        restored = other.session(Principal("project", "alice", "alice-run"))
        assert await restored.call("/scheme/ability/reports/skill/guide", "read", {}) == before
        ref = await client.acquire(
            "/scheme/ability/reports/skill/guide", actions=frozenset({"read"})
        )
        with pytest.raises(SchemeError):
            await restored.session.submit(ref, action="read", arguments={})
        await client.session.release(ref)
    finally:
        await other.close()


async def test_content_only_package_needs_no_file_root_and_upgrade_is_explicit(container):
    """示例包本身就是 content-only：无需工作区根即可安装；升级不追溯旧引用。"""
    store, host, client, version, source, runs = container
    # 同一份 content-only 版本装到第二个实例：只给内容，不配置任何文件根。
    await store.commit_version(ADMIN, version)
    await store.select_instance(ADMIN, "content-only", version.digest, expected_revision=0)
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="guide", instance_id="content-only", whole_container=True, expected_revision=1
        ),
    )
    await host.install("content-only")
    before = await client.call("/scheme/ability/guide/skill/guide", "read", {})
    data = yaml.safe_load((source / "ability.yaml").read_text())
    payload = b"new template"
    (source / "skills/guide/assets/template.txt").write_bytes(payload)
    entry = data["members"]["skills"]["guide"]["assets"]["template"]
    entry["size"] = len(payload)
    entry["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    updated = capture_ability_version(yaml.safe_dump(data), root=source)
    assert updated.digest != version.digest
    await store.commit_version(ADMIN, updated)
    assert await client.call("/scheme/ability/guide/skill/guide", "read", {}) == before
    old = await client.acquire("/scheme/ability/guide/skill/guide", actions=frozenset({"read"}))
    await store.select_instance(ADMIN, "content-only", updated.digest, expected_revision=1)
    await host.install("content-only")
    await assert_denied(client, old)
    await client.session.release(old)
    assert [item.alias for item in (await client.read("alice")).abilities] == ["reports"]
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="guide", instance_id="content-only", whole_container=True, expected_revision=3
        ),
    )
    assert (await client.call("/scheme/ability/guide/skill/guide/assets/template", "read", {}))[
        "content"
    ] == "new template"


async def test_invalid_whole_grant_is_atomic_and_run_changes_reject_old_reference(container):
    store, host, client, version, source, runs = container
    before = await store.view(ADMIN, "alice")
    with pytest.raises(AccessError):
        await store.grant(
            ADMIN,
            "alice",
            GrantAbilityRequest(
                alias="invalid",
                instance_id="reporting",
                members=["read_file", "missing"],
                skill=SkillReadScope(key="guide"),
                allowed_roots=[str(source.parent)],
                expected_revision=before.revision,
            ),
        )
    assert await store.view(ADMIN, "alice") == before
    ref = await client.acquire("/scheme/ability/reports/skill/guide", actions=frozenset({"read"}))
    runs["alice"] = "alice-next-run"
    await assert_denied(client, ref)
    await client.session.release(ref)


async def test_bounded_reads_revoke_and_sqlite_writer_lock(container, tmp_path):
    store, host, client, version, source, runs = container
    fence = await store.resolve(ALICE, "reports", "skill/guide")
    writer = await aiosqlite.connect(tmp_path / "project.db", isolation_level=None)
    try:
        await writer.execute("BEGIN IMMEDIATE")
        first = await asyncio.wait_for(
            store.read_content(ALICE, fence, ContentReadRequest(limit=7)), 1
        )
        assert first.byte_count == 7 and first.next_offset == 7
    finally:
        await writer.rollback()
        await writer.close()
    view = await store.view(ADMIN, "alice")
    await store.revoke(
        ADMIN, "alice", RevokeAbilityRequest(alias="reports", expected_revision=view.revision)
    )
    with pytest.raises(AccessError):
        await store.read_content(ALICE, fence, ContentReadRequest(offset=7))
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="reports",
            instance_id="reporting",
            skill=SkillReadScope(key="guide"),
            expected_revision=view.revision + 1,
        ),
    )
    with pytest.raises(AccessError):
        await store.read_content(ALICE, fence, ContentReadRequest())


async def test_committed_snapshot_ignores_source_change_and_corruption_fails(container):
    """已提交快照不随来源变化；内容损坏明确失败，不静默回退读旧字节。"""
    store, host, client, version, source, runs = container
    before = await client.call("/scheme/ability/reports/skill/guide/assets/template", "read", {})
    (source / "skills/guide/assets/template.txt").write_text("uncommitted", encoding="utf-8")
    assert (await client.call("/scheme/ability/reports/skill/guide/assets/template", "read", {}))[
        "content"
    ] == before["content"]
    entry = json.loads(version.manifest_json)["skill/guide/assets/template"]
    await store._db.execute(
        "UPDATE ability_content SET data=? WHERE digest=?", (b"corrupt", entry["digest"])
    )
    with pytest.raises(SchemeError):
        await client.call("/scheme/ability/reports/skill/guide/assets/template", "read", {})


@pytest.mark.parametrize(
    "mutation",
    [
        "absolute",
        "parent",
        "symlink",
        "dangling",
        "skill-unknown-field",
        "oversize",
        "bad-digest",
    ],
)
def test_import_rejects_invalid_content(tmp_path, mutation):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    data = yaml.safe_load((source / "ability.yaml").read_text())
    skills = data["members"]["skills"]
    limits = CaptureLimits()
    if mutation in {"absolute", "parent"}:
        skills["guide"]["assets"]["template"]["path"] = (
            "/etc/passwd" if mutation == "absolute" else "../outside"
        )
    elif mutation == "symlink":
        link = source / "skills/guide/assets/template.txt"
        link.unlink()
        try:
            link.symlink_to("/etc/passwd")
        except OSError as exc:  # 无符号链接权限的 Windows 环境跳过该变体
            pytest.skip(f"当前环境不允许创建符号链接: {exc}")
    elif mutation == "dangling":
        body = source / "skills/guide/SKILL.md"
        body.write_text(body.read_text() + "\n[missing](references/missing.md)\n")
    elif mutation == "skill-unknown-field":
        skills["guide"]["mystery"] = True
    elif mutation == "oversize":
        limits = CaptureLimits(file_bytes=1)
    elif mutation == "bad-digest":
        skills["guide"]["assets"]["template"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises((ValueError, OSError, ManifestValidationError)):
        capture_ability_version(yaml.safe_dump(data), root=source, limits=limits)


def test_version_captures_index_metadata_and_unlinked_resources(tmp_path):
    """索引是成员事实的唯一来源；正文未链接的声明文件也进清单，索引外文件不进。"""
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    (source / "skills/guide/assets/orphan.txt").write_text("orphan", encoding="utf-8")
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    manifest = json.loads(version.manifest_json)
    assert version.definition().skills["guide"].title == "reporting-guide"
    # 正文只链接了 references/style.md；template/mark 不是正文链接，但索引声明了
    # 它们，因此都按索引进清单。
    assert {"skill/guide/assets/template", "skill/guide/assets/mark"} <= set(manifest)
    # v1 不再把索引外的文件放进内容清单，但目录里的普通文件仍算进版本身份。
    assert "skill/guide/assets/orphan" not in manifest
    assert version.digest == version.package_digest
    cleared = replace(version, manifest_json="{}")
    assert cleared.expected_digest() == version.digest
    orphan = source / "skills/guide/assets/orphan.txt"
    orphan.unlink()
    without_orphan = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    assert without_orphan.digest != version.digest


async def test_describe_and_partial_content_revoke_keep_remaining_content(container):
    """describe 不回传正文；部分内容 revoke 保留其余可见性，且不产生工具选择。"""
    store, host, client, version, source, runs = container
    metadata = await client.call("/scheme/ability/reports/skill/guide", "describe", {})
    assert metadata["member"] == "skill/guide"
    assert metadata["media_type"] == "text/markdown"
    assert "content" not in metadata
    view = await store.view(ADMIN, "alice")
    with pytest.raises(AccessError):
        # 内容成员不是 Operation，不能进工具面。
        await store.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="reports", member="guide", tool_name="guide", expected_revision=view.revision
            ),
        )
    revoked = await store.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(
            alias="reports",
            members=["skill/guide/assets/template"],
            expected_revision=view.revision,
        ),
    )
    node = revoked.abilities[0]
    assert node.skill is not None and node.skill.body_allowed
    assert {item.key for item in node.skill.assets} == {"mark"}
    assert revoked.bound_tools == []
    projection = await client.project_tools()
    try:
        assert projection.tools == []
    finally:
        await projection.close()


def test_capture_handles_path_replacement_without_following_symlink(tmp_path, monkeypatch):
    from ghrah.ability import content

    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(tmp_path / "probe-target")
    except OSError as exc:  # Windows 未开启开发者模式/无管理员时不允创建符号链接
        pytest.skip(f"当前环境不允许创建符号链接: {exc}")
    probe.unlink()
    original = content.os.open
    swapped = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        # POSIX 传相对文件名，可移植实现传绝对路径：按主文件名匹配，两种口径都覆盖。
        if Path(path).name == "template.txt" and not swapped:
            swapped = True
            (source / "skills/guide/assets/template.txt").unlink()
            (source / "skills/guide/assets/template.txt").symlink_to("/etc/passwd")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(content.os, "open", replace_before_open)
    with pytest.raises(OSError):
        capture_ability_version((source / "ability.yaml").read_text(), root=source)
    assert swapped


def test_standard_capture_checks_frontmatter_name_and_rejects_duplicate_yaml(tmp_path):
    """v1 正文只用于核对 name；重复键 YAML 在解析期就被拒绝。"""
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    assert not version.definition().operations
    assert version.definition().skills["guide"].title == "reporting-guide"
    with pytest.raises(ManifestValidationError, match="重复键"):
        capture_ability_version(
            (source / "ability.yaml").read_text() + "\nmembers: {}\n", root=source
        )
    body = source / "skills/guide/SKILL.md"
    body.write_text(body.read_text().replace("name: guide", "name: other"), encoding="utf-8")
    with pytest.raises(ValueError, match="不一致"):
        capture_ability_version((source / "ability.yaml").read_text(), root=source)


async def test_manifest_corruption_does_not_read_another_blob(container):
    store, host, client, version, source, runs = container
    manifest = json.loads(version.manifest_json)
    manifest["skill/guide/assets/mark"] = manifest["skill/guide/references/mark"]
    await store._db.execute(
        "UPDATE ability_versions SET manifest=? WHERE digest=?",
        (json.dumps(manifest), version.digest),
    )
    with pytest.raises(SchemeError):
        await client.call("/scheme/ability/reports/skill/guide/assets/mark", "read", {})


def test_builtin_source_queries_use_the_prepared_snapshot(monkeypatch):
    from ghrah.ability.loading import ability_catalog
    from ghrah.ability.source import BuiltinAbilitySource

    source = BuiltinAbilitySource()

    def no_scan(*args, **kwargs):
        raise AssertionError("query must not scan sources")

    monkeypatch.setattr(ability_catalog, "load_all_builtin_manifests", no_scan)
    monkeypatch.setattr(ability_catalog, "builtin_handlers", no_scan)
    assert source.list_abilities()
    assert source.load_builtin("read_file").full_name == "ghrah.fs.read_file"
    assert source.create_operation("ghrah.fs.read_file").name == "read_file"


async def test_schema_two_requires_explicit_identity_reset(tmp_path):
    from ghrah.ability.versions import capture_file_version

    path = tmp_path / "legacy.db"
    store = await AbilityAccessStore.open(
        path, "project", operators=frozenset({"admin"}), runs=lambda _: "alice-run"
    )
    version = capture_file_version()
    await store.commit_version(ADMIN, version)
    await store.register_agent(ADMIN, "alice")
    await store.select_instance(ADMIN, "files", version.digest, tmp_path)
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="files",
            instance_id="files",
            members=["read_file"],
            allowed_roots=[str(tmp_path)],
            expected_revision=0,
        ),
    )
    await store.close()
    db = await aiosqlite.connect(path)
    await db.executescript("""
        ALTER TABLE ability_versions DROP COLUMN manifest;
        ALTER TABLE ability_grants DROP COLUMN content;
        DROP TABLE ability_tree_schema;
        CREATE TABLE ability_tree_schema(version INTEGER PRIMARY KEY CHECK(version=2));
        INSERT INTO ability_tree_schema VALUES(2);
    """)
    await db.close()
    with pytest.raises(ValueError, match="稳定 Agent ID"):
        await AbilityAccessStore.open(
            path, "project", operators=frozenset({"admin"}), runs=lambda _: "alice-run"
        )


def test_portable_capture_rejects_file_replaced_between_stat_and_open(tmp_path, monkeypatch):
    """可移植实现用"打开后复核文件身份"兜住 stat 与 open 之间的替换。"""
    from ghrah.ability import content

    if hasattr(os, "O_DIRECTORY"):
        pytest.skip("POSIX 走 dir_fd + NOFOLLOW 实现，本用例只覆盖可移植分支")
    root = tmp_path / "pkg"
    (root / "assets").mkdir(parents=True)
    target = root / "assets" / "template.txt"
    target.write_text("original", encoding="utf-8")
    original = content.os.open
    replaced = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if Path(path).name == "template.txt" and not replaced:
            replaced = True
            target.unlink()
            target.write_text("replaced", encoding="utf-8")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(content.os, "open", replace_before_open)

    with pytest.raises(OSError, match="打开期间文件被替换"):
        content._capture_bytes_portable(root, "assets/template.txt", 1024)
    assert replaced
