# SPDX-License-Identifier: Apache-2.0
"""启动物化：包实例登记、授权核定、工具选择与定义面 schema 投影。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _helpers import make_call, make_services
from ghrah.protocol.ability_tree import (
    BindToolRequest,
    RevokeAbilityRequest,
    UnbindToolRequest,
)

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessIdentity,
    GrantPolicy,
    GrantRule,
    StartupGrantError,
    StartupGrantMaterializer,
)
from ghrah.ability.assembly import AbilityAssembly
from ghrah.manifest.parser import parse_agent_manifest

AGENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: consumer
  description: W1 startup materialization
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
  - ref: ghrah.fs.list_directory
"""

AGENT_CORE_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: consumer
  description: W1 startup materialization
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - ref: ghrah.core.list_abilities
"""

AGENT_CONTROL_ONLY = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: loop
  description: control only
model:
  endpoint_name: default
system_prompt: loop
max_iterations: 5
abilities:
  - type: conversation
  - type: end_task
"""

DEPLOY = AccessIdentity("project", "deployment", "control", True, "deployment")

AGENT_SCOPED_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: consumer
  description: W1 scoped permissions
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
    permissions:
      allowed_paths:
        - ./src
      require_hitl: true
"""

AGENT_UNSCOPED_HITL_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w1
  name: consumer
  description: W1 declared hitl without narrowing
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
    permissions:
      require_hitl: true
"""


def _resolved(text: str):
    """解析并解析 Agent 声明；只使用新来源，不接旧注册表。"""
    manifest = parse_agent_manifest(text)
    assembly = AbilityAssembly.select("builtin")
    return assembly, assembly.source.resolver().resolve(manifest)


@pytest.fixture
async def store(tmp_path: Path):
    """打开项目关系库并交给启动物化层。"""
    db = await AbilityAccessStore.open(
        tmp_path / "project.db",
        "project",
        operators=frozenset({"deployment", "migration"}),
        runs=lambda agent: f"{agent}-run",
    )
    try:
        yield db
    finally:
        await db.close()


def _materializer(db, assembly, tmp_path: Path | None, policy=GrantPolicy(), **kwargs):
    """构造启动物化器；默认空策略（用模板结果）与临时工作区。"""

    class RegisteredMaterializer(StartupGrantMaterializer):
        async def materialize(self, **kwargs):
            result = await super().materialize(**kwargs)
            await self.bind_run(kwargs["agent"], result)
            return result

    return RegisteredMaterializer(
        store=db,
        source=assembly.source,
        actor=DEPLOY,
        policy=policy,
        workspace_root=str(tmp_path) if tmp_path is not None else None,
        **kwargs,
    )


async def test_template_grants_requested_members_and_projects_tools(store, tmp_path):
    """模板申请的两个成员被授予并选定；工具 schema 来自定义面。"""
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    assert [binding.member for binding in result.tools] == ["list_directory", "read_file"]
    assert [binding.tool_name for binding in result.tools] == ["list_directory", "read_file"]
    assert [item.name for item in result.abilities] == [
        "conversation",
        "list_directory",
        "read_file",
    ]
    read_file = result.abilities[-1]
    schema = read_file.bind_tool()
    assert schema["function"]["name"] == "read_file"
    assert schema["function"]["parameters"]["properties"]["file_path"]["type"] == "string"

    records = await store.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert [record.alias for record in records] == ["fs"]
    assert records[0].members == ("list_directory", "read_file")
    assert records[0].source == "deployment"
    assert list(await store.tool_names(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")) == [
        "list_directory",
        "read_file",
    ]
    assert (await store.list_instances(DEPLOY)) == ["ghrah-fs"]


async def test_projected_ability_executes_read_file(store, tmp_path):
    """包装层不改变执行语义：真实读取仍走原执行实例。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target)}), make_services()
    )
    assert outcome.outcome.value == "success"
    assert outcome.data["content"] == "w1 content"


async def test_replace_policy_grants_whole_package(store, tmp_path):
    """replace 模式下策略给出完整集合；整包展开带来模板未申请的工具。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(mode="replace", rules=(GrantRule(package="ghrah.fs"),))
    result = await _materializer(store, assembly, tmp_path, policy).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    names = {item.name for item in result.abilities}
    assert {"read_file", "write_file", "edit_file", "list_directory"} <= names
    assert "conversation" in names
    record = (await store.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))[0]
    assert "write_file" in record.members


async def test_merge_policy_revokes_a_requested_member(store, tmp_path):
    """merge 移除后，目标成员既不在授权里也不在工具表里。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(
        rules=(GrantRule(package="ghrah.fs", operation="list_directory", effect="revoke"),)
    )
    result = await _materializer(store, assembly, tmp_path, policy).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    assert [binding.member for binding in result.tools] == ["read_file"]
    assert [item.name for item in result.abilities] == ["conversation", "read_file"]


async def test_materialize_is_idempotent(store, tmp_path):
    """重复启动物化不产生新历史，工具映射保持稳定。"""
    assembly, resolved = _resolved(AGENT_YAML)
    materializer = _materializer(store, assembly, tmp_path)
    first = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    before = await store.changes(DEPLOY)
    second = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    assert second.granted == () and second.revoked == ()
    assert second.revision == first.revision
    assert await store.changes(DEPLOY) == before
    assert [binding.tool_name for binding in second.tools] == [
        binding.tool_name for binding in first.tools
    ]


async def test_next_manifest_revokes_stale_package_and_unbinds_tools(store, tmp_path):
    """下一次启动只保留目标包；旧包授权与工具选择被撤销。"""
    assembly, resolved = _resolved(AGENT_YAML)
    materializer = _materializer(store, assembly, tmp_path)
    await materializer.materialize(agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved)
    assert await store.tool_names(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == (
        "list_directory",
        "read_file",
    )

    _, core_resolved = _resolved(AGENT_CORE_YAML)
    result = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=core_resolved
    )
    assert result.revoked == ("fs",)
    assert [
        record.alias for record in await store.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    ] == ["core"]
    assert await store.tool_names(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == ("list_abilities",)


async def test_control_only_manifest_fails_closed(store, tmp_path):
    """只有循环控制成员时没有可授权的包，直接失败而非静默启动。"""
    assembly, resolved = _resolved(AGENT_CONTROL_ONLY)
    with pytest.raises(StartupGrantError, match="能力包成员"):
        await _materializer(store, assembly, tmp_path).materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )


async def test_unknown_policy_package_fails_closed(store, tmp_path):
    """策略引用本次启动未解析的包时失败，不回退模板授予。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(rules=(GrantRule(package="other.package"),))
    with pytest.raises(StartupGrantError, match="未解析的包"):
        await _materializer(store, assembly, tmp_path, policy).materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )


async def test_file_members_without_workspace_fail_closed(store):
    """文件类成员没有明确工作区时不授予，避免拿实例根当授权根。"""
    assembly, resolved = _resolved(AGENT_YAML)
    with pytest.raises(StartupGrantError, match="workspace_root"):
        await _materializer(store, assembly, None).materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )


async def test_selected_member_schema_matches_captured_definition(store, tmp_path):
    """工具 schema 与已提交版本定义一致，不从实现脚本二次读取。"""
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    version = (await store.list_versions(DEPLOY))[0]
    definition = json.loads(version.definition_json)
    schema = next(item for item in result.abilities if item.name == "read_file").bind_tool()
    assert schema["function"]["parameters"] == definition["operations"]["read_file"]["input_schema"]


async def test_revoke_after_startup_denies_next_execution(store, tmp_path):
    """撤销立即影响下一次调用：已物化的工具在执行接纳处被拒绝。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.revoke(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        RevokeAbilityRequest(alias="fs", expected_revision=revision),
    )
    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target)}), make_services()
    )
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_execution_rejects_path_outside_workspace(store, tmp_path):
    """清单允许但实际路径越界：执行接纳在读取前拒绝，没有文件效果。"""
    assembly, resolved = _resolved(AGENT_YAML)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(outside)}), make_services()
    )
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_manifest_scope_narrows_auto_approval_and_keeps_hitl_outside(store, tmp_path):
    """模板 allowed_paths 收窄自动批准范围；范围外按 require_hitl 转审批。"""
    (tmp_path / "src").mkdir()
    assembly, resolved = _resolved(AGENT_SCOPED_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    hook = read_file.get_hooks()[0]

    inside = make_call(member="read_file", arguments={"file_path": str(tmp_path / "src" / "a.txt")})
    assert await hook.should_trigger(inside, make_services()) is True
    assert (await hook.execute(inside, make_services(), None)).should_continue is True

    outside = make_call(member="read_file", arguments={"file_path": str(tmp_path / "other.txt")})
    pending = await hook.execute(outside, make_services(), None)
    assert pending.should_continue is False
    assert pending.requires_hitl is True


async def test_declared_hitl_without_narrowing_auto_approves_in_scope(store, tmp_path):
    """只声明 require_hitl、不收窄范围时，工作区内自动批准（HITL 降噪）。"""
    assembly, resolved = _resolved(AGENT_UNSCOPED_HITL_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    hook = read_file.get_hooks()[0]
    call = make_call(member="read_file", arguments={"file_path": str(tmp_path / "note.txt")})
    assert (await hook.execute(call, make_services(), None)).should_continue is True


async def test_projected_tool_name_maps_back_to_member_for_hooks(store, tmp_path):
    """表面工具名被限定后，内核 Hook 仍按成员身份裁决。"""
    assembly, resolved = _resolved(AGENT_SCOPED_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    from ghrah.ability.access.startup import ProjectedAbility

    renamed = ProjectedAbility(
        read_file._inner,
        tool_name="fs_read_file",
        member="read_file",
        tool_schema=read_file.bind_tool(),
    )
    hook = renamed.get_hooks()[0]
    call = make_call(member="read_file", arguments={"file_path": str(tmp_path / "outside.txt")})
    assert await hook.should_trigger(call, make_services()) is True
    assert (await hook.execute(call, make_services(), None)).requires_hitl is True


async def test_unbound_tool_is_rejected(store, tmp_path):
    """未选中（工具选择被移除）的引用不能执行，即使关系仍在。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.write_unbind_tool(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        UnbindToolRequest(tool_name="read_file", expected_revision=revision),
    )
    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target)}), make_services()
    )
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_missing_resource_argument_is_rejected(store, tmp_path):
    """参数错误在接纳处失败，不进入执行实现。"""
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(make_call(arguments={}), make_services())
    assert outcome.outcome.value == "failure"
    assert "Invalid arguments" in outcome.data["error"]


async def test_rebinding_tool_name_cannot_reuse_old_wrapper(store, tmp_path):
    """工具名改绑到别的成员后，旧包装对象不能按新绑定继续执行旧实现。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.write_unbind_tool(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        UnbindToolRequest(tool_name="read_file", expected_revision=revision),
    )
    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.write_bind_tool(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        BindToolRequest(
            alias="fs",
            member="list_directory",
            tool_name="read_file",
            expected_revision=revision,
        ),
    )
    outcome = await read_file.execute(
        make_call(arguments={"file_path": str(target), "dir_path": str(tmp_path)}),
        make_services(),
    )
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_core_member_is_admitted(store, tmp_path):
    """核心成员进入工具表后可以经过接纳层，不再一律 unavailable。"""
    assembly, resolved = _resolved(AGENT_CORE_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    tool = next(item for item in result.abilities if item.name == "list_abilities")
    outcome = await tool.execute(make_call(arguments={}), make_services())
    error = str(outcome.data.get("error", ""))
    assert "backend is not supported by the admission layer" not in error


async def test_migrated_agent_starts_from_existing_facts_without_policy(store, tmp_path):
    """已迁移的存量 Agent 在未配置策略时按既有事实启动，不扩权也不撤销。"""
    from ghrah.ability.access import MigrationGrant, migrate_agent

    await migrate_agent(
        store,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
        workspace_root=str(tmp_path),
    )
    revision_before = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assembly, resolved = _resolved(AGENT_YAML)

    result = await _materializer(store, assembly, tmp_path, policy=None).materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )

    # 模板还申请了 list_directory，但存量模式不自动扩权
    assert [binding.member for binding in result.tools] == ["read_file"]
    assert [item.name for item in result.abilities] == ["conversation", "read_file"]
    assert result.granted == ()
    assert result.revoked == ()
    # 修订仍会因工具选择（bind）推进，但授权事实本身没有被改写
    assert await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") >= revision_before
    records = await store.grants(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert records[0].source == "migration"
    assert records[0].members == ("read_file",)
    assert list(await store.tool_names(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")) == ["read_file"]


async def test_unconfigured_policy_without_facts_fails_closed(store, tmp_path):
    """没有策略也没有存量事实：不授予任何能力，启动显式失败。"""
    assembly, resolved = _resolved(AGENT_YAML)

    with pytest.raises(StartupGrantError, match="未配置启动授权策略"):
        await _materializer(store, assembly, tmp_path, policy=None).materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )


async def test_revoked_migrated_facts_do_not_resurrect_at_startup(store, tmp_path):
    """迁移后被撤销的授权不会在下次启动时被存量模式补回。"""
    from ghrah.ability.access import MigrationGrant, migrate_agent

    await migrate_agent(
        store,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        grants=[MigrationGrant(package="ghrah.fs", members=("read_file",))],
        workspace_root=str(tmp_path),
    )
    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.write_revoke(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        RevokeAbilityRequest(alias="fs", expected_revision=revision),
    )
    assembly, resolved = _resolved(AGENT_YAML)

    with pytest.raises(StartupGrantError, match="未配置启动授权策略"):
        await _materializer(store, assembly, tmp_path, policy=None).materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )


async def test_run_change_rejects_wrapper_from_earlier_run(tmp_path: Path) -> None:
    """受信 run 变更后，旧运行留下的包装工具不能再借用新 run 执行。

    启动装配把 run 固定进调用身份；接纳时要求当前 run 与绑定值一致。
    否则“旧对象 + 新 run”会绕过运行代次变化继续执行。
    """
    current = {"run": "alice-run-1"}
    db = await AbilityAccessStore.open(
        tmp_path / "run.db",
        "project",
        operators=frozenset({"deployment"}),
        runs=lambda agent: current["run"],
    )
    try:
        assembly, resolved = _resolved(AGENT_YAML)
        materializer = _materializer(db, assembly, tmp_path)
        result = await materializer.materialize(
            agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
        )
        await materializer.bind_run("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", result)
        target = tmp_path / "note.txt"
        target.write_text("run content", encoding="utf-8")
        read_file = next(item for item in result.abilities if item.name == "read_file")

        first = await read_file.execute(
            make_call(arguments={"file_path": str(target)}), make_services()
        )
        assert first.outcome.value == "success"

        current["run"] = "alice-run-2"

        stale = await read_file.execute(
            make_call(arguments={"file_path": str(target)}), make_services()
        )
        assert stale.outcome.value == "failure"
        assert "earlier run" in stale.data["error"]
    finally:
        await db.close()


async def test_tool_table_projector_tracks_revoke(store, tmp_path):
    """可见工具名由关系库投影重建：撤销后立即从投影消失。"""
    assembly, resolved = _resolved(AGENT_YAML)
    materializer = _materializer(store, assembly, tmp_path)
    result = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    projector = materializer.tool_table_projector("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", result)

    assert set(await projector()) == {"list_directory", "read_file"}

    revision = await store.agent_revision(DEPLOY, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    await store.write_revoke(
        DEPLOY,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        RevokeAbilityRequest(
            alias="fs", members=["operations/read_file"], expected_revision=revision
        ),
    )

    assert set(await projector()) == {"list_directory"}


@pytest.mark.parametrize("auto_approve", [(), ("write_file",)])
async def test_readonly_template_blocks_write_at_effect_boundary(store, tmp_path, auto_approve):
    from ghrah.abilities.executor import LocalAbilityExecutor

    text = AGENT_YAML.replace(
        "  - ref: ghrah.fs.list_directory",
        "  - ref: ghrah.fs.write_file\n    permissions:\n      fs_read_only: true",
    )
    assembly, resolved = _resolved(text)
    materializer = _materializer(store, assembly, tmp_path, auto_approve_abilities=auto_approve)
    table = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    await materializer.bind_run("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", table)
    tool = next(t for t in table.abilities if t.name == "write_file")
    target = tmp_path / "readonly.txt"
    executor = LocalAbilityExecutor(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", hooks=tool.get_hooks(), hitl_timeout=0.01
    )
    result = await executor.execute_ability(
        tool,
        make_call(member="write_file", arguments={"file_path": str(target), "content": "x"}),
        make_services(),
    )
    assert result.outcome.value == "failure"
    assert not target.exists()
    assert "read" in result.data["error"].lower()


async def test_generic_template_hitl_is_preserved(store, tmp_path):
    text = AGENT_YAML[: AGENT_YAML.index("abilities:")] + (
        "abilities:\n  - type: conversation\n  - ref: ghrah.cluster.query_agents\n"
        "    permissions:\n      require_hitl: true\n"
    )
    assembly, resolved = _resolved(text)
    materializer = _materializer(store, assembly, tmp_path)
    table = await materializer.materialize(
        agent="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", resolved=resolved
    )
    tool = next(t for t in table.abilities if t.name == "query_agents")
    assert tool.get_hooks()
