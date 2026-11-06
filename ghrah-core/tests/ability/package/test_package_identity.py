# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""包版本的身份是整树摘要。

同一目录重复捕获、重复安装得到同一个 ``package_digest``。改一个普通文件就换
摘要，缓存目录不算。重新生成索引如果字节不变，不产生新版本，也不把已经
撤掉的旧引用接回去。用定义密封提交的旧行仍然可读，但对不上新摘要。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ghrah.protocol.ability_tree import BindToolRequest, GrantAbilityRequest

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.versions import AbilityVersion, capture_ability_version

ADMIN = AccessIdentity("project", "admin", "control", True)
ALICE = AccessIdentity("project", "alice", "alice-run")

OPERATION = """\
from pydantic import BaseModel, ConfigDict

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str

class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str

TITLE = "Render"
SUMMARY = "Render a name"
TAGS: list[str] = []
EFFECTS: list[str] = []
PERMISSIONS: dict[str, object] = {}
TIMEOUT_MS = 1000

async def execute(call, services):
    return None
"""

HEADER = """\
format: ghrah.ability-definition
schema_version: '1'
identity: {namespace: acme, name: reports, version: 1.0.0}
title: Reports
description: Identity fixture
"""


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "operations").mkdir(parents=True)
    (source / "ability.yaml").write_text(HEADER, encoding="utf-8", newline="\n")
    (source / "operations" / "render.py").write_text(OPERATION, encoding="utf-8", newline="\n")
    (source / "README.md").write_text("notes\n", encoding="utf-8", newline="\n")
    build(source)
    return source


def _capture(root: Path) -> AbilityVersion:
    return capture_ability_version((root / "ability.yaml").read_text(encoding="utf-8"), root=root)


async def _open(tmp_path: Path) -> AbilityAccessStore:
    return await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"admin"}),
        runs=lambda agent: "alice-run" if agent == "alice" else None,
    )


async def _changes(db: AbilityAccessStore) -> list[tuple[str, str | None]]:
    async with db._db.execute(
        "SELECT reason, version_digest FROM ability_changes WHERE project=? ORDER BY seq",
        (db.project_id,),
    ) as cursor:
        return [(row[0], row[1]) for row in await cursor.fetchall()]


def test_repeated_capture_and_install_share_one_digest(tmp_path: Path) -> None:
    """同一源码连续捕获、连续安装，领域身份等于安装记录。"""
    source = _source(tmp_path)
    first = _capture(source)
    second = _capture(source)
    installer = AbilityInstaller(tmp_path / "installed")
    installed = installer.install(source)
    again = installer.install(source)
    slot = installer.store.resolve("acme.reports", "1.0.0")

    assert first.digest == first.package_digest == second.digest
    assert again.already_installed
    assert installed.package_digest == again.package_digest == first.digest
    assert slot is not None and slot.lock is not None
    assert _capture(slot.root).digest == slot.lock.package_digest


def test_any_ordinary_file_changes_digest_and_cache_does_not(tmp_path: Path) -> None:
    """索引外的普通文件改变身份；缓存目录不参与摘要。"""
    source = _source(tmp_path)
    original = _capture(source)
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "render.pyc").write_bytes(b"\x00\x01")

    assert _capture(source).digest == original.digest

    (source / "README.md").write_text("changed\n", encoding="utf-8", newline="\n")

    assert _capture(source).digest != original.digest


async def test_rebuilding_identical_index_keeps_the_grant(tmp_path: Path) -> None:
    """字节不变地重新生成索引，不递增实例修订，已有授予仍可使用。"""
    source = _source(tmp_path)
    version = _capture(source)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = await _open(tmp_path)
    try:
        await db.commit_version(ADMIN, version)
        await db.select_instance(ADMIN, "reports", version.digest, workspace)
        await db.register_agent(ADMIN, "alice")
        granted = await db.grant(
            ADMIN,
            "alice",
            GrantAbilityRequest(
                alias="reports",
                instance_id="reports",
                members=["render"],
                expected_revision=0,
            ),
        )
        await db.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="reports",
                member="render",
                tool_name="render",
                expected_revision=granted.revision,
            ),
        )
        fence = await db.resolve(ALICE, "reports", "render", tool="render")
        await db.authorize(ALICE, fence)
        revision = int((await db.instance("reports"))["revision"])

        rebuilt = build(source)
        assert rebuilt.changed is False
        assert _capture(source).digest == version.digest
        assert (
            await db.select_instance(
                ADMIN, "reports", version.digest, workspace, expected_revision=revision
            )
            == revision
        )

        assert [item.alias for item in await db.grants(ADMIN, "alice")] == ["reports"]
        assert not any(reason == "instance_changed" for reason, _ in await _changes(db))
        await db.authorize(ALICE, fence)
    finally:
        await db.close()


async def test_restored_digest_does_not_revive_the_old_fence(tmp_path: Path) -> None:
    """文件改回后摘要可以回到原值，旧的成员引用仍被拒绝。"""
    source = _source(tmp_path)
    original = _capture(source)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = await _open(tmp_path)
    try:
        await db.commit_version(ADMIN, original)
        await db.select_instance(ADMIN, "reports", original.digest, workspace)
        await db.register_agent(ADMIN, "alice")
        granted = await db.grant(
            ADMIN,
            "alice",
            GrantAbilityRequest(
                alias="reports",
                instance_id="reports",
                members=["render"],
                expected_revision=0,
            ),
        )
        await db.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="reports",
                member="render",
                tool_name="render",
                expected_revision=granted.revision,
            ),
        )
        fence = await db.resolve(ALICE, "reports", "render", tool="render")
        script = source / "operations" / "render.py"
        script.write_text(
            script.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8", newline="\n"
        )
        changed = _capture(source)
        assert changed.digest != original.digest
        await db.commit_version(ADMIN, changed)
        revision = int((await db.instance("reports"))["revision"])
        await db.select_instance(
            ADMIN, "reports", changed.digest, workspace, expected_revision=revision
        )

        assert await db.grants(ADMIN, "alice") == []
        with pytest.raises(AccessError, match="access_denied"):
            await db.authorize(ALICE, fence)

        script.write_text(OPERATION, encoding="utf-8", newline="\n")
        assert build(source).changed is False
        restored = _capture(source)
        assert restored.digest == original.digest
        revision = int((await db.instance("reports"))["revision"])
        await db.select_instance(
            ADMIN, "reports", restored.digest, workspace, expected_revision=revision
        )

        assert int((await db.instance("reports"))["revision"]) == 3
        assert await db.grants(ADMIN, "alice") == []
        with pytest.raises(AccessError, match="access_denied"):
            await db.authorize(ALICE, fence)
        # 第一次切换还有授予，会记 instance_changed 并删掉它。
        # 摘要回到原值时授予已经不在，不再记第二次；实例修订仍然往前走。
        assert [reason for reason, _ in await _changes(db)].count("instance_changed") == 1
    finally:
        await db.close()


async def test_legacy_seal_remains_readable_until_the_instance_switches(tmp_path: Path) -> None:
    """旧的定义密封对不上整树摘要；切过去时撤掉授予，不改写旧行。"""
    source = _source(tmp_path)
    version = _capture(source)
    legacy = AbilityVersion(
        version.sealed_digest(),
        version.definition_json,
        version.implementation_digest,
        version.manifest_json,
        version.blobs,
    )
    assert legacy.digest != version.digest
    assert legacy.package_digest == ""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = await _open(tmp_path)
    try:
        await db.commit_version(ADMIN, legacy)
        await db.select_instance(ADMIN, "reports", legacy.digest, workspace)
        await db.register_agent(ADMIN, "alice")
        await db.grant(
            ADMIN,
            "alice",
            GrantAbilityRequest(
                alias="reports",
                instance_id="reports",
                members=["render"],
                expected_revision=0,
            ),
        )
        assert (await db.instance("reports"))["digest"] == legacy.digest
        await db.commit_version(ADMIN, version)
        revision = int((await db.instance("reports"))["revision"])
        await db.select_instance(
            ADMIN, "reports", version.digest, workspace, expected_revision=revision
        )

        assert (await db.instance("reports"))["digest"] == version.digest
        assert await db.grants(ADMIN, "alice") == []
        stored = {item.digest: item.definition_json for item in await db.list_versions(ADMIN)}
        assert stored[legacy.digest] == legacy.definition_json
        assert stored[version.digest] == version.definition_json
        assert [
            digest for reason, digest in await _changes(db) if reason == "instance_changed"
        ] == [version.digest]
    finally:
        await db.close()
