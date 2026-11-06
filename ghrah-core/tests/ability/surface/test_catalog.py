# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""真实关系库验证目录、授权隔离、撤销、选择变化与版本切换。"""

import json
import random
from dataclasses import replace

import pytest
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    MemberAxes,
    RevokeAbilityRequest,
    UnbindToolRequest,
)

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.surface import AgentToolSearch, SurfaceConfig, assemble_agent_surface
from ghrah.ability.surface.catalog import read_agent_catalog
from ghrah.ability.versions import AbilityVersion, capture_file_version

ADMIN = AccessIdentity("project", "admin", "control", True)
ALICE = AccessIdentity("project", "alice", "alice-run")
BOB = AccessIdentity("project", "bob", "bob-run")


@pytest.fixture
async def catalog_env(tmp_path):
    runs = {"alice": "alice-run", "bob": "bob-run"}
    store = await AbilityAccessStore.open(
        tmp_path / "catalog.db", "project", operators=frozenset({"admin"}), runs=runs.get
    )
    try:
        version = capture_file_version()
        await store.commit_version(ADMIN, version)
        await store.select_instance(ADMIN, "files", version.digest, tmp_path)
        for actor, members in ((ALICE, ["read_file"]), (BOB, ["list_directory"])):
            await store.register_agent(ADMIN, actor.subject_id)
            view = await store.grant(
                ADMIN,
                actor.subject_id,
                GrantAbilityRequest(
                    alias="fs",
                    instance_id="files",
                    members=members,
                    allowed_roots=[str(tmp_path)],
                    expected_revision=0,
                ),
            )
            await store.bind_tool(
                actor,
                actor.subject_id,
                BindToolRequest(
                    alias="fs",
                    member=members[0],
                    tool_name=members[0],
                    expected_revision=view.revision,
                ),
            )
        yield store, runs, version, tmp_path
    finally:
        await store.close()


async def test_snapshot_has_selected_contract_metadata_without_expansion(catalog_env):
    store, _, _, _ = catalog_env
    catalog = await read_agent_catalog("alice", "alice-run", store=store)
    assert len(catalog.tools) == 1
    tool = catalog.tools[0]
    assert tool.full_name == "ghrah.fs.read_file"
    assert tool.entry.member.member == "read_file"
    assert tool.input_schema["properties"]["file_path"]["type"] == "string"
    assert "read" in tool.tags
    assert [item.entry.tool_name for item in catalog.tools] == ["read_file"]
    with pytest.raises(AccessError, match="access_denied"):
        await store.surface_snapshot(ALICE, "bob")
    with pytest.raises(AccessError, match="access_denied"):
        await store.surface_snapshot(AccessIdentity("other-project", "alice", "alice-run"), "alice")


async def test_real_agent_isolation_200_random_queries(catalog_env):
    store, _, _, _ = catalog_env
    alice = AgentToolSearch("alice", "alice-run", store=store)
    bob = AgentToolSearch("bob", "bob-run", store=store)
    randomizer = random.Random(20261106)
    words = ["read", "file", "list", "directory", "filesystem", "read_file", "list_directory"]
    for _ in range(200):
        query = " ".join(randomizer.choices(words, k=randomizer.randint(1, 3)))
        assert all(item.tool_name == "read_file" for item in await alice.search(query))
        assert all(item.tool_name == "list_directory" for item in await bob.search(query))
    assert alice._index is not bob._index


async def test_real_selection_revoke_and_regrant_rebuild_index(catalog_env):
    store, _, _, root = catalog_env
    search = AgentToolSearch("alice", "alice-run", store=store)
    assert await search.search("read")
    view = await store.view(ALICE, "alice")
    await store.unbind_tool(
        ALICE, "alice", UnbindToolRequest(tool_name="read_file", expected_revision=view.revision)
    )
    assert await search.search("read") == []
    view = await store.view(ALICE, "alice")
    await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="fs", member="read_file", tool_name="read_file", expected_revision=view.revision
        ),
    )
    assert await search.search("read")
    view = await store.view(ALICE, "alice")
    await store.revoke(
        ADMIN, "alice", RevokeAbilityRequest(alias="fs", expected_revision=view.revision)
    )
    assert await search.search("read") == []
    view = await store.view(ALICE, "alice")
    view = await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="fs",
            instance_id="files",
            members=["read_file"],
            allowed_roots=[str(root)],
            expected_revision=view.revision,
        ),
    )
    assert await search.search("read") == []  # 获授尚未选择。
    await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="fs", member="read_file", tool_name="read_file", expected_revision=view.revision
        ),
    )
    assert await search.search("read")


async def test_version_switch_removes_old_index_and_reads_new_metadata(catalog_env):
    store, _, version, root = catalog_env
    search = AgentToolSearch("alice", "alice-run", store=store)
    assert await search.search("read")
    data = json.loads(version.definition_json)
    data["operations"]["read_file"]["tags"] = ["newversiontag"]
    data["operations"]["read_file"]["effects"] = ["filesystem:read"]
    changed = AbilityVersion("", json.dumps(data), version.implementation_digest)
    changed = replace(changed, digest=changed.sealed_digest())
    await store.commit_version(ADMIN, changed)
    await store.select_instance(ADMIN, "files", changed.digest, root, expected_revision=1)
    assert await search.search("read") == []
    view = await store.view(ALICE, "alice")
    view = await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="fs",
            instance_id="files",
            members=["read_file"],
            allowed_roots=[str(root)],
            expected_revision=view.revision,
        ),
    )
    await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="fs", member="read_file", tool_name="read_file", expected_revision=view.revision
        ),
    )
    result = await search.search("newversiontag")
    assert result[0].effects == ("filesystem:read",)
    catalog = await read_agent_catalog("alice", "alice-run", store=store)
    assert catalog.tools[0].version_digest == changed.digest


async def test_assembly_filters_backend_but_unavailable_member_remains_describable(catalog_env):
    store, _, _, root = catalog_env
    result = await assemble_agent_surface(
        "alice",
        "alice-run",
        store=store,
        backend_available=lambda _: True,
        config=SurfaceConfig(mode="static"),
        max_context_length=128000,
    )
    assert [entry.tool_name for entry in result.static] == ["read_file"]
    result = await assemble_agent_surface(
        "alice", "alice-run", store=store, backend_available=lambda _: False
    )
    assert result.static == result.long_tail == ()
    view = await store.view(ALICE, "alice")
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="fs",
            instance_id="files",
            members=["read_file"],
            allowed_roots=[str(root)],
            expected_revision=view.revision,
            axes=[MemberAxes(member="operations/read_file", available=False)],
        ),
    )
    view = await store.view(ALICE, "alice")
    await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="fs", member="read_file", tool_name="read_file", expected_revision=view.revision
        ),
    )
    catalog = await read_agent_catalog("alice", "alice-run", store=store)
    assert not catalog.tools[0].entry.available
    assert await AgentToolSearch("alice", "alice-run", store=store).search("read")
    result = await assemble_agent_surface(
        "alice", "alice-run", store=store, backend_available=lambda _: True
    )
    assert result.static == result.long_tail == ()


async def test_stale_run_never_returns_cached_search_or_assembly(catalog_env):
    store, runs, _, _ = catalog_env
    search = AgentToolSearch("alice", "alice-run", store=store)
    assert await search.search("read")
    runs["alice"] = "alice-new-run"
    with pytest.raises(AccessError, match="access_denied"):
        await search.search("read")
    assert search._index is None
    with pytest.raises(AccessError, match="access_denied"):
        await assemble_agent_surface(
            "alice", "alice-run", store=store, backend_available=lambda _: True
        )
