# SPDX-License-Identifier: Apache-2.0
"""真实数据库／后端和受信身份下验证资源关系切片。"""

import asyncio
from pathlib import Path

import pytest
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    GrantAbilityRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)
from ghrah.scheme import (
    ControlPrincipal,
    ErrorCode,
    InvocationState,
    Principal,
    SchemeError,
)

from ghrah.ability.access import AbilityAccessStore, AccessError, AccessIdentity
from ghrah.ability.scheme import AbilitySchemeHost
from ghrah.ability.versions import capture_file_version
from ghrah.context.persistence.sqlite_backend import SqliteBackend
from ghrah.scheme_ability.loading.ability_roots import builtin_root

ADMIN = AccessIdentity("project", "admin", "control", True)
ALICE = AccessIdentity("project", "alice", "alice-run")
BOB = AccessIdentity("project", "bob", "bob-run")


@pytest.fixture
def runs():
    return {"alice": "alice-run", "bob": "bob-run"}


@pytest.fixture
async def slice_env(tmp_path, runs):
    database = tmp_path / "project.db"
    store = await AbilityAccessStore.open(
        database, "project", operators=frozenset({"admin"}), runs=runs.get
    )
    host = None
    try:
        version = capture_file_version()
        await store.commit_version(ADMIN, version)
        for name in ("alice", "bob"):
            root = tmp_path / name
            root.mkdir()
            (root / "memo.txt").write_text(name)
            await store.register_agent(ADMIN, name)
            await store.select_instance(ADMIN, f"files-{name}", version.digest, root)
            await store.grant(
                ADMIN,
                name,
                GrantAbilityRequest(
                    alias="files",
                    instance_id=f"files-{name}",
                    members=["read_file", "list_directory"],
                    allowed_roots=[str(root)],
                    expected_revision=0,
                ),
            )
        host = AbilitySchemeHost(store)
        await host.install("files-alice")
        await host.install("files-bob")
        yield store, host, tmp_path
    finally:
        if host is not None:
            assert (await host.close()).complete
        await store.close()


def agent_session(host, actor=ALICE):
    return host.session(Principal(actor.project_id, actor.subject_id, actor.run_id))


async def terminal(session, call):
    snapshot = await session.observe(call, wait=1)
    while snapshot.pending:
        snapshot = await session.observe(call, wait=1, after_version=snapshot.version)
    return snapshot


async def test_real_backend_local_and_global_paths_preserve_requester(slice_env):
    store, host, _ = slice_env
    for actor in (ALICE, BOB):
        client = agent_session(host, actor)
        output = await client.call(
            "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )
        assert output["data"]["content"] == actor.subject_id
        listing = await client.call(
            f"/scheme/agent/{actor.subject_id}/ability/files/operations/list_directory",
            "list_directory",
            {"dir_path": "."},
        )
        assert listing["data"]["entries"][0]["name"] == "memo.txt"
    operator = host.session(ControlPrincipal("project", "admin", "control"))
    ref = await operator.acquire(
        "/scheme/ability/files-alice/operations/read_file",
        actions=frozenset({"read_file"}),
        global_instance=True,
    )
    call = await operator.session.submit(
        ref, action="read_file", arguments={"file_path": "memo.txt"}
    )
    result = await terminal(operator.session, call)
    assert result.result["data"]["content"] == "alice"
    assert result.admission_record is None
    assert result.target.scope == operator.principal.scope
    with pytest.raises(SchemeError, match="access_denied"):
        await agent_session(host).acquire(
            "/scheme/ability/files-bob/operations/read_file",
            actions=frozenset({"read_file"}),
            global_instance=True,
        )
    with pytest.raises(SchemeError, match="access_denied"):
        await agent_session(host).acquire(
            "/scheme/agent/bob/ability/files/operations/read_file",
            actions=frozenset({"read_file"}),
        )
    assert (await store.view(ADMIN, "alice")).abilities[0].instance_id == "files-alice"


async def test_selection_does_not_grant_and_view_is_rebuildable(slice_env):
    store, host, _ = slice_env
    client = agent_session(host)
    before = await client.read("alice")
    assert len(before.abilities[0].operations) == 2
    assert before.bound_tools == []
    result = await client.call(
        "/scheme/agent/alice",
        "bind_tool",
        {
            "alias": "files",
            "member": "read_file",
            "tool_name": "read_report",
            "expected_revision": before.revision,
        },
        request_key="bind-report",
    )
    assert result["error"] is None
    after = await client.read("alice")
    assert [item.name for item in after.bound_tools] == ["read_report"]
    assert len(after.abilities[0].operations) == 2
    assert (await client.read("alice")).model_dump(exclude={"runtime"}) == after.model_dump(
        exclude={"runtime"}
    )
    assert after.abilities[0].availability == "ready"
    rejected = await client.call(
        "/scheme/agent/alice",
        "bind_tool",
        {
            "alias": "files",
            "member": "write_file",
            "tool_name": "write_report",
            "expected_revision": after.revision,
        },
        request_key="deny-ungranted",
    )
    assert rejected["error"] == "access_denied"
    assert (await client.read("alice")).model_dump(exclude={"runtime"}) == after.model_dump(
        exclude={"runtime"}
    )
    # 普通 Agent 不拥有新增获授关系的管理动作。
    with pytest.raises(SchemeError) as denied:
        await client.acquire("/scheme/agent/alice", actions=frozenset({"grant"}))
    assert denied.value.code is ErrorCode.ACCESS_DENIED
    # 领域和驱动都返回隔离投影，修改旧响应不改变权威。
    after.abilities.clear()
    assert len((await store.view(ALICE, "alice")).abilities) == 1


async def test_revoke_regrant_and_unbind_do_not_revive_old_references(slice_env):
    store, host, root = slice_env
    client = agent_session(host)
    selected = await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_report",
            expected_revision=1,
        ),
    )
    path = "/scheme/agent/alice/bound_tools/read_report"
    old = await client.acquire(path, actions=frozenset({"read_file"}))
    # 再获取相同实例不隐式重绑，不应使已经取得的引用失效。
    other = await client.acquire(
        "/scheme/ability/files/operations/list_directory", actions=frozenset({"list_directory"})
    )
    call = await client.session.submit(old, action="read_file", arguments={"file_path": "memo.txt"})
    assert (await terminal(client.session, call)).state is InvocationState.COMPLETED
    await client.session.release(other)
    unbound = await store.unbind_tool(
        ALICE,
        "alice",
        UnbindToolRequest(
            tool_name="read_report",
            expected_revision=selected.revision,
        ),
    )
    call = await client.session.submit(old, action="read_file", arguments={"file_path": "memo.txt"})
    assert (await terminal(client.session, call)).error is ErrorCode.ACCESS_DENIED
    rebound = await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_report",
            expected_revision=unbound.revision,
        ),
    )
    call = await client.session.submit(old, action="read_file", arguments={"file_path": "memo.txt"})
    assert (await terminal(client.session, call)).error is ErrorCode.ACCESS_DENIED
    revoked = await store.revoke(
        ADMIN,
        "alice",
        RevokeAbilityRequest(
            alias="files",
            expected_revision=rebound.revision,
        ),
    )
    assert revoked.bound_tools == revoked.abilities == []
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="files",
            instance_id="files-alice",
            members=["read_file"],
            allowed_roots=[str(root / "alice")],
            expected_revision=revoked.revision,
        ),
    )
    call = await client.session.submit(old, action="read_file", arguments={"file_path": "memo.txt"})
    assert (await terminal(client.session, call)).error is ErrorCode.ACCESS_DENIED
    assert (
        await agent_session(host, BOB).call(
            "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )
    )["data"]["content"] == "bob"


async def test_revision_conflict_ranges_run_and_principal_isolation(slice_env, runs):
    store, host, root = slice_env
    client = agent_session(host)
    for arguments in ({"file_path": "../bob/memo.txt"}, {"file_path": str(root / "bob/memo.txt")}):
        with pytest.raises(SchemeError) as denied:
            await client.call("/scheme/ability/files/operations/read_file", "read_file", arguments)
        assert denied.value.code is ErrorCode.ACCESS_DENIED
    with pytest.raises(AccessError, match="conflict"):
        await store.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="files",
                member="read_file",
                tool_name="read_report",
                expected_revision=0,
            ),
        )
    assert (await client.read("alice")).revision == 1
    for principal in (
        Principal("project", "alice", "old-run"),
        Principal("other", "alice", "alice-run"),
        ControlPrincipal("project", "stranger", "control"),
    ):
        with pytest.raises(SchemeError) as denied:
            await host.session(principal).read("alice")
        assert denied.value.code is ErrorCode.ACCESS_DENIED
    runs["alice"] = "new-run"
    with pytest.raises(SchemeError):
        await client.read("alice")


async def test_explicit_version_commit_only_affects_selected_instance(slice_env):
    store, host, root = slice_env
    before = await store.view(BOB, "bob")
    raw = builtin_root().locator.joinpath("fs", "ability.yaml").read_text(encoding="utf-8")
    source = root / "candidate.yaml"
    source.write_text(raw.replace("title: Read File", "title: Read Report"))
    assert (await store.view(ALICE, "alice")).revision == 1
    version = capture_file_version(source.read_text())
    await store.commit_version(ADMIN, version)
    # 登记候选不切换当前实例，显式选择才生效。
    assert (await store.view(ALICE, "alice")).revision == 1
    await store.select_instance(
        ADMIN, "files-alice", version.digest, root / "alice", expected_revision=1
    )
    assert (await store.view(ALICE, "alice")).abilities == []
    assert await store.view(BOB, "bob") == before
    unchanged = await store.select_instance(
        ADMIN,
        "files-alice",
        version.digest,
        root / "alice",
        expected_revision=2,
    )
    assert unchanged == 2
    assert (await store.view(ALICE, "alice")).revision == 2
    await host.install("files-alice")


async def test_sqlite_restart_shared_context_and_concurrent_writers(tmp_path):
    database = tmp_path / "project.db"
    backend = SqliteBackend(database, run_id="storage-run")
    await backend.connect()
    try:
        context_before = await backend.load_session_catalog("alice")
        store = await AbilityAccessStore.open(
            database,
            "project",
            operators=frozenset({"admin"}),
            runs={"alice": "alice-run", "bob": "bob-run"}.get,
        )
        peer = await AbilityAccessStore.open(
            database,
            "project",
            operators=frozenset({"admin"}),
            runs={"alice": "alice-run", "bob": "bob-run"}.get,
        )
        try:
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
            results = await asyncio.gather(
                *(
                    connection.bind_tool(
                        ALICE,
                        "alice",
                        BindToolRequest(
                            alias="files",
                            member="read_file",
                            tool_name=name,
                            expected_revision=1,
                        ),
                    )
                    for connection, name in ((store, "first"), (peer, "second"))
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(result, AccessError) for result in results) == 1
            snapshot = await store.view(ALICE, "alice")
            assert snapshot.revision == 2 and len(snapshot.bound_tools) == 1
        finally:
            await peer.close()
            await store.close()
        reopened = await AbilityAccessStore.open(
            database,
            "project",
            operators=frozenset({"admin"}),
            runs={"alice": "alice-run", "bob": "bob-run"}.get,
        )
        try:
            assert await reopened.view(ALICE, "alice") == snapshot
            assert await backend.load_session_catalog("alice") == context_before
        finally:
            await reopened.close()
    finally:
        await backend.close()


async def test_runtime_schema_uses_yaml_and_rejects_coercion(slice_env):
    _, host, _ = slice_env
    client = agent_session(host)
    ref = await client.acquire(
        "/scheme/ability/files/operations/read_file", actions=frozenset({"read_file"})
    )
    for arguments in (
        {"file_path": "memo.txt", "offset": True},
        {"file_path": "memo.txt", "offset": "1"},
        {"file_path": "memo.txt", "extra": "bad"},
    ):
        with pytest.raises(SchemeError) as error:
            await client.session.submit(ref, action="read_file", arguments=arguments)
        assert error.value.code is ErrorCode.INVALID_INPUT
    result = await client.call(
        "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "missing.txt"}
    )
    assert result["outcome"] == "failure" and "error" in result["data"]
    view = await client.read("alice")
    spec = host._exports["files-alice"].spec
    assert view.abilities[0].operations[1].input_schema == spec.action("read_file").schema()
    assert view.abilities[0].operations[1].output_schema == spec.action("read_file").schema(
        output=True
    )


async def test_model_tools_are_selected_and_hold_the_original_grant(slice_env):
    store, host, _ = slice_env
    await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_report",
            expected_revision=1,
        ),
    )
    client = agent_session(host)
    projection = await client.project_tools()
    try:
        assert [item["function"]["name"] for item in projection.tools] == ["read_report"]
        assert (await projection.call("read_report", {"file_path": "memo.txt"}))["data"][
            "content"
        ] == "alice"
        await store.revoke(ADMIN, "alice", RevokeAbilityRequest(alias="files", expected_revision=2))
        with pytest.raises(SchemeError) as denied:
            await projection.call("read_report", {"file_path": "memo.txt"})
        assert denied.value.code is ErrorCode.ACCESS_DENIED
        assert (await client.read("alice")).bound_tools == []
    finally:
        await projection.close()


async def test_shared_instance_and_failed_write_leave_other_agents_unchanged(slice_env):
    store, host, root = slice_env
    before = await store.view(BOB, "bob")
    await store.grant(
        ADMIN,
        "bob",
        GrantAbilityRequest(
            alias="shared",
            instance_id="files-alice",
            members=["read_file"],
            allowed_roots=[str(root / "alice")],
            expected_revision=before.revision,
        ),
    )
    await store.revoke(ADMIN, "alice", RevokeAbilityRequest(alias="files", expected_revision=1))
    bob = agent_session(host, BOB)
    assert (
        await bob.call(
            "/scheme/ability/shared/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )
    )["data"]["content"] == "alice"
    current = await store.view(BOB, "bob")
    with pytest.raises(AccessError, match="invalid_change"):
        await store.grant(
            ADMIN,
            "bob",
            GrantAbilityRequest(
                alias="outside",
                instance_id="files-alice",
                members=["read_file"],
                allowed_roots=[str(root.parent)],
                expected_revision=current.revision,
            ),
        )
    assert await store.view(BOB, "bob") == current


async def test_scheme_relation_mutations_and_noop_changes(slice_env):
    store, host, root = slice_env
    operator = host.session(ControlPrincipal("project", "admin", "control"))
    result = await operator.call(
        "/scheme/agent/alice",
        "revoke",
        {
            "alias": "files",
            "expected_revision": 1,
        },
        request_key="revoke",
    )
    assert result["view"]["abilities"] == []
    result = await operator.call(
        "/scheme/agent/alice",
        "grant",
        {
            "alias": "files",
            "instance_id": "files-alice",
            "members": ["read_file"],
            "allowed_roots": [str(root / "alice")],
            "expected_revision": 2,
        },
        request_key="grant",
    )
    assert result["view"]["revision"] == 3
    identical = await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="files",
            instance_id="files-alice",
            members=["read_file"],
            allowed_roots=[str(root / "alice")],
            expected_revision=3,
        ),
    )
    assert identical.revision == 3
    agent = agent_session(host)
    view = await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_report",
            expected_revision=3,
        ),
    )
    unchanged = await store.bind_tool(
        ALICE,
        "alice",
        BindToolRequest(
            alias="files",
            member="read_file",
            tool_name="read_report",
            expected_revision=view.revision,
        ),
    )
    assert view == unchanged
    # 新的同 scope 会话不应仅因创建连接就改变已有路由修订。
    reference = await agent.acquire("/scheme/view/alice", actions=frozenset({"read"}))
    agent_session(host)
    call = await agent.session.submit(reference, action="read", arguments={})
    assert (await terminal(agent.session, call)).state is InvocationState.COMPLETED


def test_consumer_fixture_export_is_current():
    import json
    import runpy

    script = Path(__file__).resolve().parents[3] / "scripts/export_ability_tree_fixture.py"
    exported = runpy.run_path(str(script))["fixtures"]()
    target = script.parent.parent / (
        "ghrah-observer-webui/packages/protocol/src/__fixtures__/ability-tree.json"
    )
    assert json.loads(target.read_text()) == exported


async def test_switched_root_is_unavailable_until_matching_backend_is_installed(slice_env):
    store, host, base = slice_env
    client = agent_session(host)
    path = "/scheme/ability/files/operations/read_file"
    old = await client.acquire(path, actions=frozenset({"read_file"}))
    second = base / "new-root"
    second.mkdir()
    (second / "memo.txt").write_text("new root")
    info = await store.instance("files-alice")
    await store.select_instance(
        ADMIN, "files-alice", info["digest"], second, expected_revision=info["revision"]
    )
    view = await store.view(ADMIN, "alice")
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="files",
            instance_id="files-alice",
            members=["read_file"],
            allowed_roots=[str(second)],
            expected_revision=view.revision,
        ),
    )
    assert (await client.read("alice")).abilities[0].availability == "unavailable"
    with pytest.raises(SchemeError) as denied:
        await client.call(path, "read_file", {"file_path": "memo.txt"})
    assert denied.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    operator = host.session(ControlPrincipal("project", "admin", "control"))
    with pytest.raises(SchemeError) as denied:
        await operator.acquire(
            "/scheme/ability/files-alice/operations/read_file",
            actions=frozenset({"read_file"}),
            global_instance=True,
        )
    assert denied.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    denied_call = await client.session.submit(
        old, action="read_file", arguments={"file_path": "memo.txt"}
    )
    assert (await terminal(client.session, denied_call)).error is ErrorCode.ACCESS_DENIED
    await host.install("files-alice")
    assert (await client.call(path, "read_file", {"file_path": "memo.txt"}))["data"][
        "content"
    ] == "new root"
    with pytest.raises(SchemeError):
        await client.session.submit(old, action="read_file", arguments={"file_path": "memo.txt"})


async def test_install_is_idempotent_and_reuses_provider_and_contract_capacity(slice_env):
    store, host, base = slice_env
    client = agent_session(host)
    path = "/scheme/ability/files/operations/read_file"
    reference = await client.acquire(path, actions=frozenset({"read_file"}))
    await host.install("files-alice")
    await host.install("files-alice")
    call = await client.session.submit(
        reference, action="read_file", arguments={"file_path": "memo.txt"}
    )
    assert (await terminal(client.session, call)).state is InvocationState.COMPLETED
    original = host.runtime.manager.diagnostics()
    roots = [base / "alice", base / "bob"]
    raw = builtin_root().locator.joinpath("fs", "ability.yaml").read_text()
    for revision in range(1, 71):
        version = capture_file_version(
            raw.replace("title: Read File", f"title: Read File {revision}")
        )
        await store.commit_version(ADMIN, version)
        await store.select_instance(
            ADMIN, "files-alice", version.digest, roots[revision % 2], expected_revision=revision
        )
        await host.install("files-alice")
    current = host.runtime.manager.diagnostics()
    assert len(current["providers"]) == len(original["providers"]) == 4
    assert set(current["providers"].values()) == {"ready"}
    assert current["contracts"] == original["contracts"] == 3
    assert (await agent_session(host, BOB).call(path, "read_file", {"file_path": "memo.txt"}))[
        "data"
    ]["content"] == "bob"


async def test_read_admission_neither_writes_nor_waits_for_sqlite_writer(slice_env):
    import aiosqlite

    store, host, base = slice_env
    before = store._db.total_changes
    writer = await aiosqlite.connect(base / "project.db")
    try:
        await writer.execute("BEGIN IMMEDIATE")
        for _ in range(3):
            result = await asyncio.wait_for(
                agent_session(host).call(
                    "/scheme/ability/files/operations/read_file",
                    "read_file",
                    {"file_path": "memo.txt"},
                ),
                1,
            )
            assert result["data"]["content"] == "alice"
        assert store._db.total_changes == before
        async with store._db.execute(
            "SELECT name FROM sqlite_master WHERE name='ability_admissions'"
        ) as cursor:
            assert await cursor.fetchone() is None
    finally:
        await writer.rollback()
        await writer.close()


async def test_hidden_operation_cannot_be_granted_published_or_selected(slice_env):
    import yaml

    store, host, base = slice_env
    raw = yaml.safe_load(builtin_root().locator.joinpath("fs", "ability.yaml").read_text())
    raw["operations"]["read_file"]["expose"] = False
    version = capture_file_version(yaml.safe_dump(raw))
    await store.commit_version(ADMIN, version)
    await store.select_instance(
        ADMIN, "files-alice", version.digest, base / "alice", expected_revision=1
    )
    view = await store.view(ADMIN, "alice")
    with pytest.raises(AccessError, match="invalid_change"):
        await store.grant(
            ADMIN,
            "alice",
            GrantAbilityRequest(
                alias="files",
                instance_id="files-alice",
                members=["read_file"],
                allowed_roots=[str(base / "alice")],
                expected_revision=view.revision,
            ),
        )
    await store.grant(
        ADMIN,
        "alice",
        GrantAbilityRequest(
            alias="files",
            instance_id="files-alice",
            members=["list_directory"],
            allowed_roots=[str(base / "alice")],
            expected_revision=view.revision,
        ),
    )
    await host.install("files-alice")
    assert [item.name for item in host._exports["files-alice"].spec.actions] == ["list_directory"]
    view = await agent_session(host).read("alice")
    assert [item.key for item in view.abilities[0].operations] == ["list_directory"]
    with pytest.raises(AccessError, match="access_denied"):
        await store.bind_tool(
            ALICE,
            "alice",
            BindToolRequest(
                alias="files",
                member="read_file",
                tool_name="hidden",
                expected_revision=view.revision,
            ),
        )
    with pytest.raises(SchemeError):
        await agent_session(host).call(
            "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )


async def test_external_run_owner_and_rebuilt_exports_are_the_only_runtime_sources(slice_env, runs):
    store, host, _ = slice_env
    client = agent_session(host)
    original = await client.read("alice")
    raw = await store.view(ALICE, "alice")
    assert raw.runtime is None and raw.abilities[0].availability == "unknown"
    async with store._db.execute("PRAGMA table_info(ability_agents)") as cursor:
        assert "run" not in {row[1] for row in await cursor.fetchall()}
    runs["alice"] = "replacement-run"
    with pytest.raises(SchemeError):
        await client.read("alice")
    replacement = host.session(Principal("project", "alice", "replacement-run"))
    assert (await replacement.read("alice")).run_id == "replacement-run"
    rebuilt = AbilitySchemeHost(store)
    try:
        next_client = rebuilt.session(Principal("project", "alice", "replacement-run"))
        before = await next_client.read("alice")
        assert before.abilities[0].availability == "unavailable"
        assert before.runtime.host_id != original.runtime.host_id
        await rebuilt.restore(ADMIN)
        after = await next_client.read("alice")
        assert after.abilities[0].availability == "ready"
        assert after.runtime.observed_at.endswith("Z")
        assert await store.list_instances(ADMIN) == ["files-alice", "files-bob"]
        assert len(await store.list_versions(ADMIN)) == 1
        result = await next_client.call(
            "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )
        assert result["data"]["content"] == "alice"
        with pytest.raises(AccessError):
            await store.list_instances(ALICE)
    finally:
        assert (await rebuilt.close()).complete


async def test_changes_are_a_bounded_revision_feed_with_actor_and_version(slice_env):
    import json

    store, host, _ = slice_env
    first = await store.changes(ADMIN, limit=1)
    assert len(first) == 1
    assert first[0]["reason"] == "grant"
    assert first[0]["before_revision"] == 0 and first[0]["revision"] == 1
    assert json.loads(first[0]["actor"])["subject"] == "admin"
    assert first[0]["version_digest"] == (await store.instance("files-alice"))["digest"]
    second = await store.changes(ADMIN, after_seq=first[0]["seq"])
    assert len(second) == 1 and second[0]["agent"] == "bob"
    with pytest.raises(AccessError):
        await store.changes(ALICE)
    with pytest.raises(AccessError):
        await store.changes(ADMIN, limit=1001)
    before = await store.changes(ADMIN)
    await agent_session(host).read("alice")
    assert await store.changes(ADMIN) == before


async def test_legacy_schema_preserves_relationship_revision_but_removes_run_copy(tmp_path):
    import aiosqlite

    path = tmp_path / "legacy.db"
    db = await aiosqlite.connect(path)
    try:
        await db.executescript("""
            CREATE TABLE ability_tree_schema(version INTEGER PRIMARY KEY CHECK(version=1));
            INSERT INTO ability_tree_schema VALUES(1);
            CREATE TABLE ability_agents(project TEXT,id TEXT,run TEXT,revision INTEGER,
                PRIMARY KEY(project,id));
            INSERT INTO ability_agents VALUES('project','alice','obsolete-run',7);
            CREATE TABLE ability_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT,agent TEXT,revision INTEGER,reason TEXT);
            INSERT INTO ability_changes(project,agent,revision,reason)
                VALUES('project','alice',7,'legacy');
            CREATE TABLE ability_admissions(id TEXT PRIMARY KEY,arguments TEXT);
            INSERT INTO ability_admissions VALUES('legacy','retained');
        """)
    finally:
        await db.close()
    store = await AbilityAccessStore.open(
        path, "project", operators=frozenset({"admin"}), runs={"alice": "owner-run"}.get
    )
    try:
        view = await store.view(ADMIN, "alice")
        assert view.run_id == "owner-run" and view.revision == 7
        assert (await store.changes(ADMIN))[0]["actor"] is None
        async with store._db.execute("SELECT arguments FROM ability_admissions") as cursor:
            assert (await cursor.fetchone())[0] == "retained"
    finally:
        await store.close()


async def test_member_fence_codec_and_public_errors_are_strict(slice_env):
    from dataclasses import replace

    from pydantic import TypeAdapter

    from ghrah.ability.access import MemberFence
    from ghrah.ability.scheme.host import _fence

    store, host, _ = slice_env
    client = agent_session(host)
    ref = await client.acquire(
        "/scheme/ability/files/operations/read_file", actions=frozenset({"read_file"})
    )
    fence = await store.resolve(ALICE, "files", "read_file")
    target = client.session._runtime._resource(client.session._holder, ref).target
    assert _fence(target, "files-alice") == fence
    for payload in (
        "{}",
        '{"instance_revision":true}',
        TypeAdapter(MemberFence).dump_json(fence).decode()[:-1] + ',"future":1}',
    ):
        with pytest.raises(AccessError):
            _fence(replace(target, resource=("member-v1", payload)), "files-alice")
    for path in ("bad", "/scheme/view/bob", "/scheme/ability/missing/operations/read_file"):
        with pytest.raises(SchemeError):
            await client.acquire(path, actions=frozenset({"read"}))


async def test_revoke_after_admission_remains_access_denied_at_provider_boundary(
    slice_env, monkeypatch
):
    store, host, _ = slice_env
    original = store.admit

    async def revoke_before_provider(actor, fence, arguments):
        admitted = await original(actor, fence, arguments)
        await store.revoke(ADMIN, "alice", RevokeAbilityRequest(alias="files", expected_revision=1))
        return admitted

    monkeypatch.setattr(store, "admit", revoke_before_provider)
    with pytest.raises(SchemeError) as denied:
        await agent_session(host).call(
            "/scheme/ability/files/operations/read_file", "read_file", {"file_path": "memo.txt"}
        )
    assert denied.value.code is ErrorCode.ACCESS_DENIED
