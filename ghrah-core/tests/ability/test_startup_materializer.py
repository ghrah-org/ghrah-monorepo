# SPDX-License-Identifier: Apache-2.0
"""启动物化：包实例登记、授权核定、工具选择与定义面 schema 投影。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ghrah.protocol.ability_tree import RevokeAbilityRequest, UnbindToolRequest

from ghrah.ability.access import (
    AbilityAccessStore,
    AccessIdentity,
    GrantPolicy,
    GrantRule,
    StartupGrantError,
    StartupGrantMaterializer,
)
from ghrah.ability.assembly import AbilityAssembly
from ghrah.ability.execution.context import AbilityExecutionContext
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
        operators=frozenset({"deployment"}),
        runs=lambda agent: f"{agent}-run",
    )
    try:
        yield db
    finally:
        await db.close()


def _materializer(db, assembly, tmp_path: Path | None, policy=GrantPolicy(), **kwargs):
    """构造启动物化器；默认空策略（用模板结果）与临时工作区。"""
    return StartupGrantMaterializer(
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
        agent="alice", resolved=resolved
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

    records = await store.grants(DEPLOY, "alice")
    assert [record.alias for record in records] == ["fs"]
    assert records[0].members == ("list_directory", "read_file")
    assert records[0].source == "deployment"
    assert list(await store.tool_names(DEPLOY, "alice")) == ["list_directory", "read_file"]
    assert (await store.list_instances(DEPLOY)) == ["ghrah-fs"]


async def test_projected_ability_executes_read_file(store, tmp_path):
    """包装层不改变执行语义：真实读取仍走原执行实例。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(AbilityExecutionContext(tool_args={"file_path": str(target)}))
    assert outcome.outcome.value == "success"
    assert outcome.data["content"] == "w1 content"


async def test_replace_policy_grants_whole_package(store, tmp_path):
    """replace 模式下策略给出完整集合；整包展开带来模板未申请的工具。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(mode="replace", rules=(GrantRule(package="ghrah.fs"),))
    result = await _materializer(store, assembly, tmp_path, policy).materialize(
        agent="alice", resolved=resolved
    )
    names = {item.name for item in result.abilities}
    assert {"read_file", "write_file", "edit_file", "list_directory"} <= names
    assert "conversation" in names
    record = (await store.grants(DEPLOY, "alice"))[0]
    assert "write_file" in record.members


async def test_merge_policy_revokes_a_requested_member(store, tmp_path):
    """merge 移除后，目标成员既不在授权里也不在工具表里。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(
        rules=(GrantRule(package="ghrah.fs", operation="list_directory", effect="revoke"),)
    )
    result = await _materializer(store, assembly, tmp_path, policy).materialize(
        agent="alice", resolved=resolved
    )
    assert [binding.member for binding in result.tools] == ["read_file"]
    assert [item.name for item in result.abilities] == ["conversation", "read_file"]


async def test_materialize_is_idempotent(store, tmp_path):
    """重复启动物化不产生新历史，工具映射保持稳定。"""
    assembly, resolved = _resolved(AGENT_YAML)
    materializer = _materializer(store, assembly, tmp_path)
    first = await materializer.materialize(agent="alice", resolved=resolved)
    before = await store.changes(DEPLOY)
    second = await materializer.materialize(agent="alice", resolved=resolved)
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
    await materializer.materialize(agent="alice", resolved=resolved)
    assert await store.tool_names(DEPLOY, "alice") == ("list_directory", "read_file")

    _, core_resolved = _resolved(AGENT_CORE_YAML)
    result = await materializer.materialize(agent="alice", resolved=core_resolved)
    assert result.revoked == ("fs",)
    assert [record.alias for record in await store.grants(DEPLOY, "alice")] == ["core"]
    assert await store.tool_names(DEPLOY, "alice") == ("list_abilities",)


async def test_control_only_manifest_fails_closed(store, tmp_path):
    """只有循环控制成员时没有可授权的包，直接失败而非静默启动。"""
    assembly, resolved = _resolved(AGENT_CONTROL_ONLY)
    with pytest.raises(StartupGrantError, match="能力包成员"):
        await _materializer(store, assembly, tmp_path).materialize(agent="alice", resolved=resolved)


async def test_unknown_policy_package_fails_closed(store, tmp_path):
    """策略引用本次启动未解析的包时失败，不回退模板授予。"""
    assembly, resolved = _resolved(AGENT_YAML)
    policy = GrantPolicy(rules=(GrantRule(package="other.package"),))
    with pytest.raises(StartupGrantError, match="未解析的包"):
        await _materializer(store, assembly, tmp_path, policy).materialize(
            agent="alice", resolved=resolved
        )


async def test_file_members_without_workspace_fail_closed(store):
    """文件类成员没有明确工作区时不授予，避免拿实例根当授权根。"""
    assembly, resolved = _resolved(AGENT_YAML)
    with pytest.raises(StartupGrantError, match="workspace_root"):
        await _materializer(store, assembly, None).materialize(agent="alice", resolved=resolved)


async def test_selected_member_schema_matches_captured_definition(store, tmp_path):
    """工具 schema 与已提交版本定义一致，不从实现脚本二次读取。"""
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
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
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, "alice")
    await store.revoke(
        DEPLOY, "alice", RevokeAbilityRequest(alias="fs", expected_revision=revision)
    )
    outcome = await read_file.execute(AbilityExecutionContext(tool_args={"file_path": str(target)}))
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_execution_rejects_path_outside_workspace(store, tmp_path):
    """清单允许但实际路径越界：执行接纳在读取前拒绝，没有文件效果。"""
    assembly, resolved = _resolved(AGENT_YAML)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(
        AbilityExecutionContext(tool_args={"file_path": str(outside)})
    )
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_manifest_scope_narrows_auto_approval_and_keeps_hitl_outside(store, tmp_path):
    """模板 allowed_paths 收窄自动批准范围；范围外按 require_hitl 转审批。"""
    (tmp_path / "src").mkdir()
    assembly, resolved = _resolved(AGENT_SCOPED_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    hook = read_file.get_hooks()[0]

    inside = AbilityExecutionContext(
        current_ability_name="read_file",
        tool_args={"file_path": str(tmp_path / "src" / "a.txt")},
    )
    assert await hook.should_trigger(inside) is True
    assert (await hook.execute(inside, None)).should_continue is True

    outside = AbilityExecutionContext(
        current_ability_name="read_file",
        tool_args={"file_path": str(tmp_path / "other.txt")},
    )
    pending = await hook.execute(outside, None)
    assert pending.should_continue is False
    assert pending.requires_hitl is True


async def test_declared_hitl_without_narrowing_auto_approves_in_scope(store, tmp_path):
    """只声明 require_hitl、不收窄范围时，工作区内自动批准（HITL 降噪）。"""
    assembly, resolved = _resolved(AGENT_UNSCOPED_HITL_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    hook = read_file.get_hooks()[0]
    context = AbilityExecutionContext(
        current_ability_name="read_file",
        tool_args={"file_path": str(tmp_path / "note.txt")},
    )
    assert (await hook.execute(context, None)).should_continue is True


async def test_projected_tool_name_maps_back_to_member_for_hooks(store, tmp_path):
    """表面工具名被限定后，内核 Hook 仍按成员身份裁决。"""
    assembly, resolved = _resolved(AGENT_SCOPED_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
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
    context = AbilityExecutionContext(
        current_ability_name="fs_read_file",
        tool_args={"file_path": str(tmp_path / "outside.txt")},
    )
    assert await hook.should_trigger(context) is True
    assert (await hook.execute(context, None)).requires_hitl is True


async def test_unbound_tool_is_rejected(store, tmp_path):
    """未选中（工具选择被移除）的引用不能执行，即使关系仍在。"""
    assembly, resolved = _resolved(AGENT_YAML)
    target = tmp_path / "note.txt"
    target.write_text("w1 content", encoding="utf-8")
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    revision = await store.agent_revision(DEPLOY, "alice")
    await store.write_unbind_tool(
        DEPLOY,
        "alice",
        UnbindToolRequest(tool_name="read_file", expected_revision=revision),
    )
    outcome = await read_file.execute(AbilityExecutionContext(tool_args={"file_path": str(target)}))
    assert outcome.outcome.value == "failure"
    assert "Access denied" in outcome.data["error"]


async def test_missing_resource_argument_is_rejected(store, tmp_path):
    """参数错误在接纳处失败，不进入执行实现。"""
    assembly, resolved = _resolved(AGENT_YAML)
    result = await _materializer(store, assembly, tmp_path).materialize(
        agent="alice", resolved=resolved
    )
    read_file = next(item for item in result.abilities if item.name == "read_file")
    outcome = await read_file.execute(AbilityExecutionContext(tool_args={}))
    assert outcome.outcome.value == "failure"
    assert "Invalid arguments" in outcome.data["error"]
