# SPDX-License-Identifier: Apache-2.0
"""真实快照、关系库、Scheme 和 Context 的容器闭环及失败边界。"""

import asyncio
import base64
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


async def test_mixed_tree_bytes_context_and_real_operation(container, tmp_path):
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
    assert not view.bound_tools
    script = next(item for item in node.operations if item.key == "render")
    assert script.availability == "unavailable"
    assert "operations/render/script" in json.loads(version.manifest_json)
    with pytest.raises(SchemeError):
        await client.call("/scheme/ability/reports/operations/render", "render", {})
    (tmp_path / "note.txt").write_text("real effect")
    result = await client.call(
        "/scheme/ability/reports/operations/read_file", "read_file", {"file_path": "note.txt"}
    )
    assert result["data"]["content"] == "real effect"
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
    (source / "SKILL.md").write_text("uncommitted")
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


async def test_content_only_package_needs_no_file_root_and_upgrade_is_explicit(container, tmp_path):
    store, host, client, version, source, runs = container
    data = yaml.safe_load((source / "ability.yaml").read_text())
    data["operations"] = {}
    data["provider_requirements"] = {}
    raw = yaml.safe_dump(data)
    content = capture_ability_version(raw, root=source)
    await store.commit_version(ADMIN, content)
    await store.select_instance(ADMIN, "content-only", content.digest)
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="guide", instance_id="content-only", whole_container=True, expected_revision=1
        ),
    )
    await host.install("content-only")
    before = await client.call("/scheme/ability/guide/skill/guide", "read", {})
    (source / "assets/template.txt").write_text("new template")
    updated = capture_ability_version(raw, root=source)
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


async def test_operation_backend_failure_does_not_block_content_and_corruption_fails(container):
    store, host, client, version, source, runs = container
    await host.runtime.manager.withdraw(host._exports["reporting"].provider)
    assert (await client.call("/scheme/ability/reports/skill/guide/assets/template", "read", {}))[
        "content"
    ]
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
        "top-asset",
        "unknown",
        "oversize",
        "bad-digest",
    ],
)
def test_import_rejects_invalid_content(tmp_path, mutation):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    data = yaml.safe_load((source / "ability.yaml").read_text())
    limits = CaptureLimits()
    if mutation in {"absolute", "parent"}:
        data["skills"]["guide"]["body"]["path"] = (
            "/etc/passwd" if mutation == "absolute" else "../outside"
        )
    elif mutation == "symlink":
        (source / "assets/template.txt").unlink()
        try:
            (source / "assets/template.txt").symlink_to("/etc/passwd")
        except OSError as exc:  # 无符号链接权限的 Windows 环境跳过该变体
            pytest.skip(f"当前环境不允许创建符号链接: {exc}")
    elif mutation == "dangling":
        (source / "SKILL.md").write_text(
            (source / "SKILL.md").read_text() + "\n[missing](references/missing.md)\n"
        )
    elif mutation == "top-asset":
        data["assets"] = {"orphan": {"path": "assets/template.txt"}}
    elif mutation == "unknown":
        data["skills"]["guide"]["mystery"] = True
    elif mutation == "oversize":
        limits = CaptureLimits(file_bytes=1)
    elif mutation == "bad-digest":
        data["skills"]["guide"]["assets"]["template"]["expected_digest"] = "sha256:wrong"
    with pytest.raises((ValueError, OSError, ManifestValidationError)):
        capture_ability_version(yaml.safe_dump(data), root=source, limits=limits)


def test_version_captures_metadata_from_body_and_unlinked_resources(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    version = capture_ability_version((source / "ability.yaml").read_text(), root=source)
    assert version.definition().skills["guide"].title == "reporting-guide"
    assert "skill/guide/assets/template" in json.loads(version.manifest_json)
    assert version.digest != replace(version, manifest_json="{}").expected_digest()


async def test_describe_and_partial_skill_revoke_preserve_operation_selection(container):
    store, host, client, version, source, runs = container
    view = await store.view(ADMIN, "alice")
    view = await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="reports",
            member="read_file",
            tool_name="read_report",
            expected_revision=view.revision,
        ),
    )
    metadata = await client.call("/scheme/ability/reports/skill/guide", "describe", {})
    assert metadata["member"] == "skill/guide"
    assert "content" not in metadata
    view = await store.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(
            alias="reports", members=["skill/guide"], expected_revision=view.revision
        ),
    )
    assert view.abilities[0].skill is None
    assert view.bound_tools[0].name == "read_report"
    projection = await client.project_tools()
    try:
        assert len(projection.tools) == 1
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
            (source / "assets/template.txt").unlink()
            (source / "assets/template.txt").symlink_to("/etc/passwd")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(content.os, "open", replace_before_open)
    with pytest.raises(OSError):
        capture_ability_version((source / "ability.yaml").read_text(), root=source)
    assert swapped


def test_standard_import_uses_body_metadata_and_rejects_duplicate_yaml(tmp_path):
    from ghrah.ability.versions import import_skill_package

    source = tmp_path / "source"
    shutil.copytree(EXAMPLE, source)
    data = yaml.safe_load((source / "ability.yaml").read_text())
    skill = data["skills"]["guide"]
    version = import_skill_package(
        source,
        key="guide",
        identity={"namespace": "reports", "version": "1"},
        assets=skill["assets"],
        references=skill["references"],
    )
    assert not version.definition().operations
    assert version.definition().skills["guide"].title == "reporting-guide"
    with pytest.raises(ManifestValidationError, match="重复键"):
        capture_ability_version(
            (source / "ability.yaml").read_text() + "\nskills: {}\n", root=source
        )


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


async def test_same_operation_key_does_not_select_an_unsupported_backend(container):
    store, host, client, version, source, runs = container
    data = yaml.safe_load((source / "ability.yaml").read_text())
    data["operations"]["read_file"]["implementation"]["provider_slot"] = "script"
    replacement = capture_ability_version(yaml.safe_dump(data), root=source)
    await store.commit_version(ADMIN, replacement)
    await store.select_instance(
        ADMIN, "reporting", replacement.digest, source.parent, expected_revision=1
    )
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="reports",
            instance_id="reporting",
            whole_container=True,
            allowed_roots=[str(source.parent)],
            expected_revision=2,
        ),
    )
    await host.install("reporting")
    node = (await client.read("alice")).abilities[0]
    states = {operation.key: operation.availability for operation in node.operations}
    assert states["read_file"] == "unavailable"
    assert states["list_directory"] == "ready"
    assert (await client.call("/scheme/ability/reports/skill/guide", "read", {}))["content"]
    with pytest.raises(SchemeError):
        await client.call(
            "/scheme/ability/reports/operations/read_file", "read_file", {"file_path": "SKILL.md"}
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
