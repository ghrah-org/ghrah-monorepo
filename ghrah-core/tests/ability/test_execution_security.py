# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""执行边界的权限、稳定主体、审批与隔离回归。"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _helpers import make_call, make_services
from ghrah.protocol.ability_tree import RevokeAbilityRequest
from test_startup_materializer import AGENT_YAML, DEPLOY, _resolved

from ghrah.abilities.executor import LocalAbilityExecutor
from ghrah.ability.access import AbilityAccessStore, GrantPolicy, StartupGrantMaterializer
from ghrah.ability.execution.execution_services import LOOP
from ghrah.ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.agents.base import ActorAgent
from ghrah.types.results import ActionOutcome, ActionResult


async def materialize(db, root, text=AGENT_YAML, agent_id="a" * 32):
    assembly, resolved = _resolved(text)
    resolved.config = replace(resolved.config, name="planner", agent_id=agent_id)
    materializer = StartupGrantMaterializer(
        store=db,
        source=assembly.source,
        actor=DEPLOY,
        policy=GrantPolicy(),
        workspace_root=str(root),
    )
    table = await materializer.materialize(agent=agent_id, resolved=resolved)
    return materializer, table


class IdentityHook(Hook):
    hook_point = HookPoint.PRE_EXECUTE

    def __init__(self):
        self.calls = []

    async def should_trigger(self, call, services):
        return True

    async def execute(self, call, services, result=None):
        self.calls.append(call)
        return HookResult.continue_()


async def test_unbound_run_rejected_then_hook_receives_fixed_identity(tmp_path):
    current = {"a" * 32: "run-one"}
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=current.get,
    )
    try:
        mat, table = await materialize(db, tmp_path)
        tool = next(t for t in table.abilities if t.name == "read_file")
        target = tmp_path / "note.txt"
        target.write_text("content")
        hook = IdentityHook()
        executor = LocalAbilityExecutor("planner", hooks=[hook])
        forged = make_call(
            member="forged",
            agent="someone-else",
            project_id="other",
            run="fake",
            arguments={"file_path": str(target)},
            tool_call_id="original-call",
        )
        denied = await executor.execute_ability(tool, forged, make_services())
        assert denied.outcome is ActionOutcome.FAILURE and not hook.calls
        await mat.bind_run("a" * 32, table)
        allowed = await executor.execute_ability(tool, forged, make_services())
        assert allowed.outcome is ActionOutcome.SUCCESS
        call = hook.calls[0]
        assert call.identity.agent == "a" * 32
        assert call.identity.agent_name == "planner"
        assert call.identity.project_id == "project" and call.identity.run == "run-one"
        assert call.member == tool.member_reference and call.tool_call_id == "original-call"
        current["a" * 32] = "run-two"
        stale = await executor.execute_ability(tool, forged, make_services())
        assert stale.outcome is ActionOutcome.FAILURE
    finally:
        await db.close()


async def test_same_name_different_stable_subjects_keep_independent_grants(tmp_path):
    current = {"a" * 32: "cluster-a-run", "b" * 32: "cluster-b-run"}
    first = await AbilityAccessStore.open(
        tmp_path / "shared.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=current.get,
    )
    second = await AbilityAccessStore.open(
        tmp_path / "shared.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=current.get,
    )
    try:
        ma, ta = await materialize(
            first, tmp_path, AGENT_YAML.replace("  - ref: ghrah.fs.list_directory\n", "")
        )
        await ma.bind_run("a" * 32, ta)
        await materialize(
            second, tmp_path, AGENT_YAML.replace("  - ref: ghrah.fs.read_file\n", ""), "b" * 32
        )
        assert (await first.grants(DEPLOY, "a" * 32))[0].members == ("read_file",)
        assert (await second.grants(DEPLOY, "b" * 32))[0].members == ("list_directory",)
        tool = next(t for t in ta.abilities if t.name == "read_file")
        target = tmp_path / "a.txt"
        target.write_text("still authorized")
        result = await tool.execute(
            make_call(arguments={"file_path": str(target)}), make_services()
        )
        assert result.outcome is ActionOutcome.SUCCESS
    finally:
        await second.close()
        await first.close()


@pytest.mark.parametrize("outcome", ["approved", "rejected", "timeout", "cancel", "revoke"])
async def test_generic_approval_keeps_effects_behind_decision(tmp_path, outcome):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        "abilities:\n  - type: conversation\n  - ref: ghrah.cluster.query_agents\n"
        "    permissions:\n      require_hitl: true\n"
    )
    db = await AbilityAccessStore.open(
        tmp_path / "hitl.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "run-one",
    )
    try:
        mat, table = await materialize(db, tmp_path, text)
        await mat.bind_run("a" * 32, table)
        tool = next(t for t in table.abilities if t.name == "query_agents")
        supervisor = SimpleNamespace(
            get_cluster_context=lambda: {"cluster_id": "c"}, list_agents=AsyncMock(return_value=[])
        )
        from ghrah.core.hitl import HITLPromiseRegistry

        promises = HITLPromiseRegistry()
        executor = LocalAbilityExecutor(
            "planner", hooks=tool.get_hooks(), hitl_timeout=0.1, hitl_promise_registry=promises
        )
        task = asyncio.create_task(
            executor.execute_ability(
                tool, make_call(tool_call_id="approval-call"), make_services(supervisor=supervisor)
            )
        )
        for _ in range(100):
            if executor.hitl_store.list_pending():
                break
            await asyncio.sleep(0.001)
        assert not supervisor.list_agents.called
        if outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            if outcome == "revoke":
                revision = await db.agent_revision(DEPLOY, "a" * 32)
                await db.write_revoke(
                    DEPLOY,
                    "a" * 32,
                    RevokeAbilityRequest(alias="cluster", expected_revision=revision),
                )
            if outcome != "timeout":
                executor.receive_hitl_response(
                    "query_agents", "approval-call", outcome in {"approved", "revoke"}
                )
            result = await task
            assert (result.outcome is ActionOutcome.SUCCESS) == (outcome == "approved")
        assert supervisor.list_agents.call_count == (1 if outcome == "approved" else 0)
        assert not promises.list_pending()
        assert not executor.hitl_store.list_pending()
    finally:
        await db.close()


def test_loop_previous_result_is_an_isolated_snapshot():
    agent = object.__new__(ActorAgent)
    agent._context_manager = None
    agent._supervisor = None
    previous = ActionResult(outcome=ActionOutcome.SUCCESS, data={"value": [1]})
    agent._iteration_state = SimpleNamespace(last_action_result=previous)
    loop = agent._build_services({"last_action_result": "forged"}).require(LOOP)
    assert loop.last_action_result == previous and loop.last_action_result is not previous
    loop.last_action_result.data["value"].append(2)
    assert previous.data == {"value": [1]}


def test_offline_reset_preserves_other_domains_and_refuses_backup_overwrite(tmp_path):
    from ghrah.ability.access.maintenance import inspect_ability_domain, reset_ability_domain

    path, backup = tmp_path / "shared.db", tmp_path / "backup.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE messages(id TEXT);
            INSERT INTO messages VALUES('retained');
            CREATE TABLE ability_tree_schema(version INTEGER);
            INSERT INTO ability_tree_schema VALUES(4);
            CREATE TABLE ability_agents(id TEXT);
            INSERT INTO ability_agents VALUES('old-name');
        """)
    assert inspect_ability_domain(path) == ("ability_tree_schema", "ability_agents")
    reset_ability_domain(path, backup)
    assert inspect_ability_domain(path) == ()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM messages").fetchone() == ("retained",)
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT id FROM ability_agents").fetchone() == ("old-name",)
    with pytest.raises(FileExistsError):
        reset_ability_domain(path, backup)


@pytest.mark.parametrize("member", ["write_file", "edit_file", "delete_file", "move_file"])
async def test_readonly_blocks_each_mutation_even_with_auto_approval(tmp_path, member):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        f"abilities:\n  - type: conversation\n  - ref: ghrah.fs.{member}\n"
        "    permissions:\n      fs_read_only: true\n"
    )
    db = await AbilityAccessStore.open(
        tmp_path / "readonly.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "run-one",
    )
    try:
        assembly, resolved = _resolved(text)
        mat = StartupGrantMaterializer(
            store=db,
            source=assembly.source,
            actor=DEPLOY,
            policy=GrantPolicy(),
            workspace_root=str(tmp_path),
            auto_approve_abilities=(member,),
        )
        table = await mat.materialize(agent="a" * 32, resolved=resolved)
        await mat.bind_run("a" * 32, table)
        tool = next(t for t in table.abilities if t.name == member)
        src, dst = tmp_path / "src.txt", tmp_path / "dst.txt"
        src.write_text("untouched")
        args = {
            "file_path": str(src),
            "content": "changed",
            "old_str": "untouched",
            "new_str": "changed",
            "source_path": str(src),
            "destination_path": str(dst),
        }
        executor = LocalAbilityExecutor("planner", hooks=tool.get_hooks(), hitl_timeout=0.01)
        result = await executor.execute_ability(tool, make_call(arguments=args), make_services())
        assert result.outcome is ActionOutcome.FAILURE
        assert not executor.hitl_store.list_pending()
        assert src.read_text() == "untouched" and not dst.exists()
        direct = await tool.execute(make_call(arguments=args), make_services())
        assert direct.outcome is ActionOutcome.FAILURE
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("package", "member", "bit", "arguments"),
    [
        ("shell", "execute_command", "shell_access", {"command": "echo safe"}),
        ("mcp", "mcp_call", "net_access", {"server": "server", "tool": "tool"}),
    ],
)
async def test_explicit_denial_prevents_runner_and_runtime_calls(
    tmp_path,
    package,
    member,
    bit,
    arguments,
):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        f"abilities:\n  - type: conversation\n  - ref: ghrah.{package}.{member}\n"
        f"    permissions:\n      {bit}: false\n"
    )
    db = await AbilityAccessStore.open(
        tmp_path / "disabled.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "run-one",
    )
    try:
        assembly, resolved = _resolved(text)
        runner = SimpleNamespace(execute_command=AsyncMock())
        runtime = SimpleNamespace(call=AsyncMock())
        mat = StartupGrantMaterializer(
            store=db,
            source=assembly.source,
            actor=DEPLOY,
            policy=GrantPolicy(),
            workspace_root=str(tmp_path),
            command_runner=runner,
        )
        table = await mat.materialize(agent="a" * 32, resolved=resolved)
        await mat.bind_run("a" * 32, table)
        tool = next(t for t in table.abilities if t.name == member)
        result = await tool.execute(
            make_call(arguments=arguments), make_services(mcp_runtime=runtime)
        )
        assert result.outcome is ActionOutcome.FAILURE and bit in result.data["error"]
        assert not runner.execute_command.called and not runtime.call.called
    finally:
        await db.close()


async def test_examples_import_and_execute_without_legacy_context(tmp_path):
    import runpy

    import ghrah.ability.execution.base as execution_base

    root = Path(execution_base.__file__).resolve().parents[4] / "examples"
    for source in root.rglob("*.py"):
        assert "AbilityExecutionContext" not in source.read_text(), source
    runpy.run_path(str(root / "tool_agent.py"), run_name="import_only")
    directory = root / "student_grade_system/abilities_impl"
    listing = runpy.run_path(str(directory / "list_directory.py"))[
        "StudentGradeListDirectoryAbility"
    ]()
    listed = await listing.execute(
        make_call(arguments={"dir_path": str(tmp_path)}), make_services()
    )
    assert listed.outcome is ActionOutcome.SUCCESS
    init = runpy.run_path(str(directory / "init_project.py"))["InitProjectAbility"]()
    invalid = await init.execute(make_call(arguments={}), make_services())
    assert invalid.outcome is ActionOutcome.FAILURE


async def test_shared_database_project_grants_stay_isolated(tmp_path):
    path = tmp_path / "projects.db"
    a = await AbilityAccessStore.open(
        path,
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "a-run",
    )
    b = await AbilityAccessStore.open(
        path,
        "other-project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "b-run",
    )
    try:
        await materialize(a, tmp_path)
        other_actor = replace(DEPLOY, project_id="other-project")
        assembly, resolved = _resolved(AGENT_YAML)
        mat = StartupGrantMaterializer(
            store=b,
            source=assembly.source,
            actor=other_actor,
            policy=GrantPolicy(),
            workspace_root=str(tmp_path),
        )
        await mat.materialize(agent="a" * 32, resolved=resolved)
        revision = await b.agent_revision(other_actor, "a" * 32)
        await b.write_revoke(
            other_actor, "a" * 32, RevokeAbilityRequest(alias="fs", expected_revision=revision)
        )
        assert await a.grants(DEPLOY, "a" * 32)
        assert not await b.grants(other_actor, "a" * 32)
    finally:
        await b.close()
        await a.close()


@pytest.mark.parametrize(
    "command",
    [
        "git -C sub clean -fd",
        "docker -H host rmi image",
        "kubectl -n default delete pod x",
        "npm --prefix sub publish",
        "git reset --hard",
        "git push --force",
        "docker rm -f image",
        "docker system prune",
    ],
)
async def test_builtin_dangerous_commands_never_reach_runner(tmp_path, command):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        "abilities:\n  - type: conversation\n  - ref: ghrah.shell.execute_command\n"
        "    permissions:\n      allowed_commands: [git, docker, kubectl, npm]\n"
        '      denied_commands: ["git clean"]\n'
    )
    db = await AbilityAccessStore.open(
        tmp_path / "commands.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "run-one",
    )
    try:
        assembly, resolved = _resolved(text)
        runner = SimpleNamespace(execute_command=AsyncMock())
        mat = StartupGrantMaterializer(
            store=db,
            source=assembly.source,
            actor=DEPLOY,
            policy=GrantPolicy(),
            workspace_root=str(tmp_path),
            command_runner=runner,
            auto_approve_abilities=("execute_command",),
        )
        table = await mat.materialize(agent="a" * 32, resolved=resolved)
        await mat.bind_run("a" * 32, table)
        tool = next(t for t in table.abilities if t.name == "execute_command")
        executor = LocalAbilityExecutor("planner", hooks=tool.get_hooks(), hitl_timeout=0.01)
        denied = await executor.execute_ability(
            tool, make_call(arguments={"command": command}), make_services()
        )
        assert denied.outcome is ActionOutcome.FAILURE
        assert not runner.execute_command.called and not executor.hitl_store.list_pending()
    finally:
        await db.close()


async def test_move_file_succeeds_without_readonly_template(tmp_path):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        "abilities:\n  - type: conversation\n  - ref: ghrah.fs.move_file\n"
    )
    db = await AbilityAccessStore.open(
        tmp_path / "move.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda _: "run-one",
    )
    try:
        mat, table = await materialize(db, tmp_path, text)
        await mat.bind_run("a" * 32, table)
        tool = next(t for t in table.abilities if t.name == "move_file")
        src, dst = tmp_path / "source.txt", tmp_path / "dest.txt"
        src.write_text("movable")
        executor = LocalAbilityExecutor("planner", hooks=tool.get_hooks())
        result = await executor.execute_ability(
            tool,
            make_call(
                arguments={
                    "source_path": str(src),
                    "destination_path": str(dst),
                }
            ),
            make_services(),
        )
        assert result.outcome is ActionOutcome.SUCCESS, result.data
        assert not src.exists() and dst.read_text() == "movable"
    finally:
        await db.close()


async def test_raw_builtin_effects_obey_readonly_checker(tmp_path):
    from ghrah.ability.builtin.fs.operations.edit_file import EditFileAbility
    from ghrah.ability.builtin.fs.operations.move_file import MoveFileAbility
    from ghrah.ability.permissions.fs_permissions import FSPermissionChecker

    src, dst = tmp_path / "src.txt", tmp_path / "dst.txt"
    src.write_text("original")
    checker = FSPermissionChecker(workspace_root=str(tmp_path), read_only=True)
    for ability, args in [
        (
            EditFileAbility(permission_checker=checker),
            {"file_path": str(src), "old_str": "original", "new_str": "changed"},
        ),
        (
            MoveFileAbility(permission_checker=checker),
            {"source_path": str(src), "destination_path": str(dst)},
        ),
    ]:
        denied = await ability.execute(make_call(arguments=args), make_services())
        assert denied.outcome is ActionOutcome.FAILURE
        assert "read-only" in denied.data["error"]
        assert src.read_text() == "original" and not dst.exists()


async def test_startup_claims_exclude_other_connections_and_rollback_failed_grants(tmp_path):
    from ghrah.ability.access import AccessError

    path = tmp_path / "registration.db"
    a = await AbilityAccessStore.open(
        path, "project", operators=frozenset({"deployment"}), runs=lambda _: "a-run"
    )
    b = await AbilityAccessStore.open(
        path, "project", operators=frozenset({"deployment"}), runs=lambda _: "b-run"
    )
    try:
        async with a.startup_registration(DEPLOY, "a" * 32):
            await materialize(a, tmp_path)
            with pytest.raises(AccessError, match="conflict"):
                async with b.startup_registration(DEPLOY, "a" * 32):
                    pytest.fail("duplicate owner entered")
            # 未标记提交，模拟绑定失败。
        with pytest.raises(AccessError, match="not_found"):
            await a.grants(DEPLOY, "a" * 32)
        async with b.startup_registration(DEPLOY, "a" * 32) as registration:
            await materialize(b, tmp_path)
            registration.committed = True
        with pytest.raises(AccessError, match="conflict"):
            async with a.startup_registration(DEPLOY, "a" * 32):
                pytest.fail("active owner stolen")
        await b.release_registration(DEPLOY, "a" * 32)
        assert await b.grants(DEPLOY, "a" * 32)
    finally:
        await b.close()
        await a.close()


@pytest.mark.parametrize("marker", [False, True])
async def test_old_or_unmarked_domain_is_not_silently_adopted(tmp_path, marker):
    path = tmp_path / "unmarked.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE ability_agents(project TEXT,id TEXT,revision INTEGER)")
        db.execute("INSERT INTO ability_agents VALUES('project','name',1)")
        if marker:
            db.execute("CREATE TABLE ability_tree_schema(version INTEGER)")
            db.execute("INSERT INTO ability_tree_schema VALUES(4)")
    with pytest.raises(ValueError):
        await AbilityAccessStore.open(
            path, "project", operators=frozenset({"deployment"}), runs=lambda _: "run"
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM ability_agents").fetchone() == ("name",)


@pytest.mark.parametrize("inherited_context", [False, True])
async def test_failed_startup_does_not_undo_concurrent_admin_revoke(tmp_path, inherited_context):
    from ghrah.ability.access import AccessError

    a = await AbilityAccessStore.open(
        tmp_path / "cas.db", "project", operators=frozenset({"deployment"}), runs=lambda _: "run"
    )
    b = await AbilityAccessStore.open(
        tmp_path / "cas.db", "project", operators=frozenset({"deployment"}), runs=lambda _: "run"
    )
    try:
        with pytest.raises(AccessError, match="conflict"):
            async with a.startup_registration(DEPLOY, "a" * 32):
                await materialize(a, tmp_path)
                revision = await b.agent_revision(DEPLOY, "a" * 32)
                writer = a if inherited_context else b
                await asyncio.create_task(
                    writer.write_revoke(
                        DEPLOY,
                        "a" * 32,
                        RevokeAbilityRequest(alias="fs", expected_revision=revision),
                    )
                )
        assert not await b.grants(DEPLOY, "a" * 32)
        with pytest.raises(AccessError, match="conflict"):
            async with b.startup_registration(DEPLOY, "a" * 32):
                pytest.fail("conflicted startup must require explicit recovery")
    finally:
        await b.close()
        await a.close()
