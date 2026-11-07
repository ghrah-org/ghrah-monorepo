# SPDX-License-Identifier: Apache-2.0
"""真实授权库、安装包、执行器与 receive 循环的渐进工具面验收。"""

import asyncio
from dataclasses import replace

import pytest
from _helpers import ToolScriptedLLM
from ghrah.protocol.ability_tree import RevokeAbilityRequest

from ghrah.ability.access import AbilityAccessStore, AccessIdentity, GrantPolicy, GrantRule
from ghrah.ability.access.startup import StartupGrantMaterializer
from ghrah.ability.assembly import AbilityAssembly
from ghrah.ability.execution.hooks import Hook, HookPoint, HookResult
from ghrah.ability.package.generate import build
from ghrah.ability.package.installer import AbilityInstaller
from ghrah.ability.surface.config import SurfaceConfig
from ghrah.ability.surface.runtime import _cost
from ghrah.agents.builder import AgentBuilder
from ghrah.chat.content import ToolCallBlock
from ghrah.core.events import HITLRequestEvent
from ghrah.core.message import AgentMessage, MessageType
from ghrah.manifest.parser import parse_agent_manifest
from ghrah.types.results import ActionOutcome, ActionResult

AGENT = "a" * 32
ADMIN = AccessIdentity("project", "deployment", "control", True, "deployment")
MODULE = """from pydantic import BaseModel, ConfigDict
from ghrah.types.results import ActionResult, ActionOutcome
class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int
class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int
TITLE = "Calculate value"
SUMMARY = "Calculate a numbered value"
TAGS = ["calculate"]
EFFECTS = []
PERMISSIONS = {}
TIMEOUT_MS = 1000
async def execute(call, services):
    return ActionResult(ActionOutcome.SUCCESS, {"value": call.arguments["value"] + NUMBER})
"""
MANIFEST = """manifest: agent
version: '2'
metadata: {namespace: demo, name: consumer}
model: {endpoint_name: default}
system_prompt: Use tools then reply.
max_iterations: 10
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
  - ref: ghrah.fs.write_file
    permissions:
      allowed_paths: [./approved]
surface:
  mode: progressive
  static: [ghrah.fs.read_file]
  max_static: 12
"""


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """一个真实安装包提供 50 个长尾 Operation 和一个登记脚本。"""
    root = tmp_path_factory.mktemp("surface-package")
    source = root / "source"
    (source / "operations").mkdir(parents=True)
    (source / "ability.yaml").write_text(
        "format: ghrah.ability-definition\nschema_version: '1'\n"
        "identity: {namespace: acme, name: tools, version: '1.0.0'}\n"
        "title: Tools\ndescription: Surface fixture\n",
        encoding="utf-8",
        newline="\n",
    )
    for number in range(50):
        (source / "operations" / f"op_{number:03}.py").write_text(
            MODULE.replace("NUMBER", str(number)), encoding="utf-8", newline="\n"
        )
    skill = source / "skills" / "guide"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guide\ndescription: Surface scripts\n---\n# Guide\n",
        encoding="utf-8",
        newline="\n",
    )
    (skill / "scripts" / "export.py").write_text(
        "import json, sys\ne=json.load(sys.stdin)\n"
        'json.dump({"ok":True,"data":{"call_id":e["call"]["tool_call_id"]}},sys.stdout)\n',
        encoding="utf-8",
        newline="\n",
    )
    build(source)
    installer = AbilityInstaller(root / "installed")
    installer.install(source)
    return installer


@pytest.fixture
async def environment(tmp_path, installed):
    """以生产物化器与 Builder 接通单个 Agent，身份与服务均来自装配边界。"""
    runs = {AGENT: "run-one"}
    store = await AbilityAccessStore.open(
        tmp_path / "surface.db", "project", operators=frozenset({"deployment"}), runs=runs.get
    )
    assembly = AbilityAssembly.select("builtin", install_root=str(installed.root))
    resolved = assembly.source.resolver().resolve(parse_agent_manifest(MANIFEST))
    resolved.config = replace(resolved.config, agent_id=AGENT, workspace_root=str(tmp_path))
    materializer = StartupGrantMaterializer(
        store=store,
        source=assembly.source,
        actor=ADMIN,
        policy=GrantPolicy(
            rules=(
                GrantRule(package="acme.tools"),
                GrantRule(package="acme.tools", skill="guide", script="export", available=True),
            )
        ),
        workspace_root=str(tmp_path),
        surface_loaded_limit=2,
    )
    try:
        result = await materializer.materialize(agent=AGENT, resolved=resolved)
        await materializer.bind_run(AGENT, result)
        yield store, materializer, result, resolved.config, runs
    finally:
        await materializer.close_agent_resources(AGENT)
        await store.close()


class Model(ToolScriptedLLM):
    """按轮次记录模型实际看到的工具数组。"""

    model_capabilities = {"max_context_length": 128000}

    def __init__(self, rounds=()):
        super().__init__(list(rounds))
        self.tool_history = []

    async def generate(self, messages, tools=None):
        self.tool_history.append([item["function"]["name"] for item in self.tools])
        return await super().generate(messages, tools)


def make_agent(environment, model=None, publisher=None):
    _, _, result, config, _ = environment
    return AgentBuilder.from_config(
        config,
        abilities=result.abilities,
        llm_factory=lambda _: model or Model(),
        persistence_factory=lambda _: None,
        event_publisher=publisher,
    )


async def invoke(agent, name, arguments, call_id="test-call"):
    """与模型原生调用共用完整分发，包括成员路径解析。"""
    results = await agent._ability_executor.execute_tool_calls(
        tool_calls=[ToolCallBlock(id=call_id, name=name, arguments=arguments)],
        abilities=agent._abilities,
        accumulated_data={},
        context_manager=agent._context_manager,
        invocation_factory=agent._build_ability_call,
    )
    return results[0]["action_result"]


async def test_receive_search_describe_execute_and_result_roundtrip(environment):
    model = Model(
        [
            [ToolCallBlock(id="search", name="search_tools", arguments={"query": "op_049"})],
            [ToolCallBlock(id="describe", name="describe_tool", arguments={"name": "op_049"})],
            [
                ToolCallBlock(
                    id="execute",
                    name="execute_tool",
                    arguments={"name": "op_049", "arguments": {"value": 7}},
                )
            ],
        ]
    )
    agent = make_agent(environment, model)
    await agent.receive(
        AgentMessage(
            sender="user",
            recipient="consumer",
            content="Calculate 49 + 7",
            type=MessageType.CHAT,
        )
    )
    assert len(model.calls) == 4
    assert "op_049" not in model.tool_history[0]
    assert "op_049" not in model.tool_history[1]
    assert "op_049" in model.tool_history[2]
    assert "search_tools" in model.tool_history[0]
    assert "run_script" in model.tool_history[0]
    assert max(map(len, model.tool_history)) <= 7
    assert all(
        any("use search_tools" in item.text.lower() for item in messages)
        for messages in model.calls
    )
    assert any(
        "56" in getattr(block, "content", "")
        for item in model.calls[3]
        for block in item.content_blocks
    )
    for call_id, messages in zip(("search", "describe", "execute"), model.calls[1:]):
        assert any(
            getattr(block, "tool_call_id", None) == call_id
            for message in messages
            for block in message.content_blocks
        )
    nodes = agent._context_manager.chain.nodes
    audit = [row for node in nodes for row in node.metadata.get("tool_surface_calls", [])]
    assert any(row["kind"] == "meta.call" and row["name"] == "execute_tool" for row in audit)
    assert any(
        row["kind"] == "tool.call" and row["name"] == "op_049" and row["tool_call_id"] == "execute"
        for row in audit
    )


@pytest.mark.parametrize("name", ["unknown", "execute_tool", "search_tools", "run_script"])
async def test_unknown_ungranted_and_meta_targets_are_rejected(environment, name):
    agent = make_agent(environment)
    for entry, arguments in (
        ("describe_tool", {"name": name}),
        ("execute_tool", {"name": name, "arguments": {}}),
    ):
        result = await invoke(agent, entry, arguments)
        assert result.outcome == ActionOutcome.FAILURE
        assert "not found" in result.data["error"].lower()


async def test_lru_keeps_retained_order_and_prunes_revocation(environment):
    store, _, result, _, _ = environment
    agent = make_agent(environment)
    surface = result.abilities.tool_surface
    await surface.refresh(128000)
    for name in ("op_001", "op_002", "op_001", "op_003"):
        assert (
            await invoke(agent, "describe_tool", {"name": name})
        ).outcome == ActionOutcome.SUCCESS
    names = [item["function"]["name"] for item in await surface.refresh(128000)]
    assert [name for name in names if name.startswith("op_")] == ["op_001", "op_003"]
    revision = await store.agent_revision(ADMIN, AGENT)
    await store.revoke(
        ADMIN,
        AGENT,
        RevokeAbilityRequest(
            alias="tools", members=["operations/op_001"], expected_revision=revision
        ),
    )
    names = [item["function"]["name"] for item in await surface.refresh(128000)]
    assert "op_001" not in names and "op_003" in names
    assert (
        await invoke(agent, "execute_tool", {"name": "op_001", "arguments": {"value": 1}})
    ).outcome == ActionOutcome.FAILURE


async def test_native_and_wrapped_results_and_validation_are_identical(environment):
    agent = make_agent(environment)
    wrapped = await invoke(agent, "execute_tool", {"name": "op_049", "arguments": {"value": 7}})
    native = await invoke(agent, "op_049", {"value": 7})
    assert wrapped == native
    invalid = await invoke(agent, "execute_tool", {"name": "op_049", "arguments": {"bad": True}})
    assert invalid.outcome == ActionOutcome.FAILURE
    # 内置 Input 同样在任何效果与审批前验证。
    invalid = await invoke(agent, "execute_tool", {"name": "write_file", "arguments": {}})
    assert invalid.outcome == ActionOutcome.FAILURE


async def test_script_result_enters_next_receive_input(environment):
    model = Model(
        [
            [
                ToolCallBlock(
                    id="script-id",
                    name="run_script",
                    arguments={"script": "skills/guide/scripts/export", "arguments": {}},
                )
            ]
        ]
    )
    agent = make_agent(environment, model)
    await agent.receive(
        AgentMessage(sender="user", recipient="consumer", content="Export", type=MessageType.CHAT)
    )
    assert any(
        "script.call" in getattr(block, "content", "")
        and "script-id" in getattr(block, "content", "")
        for message in model.calls[1]
        for block in message.content_blocks
    )
    denied = await invoke(agent, "run_script", {"script": "../../bin/sh", "arguments": {}})
    assert denied.outcome == ActionOutcome.FAILURE


async def test_old_run_cannot_use_any_surface_entry(environment):
    _, _, result, _, runs = environment
    agent = make_agent(environment)
    await invoke(agent, "describe_tool", {"name": "op_001"})
    runs[AGENT] = "run-two"
    assert (await invoke(agent, "search_tools", {"query": "op"})).outcome == ActionOutcome.FAILURE
    assert (
        await invoke(
            agent, "run_script", {"script": "skills/guide/scripts/export", "arguments": {}}
        )
    ).outcome == ActionOutcome.FAILURE
    await agent.refresh_tool_projection()
    assert not agent._bound_tools


async def test_cancelled_script_propagates_through_receive(environment, monkeypatch):
    """循环取消必须传到脚本入口，不能转成一次普通失败后继续问模型。"""
    started = asyncio.Event()
    received = {}

    async def blocked_script(**kwargs):
        received.update(kwargs)
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("ghrah.ability.surface.runtime.execute_run_script", blocked_script)
    model = Model(
        [
            [
                ToolCallBlock(
                    id="cancel-script",
                    name="run_script",
                    arguments={"script": "skills/guide/scripts/export", "arguments": {}},
                )
            ]
        ]
    )
    agent = make_agent(environment, model)
    task = asyncio.create_task(
        agent.receive(
            AgentMessage(
                sender="user", recipient="consumer", content="Export", type=MessageType.CHAT
            )
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert received["tool_call_id"] == "cancel-script"
    assert received["actor"].subject_id == AGENT
    assert len(model.calls) == 1


async def test_run_end_clears_loaded_state_without_revoking_grants(environment):
    store, materializer, result, _, _ = environment
    agent = make_agent(environment)
    await invoke(agent, "describe_tool", {"name": "op_001"})
    surface = result.abilities.tool_surface
    assert surface._loaded
    before = await store.agent_revision(ADMIN, AGENT)
    await materializer.close_agent_resources(AGENT)
    assert not surface._loaded and surface.closed
    assert (await invoke(agent, "search_tools", {"query": "op"})).outcome == ActionOutcome.FAILURE
    assert await store.agent_revision(ADMIN, AGENT) == before


async def test_nested_deferred_keeps_target_and_wrapper_post_hooks():
    """延迟回复到达时，目标与包装的 POST 各执行一次，不被同一消息键覆盖。"""
    from ghrah.ability.execution.base import Ability
    from ghrah.ability.execution.execution_services import ExecutionServices
    from ghrah.ability.execution.executor import LocalAbilityExecutor
    from ghrah.ability.execution.invocation import OperationCall

    seen = []

    class PostHook(Hook):
        hook_point = HookPoint.POST_EXECUTE

        async def should_trigger(self, call, services):
            return True

        async def execute(self, call, services, result=None):
            seen.append(call.member.member)
            return HookResult.continue_()

    executor = LocalAbilityExecutor("consumer", hooks=[PostHook()])

    class Target(Ability):
        name = "target"

        def get_hooks(self):
            return []

        async def execute(self, call, services):
            return ActionResult(ActionOutcome.SUCCESS, {"await_reply": {"message_id": "deferred"}})

    class Wrapper(Target):
        name = "wrapper"

        async def execute(self, call, services):
            return await executor.execute_routed(Target(), call, services)

    call, services = OperationCall(tool_call_id="deferred-call"), ExecutionServices()
    await executor.execute_ability(Wrapper(), call, services)
    assert not seen
    await executor.finish_deferred(
        "deferred", call, services, ActionResult(ActionOutcome.SUCCESS, {})
    )
    assert seen == ["target", "wrapper"]


async def test_auto_budget_and_explicit_meta_revoke_fall_back_to_static(environment):
    store, _, result, _, _ = environment
    surface = result.abilities.tool_surface
    surface.config = SurfaceConfig(max_static=12)
    tools = await surface.refresh(128000)
    names = [item["function"]["name"] for item in tools]
    assert len(names) <= 16  # 12 个常驻、3 个元工具、1 个脚本入口。
    assert _cost(tools) <= 128000 * 0.02
    assert "search_tools" in names
    revision = await store.agent_revision(ADMIN, AGENT)
    await store.revoke(
        ADMIN,
        AGENT,
        RevokeAbilityRequest(
            alias="core", members=["operations/search_tools"], expected_revision=revision
        ),
    )
    names = [item["function"]["name"] for item in await surface.refresh(128000)]
    assert "search_tools" not in names and "execute_tool" not in names
    assert len([name for name in names if name.startswith("op_")]) == 50
    assert not surface.progressive
    # 每轮刷新不重新授予已显式撤销的系统成员。
    assert await store.agent_revision(ADMIN, AGENT) == revision + 1


async def test_describe_keeps_unmaterialized_contract_but_execute_refuses(environment):
    _, _, result, _, _ = environment
    agent = make_agent(environment)
    result.abilities[:] = [tool for tool in result.abilities if tool.name != "op_049"]
    described = await invoke(agent, "describe_tool", {"name": "op_049"})
    assert described.outcome == ActionOutcome.SUCCESS
    assert described.data["available"] is False
    assert "value" in described.data["input_schema"]["properties"]
    executed = await invoke(agent, "execute_tool", {"name": "op_049", "arguments": {"value": 7}})
    assert executed.outcome == ActionOutcome.FAILURE
    assert "unavailable" in executed.data["error"]


async def test_unavailable_version_stays_describable_but_not_natively_visible(environment):
    """来源离线不撤销授权：仍可查看已提交契约，不能借旧对象执行。"""
    store, _, result, _, _ = environment
    agent = make_agent(environment)
    store.set_version_availability(
        lambda version: version.definition().identity.namespace == "ghrah"
    )
    searched = await invoke(agent, "search_tools", {"query": "op_049"})
    assert any(row["name"] == "op_049" for row in searched.data["tools"])
    described = await invoke(agent, "describe_tool", {"name": "op_049"})
    assert described.outcome == ActionOutcome.SUCCESS and described.data["available"] is False
    assert "value" in described.data["input_schema"]["properties"]
    executed = await invoke(agent, "execute_tool", {"name": "op_049", "arguments": {"value": 7}})
    assert executed.outcome == ActionOutcome.FAILURE
    names = [
        item["function"]["name"] for item in await result.abilities.tool_surface.refresh(128000)
    ]
    assert "op_049" not in names


async def test_meta_only_agent_cannot_borrow_another_agents_members(environment, tmp_path):
    store, materializer, _, _, runs = environment
    bob = "b" * 32
    runs[bob] = "bob-run"
    assembly = AbilityAssembly.select("builtin")
    resolved = assembly.source.resolver().resolve(parse_agent_manifest(MANIFEST))
    resolved.config = replace(resolved.config, agent_id=bob, workspace_root=str(tmp_path))
    resolved.surface = SurfaceConfig(mode="progressive")
    restricted = StartupGrantMaterializer(
        store=store,
        source=materializer._source,
        actor=ADMIN,
        policy=GrantPolicy(
            mode="replace", rules=(GrantRule(package="ghrah.core", operation="execute_tool"),)
        ),
        workspace_root=str(tmp_path),
    )
    try:
        result = await restricted.materialize(agent=bob, resolved=resolved)
        await restricted.bind_run(bob, result)
        agent = AgentBuilder.from_config(
            resolved.config,
            abilities=result.abilities,
            llm_factory=lambda _: Model(),
            persistence_factory=lambda _: None,
        )
        assert (await invoke(agent, "search_tools", {"query": "op_049"})).data["tools"] == []
        assert (
            await invoke(agent, "describe_tool", {"name": "op_049"})
        ).outcome == ActionOutcome.FAILURE
        assert (
            await invoke(agent, "execute_tool", {"name": "op_049", "arguments": {"value": 7}})
        ).outcome == ActionOutcome.FAILURE
    finally:
        await restricted.close_agent_resources(bob)


class CounterHook(Hook):
    """只观察真实目标，包装调用有自己的独立审计。"""

    def __init__(self, point):
        self.hook_point = point
        self.calls = []

    async def should_trigger(self, call, services):
        return call.member.member == "write_file"

    async def execute(self, call, services, result=None):
        self.calls.append(call)
        return HookResult()


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_hitl_real_member_and_target_hook_counts(environment, tmp_path, approved, wrapped):
    events = []

    class Publisher:
        async def publish(self, event):
            events.append(event)
            if isinstance(event, HITLRequestEvent):
                import asyncio

                asyncio.get_running_loop().call_soon(
                    agent._ability_executor.receive_hitl_response,
                    event.ability_name,
                    event.context["tool_call_id"],
                    approved,
                    {"reason": "deny"},
                )

    agent = make_agent(environment, publisher=Publisher())
    pre, post = CounterHook(HookPoint.PRE_EXECUTE), CounterHook(HookPoint.POST_EXECUTE)
    agent._all_hooks = [pre, post]
    arguments = {"file_path": "new.txt", "content": "controlled"}
    result = await invoke(
        agent,
        "execute_tool" if wrapped else "write_file",
        {"name": "write_file", "arguments": arguments} if wrapped else arguments,
        "approval-id",
    )
    approvals = [event for event in events if isinstance(event, HITLRequestEvent)]
    assert len(approvals) == 1 and approvals[0].ability_name == "write_file", result
    assert approvals[0].context["tool_call_id"] == "approval-id"
    assert len(pre.calls) == 1 and len(post.calls) == int(approved)
    if approved:
        assert result.outcome == ActionOutcome.SUCCESS
        assert (tmp_path / "new.txt").read_text() == "controlled"
    else:
        assert result.data["hitl_rejected"]
        assert not (tmp_path / "new.txt").exists()
