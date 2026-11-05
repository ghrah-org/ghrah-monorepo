# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""显式装配模式测试：同一 Agent 的目录、工具实例与读取服务同源。

钉住四条契约：

1. 未选择模式时保持现有运行方式（旧注册表实例化工具）；
2. 选择 ``builtin`` 后声明解析与工具实例都来自新版 ``ghrah.ability``，
   不再访问旧注册表，也不读旧 Skill 根；
3. 只属于旧模式的入口（直传 wire 定义、默认能力集、运行期注册／注销）
   在新模式下显式拒绝，而不是静默降级或悄悄换一套来源；
4. 新模式解析不到成员时直接失败，不回退到 Agent 声明存储里的能力库。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from _helpers import make_call, make_services
from test_core_unit import FakeCtx

from ghrah.ability.assembly import (
    AbilityAssembly,
    AbilityAssemblyError,
    AbilitySourceMode,
)
from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.manifest.ability import AbilityManifest
from ghrah.manifest.builtins import load_all_builtin_manifests
from ghrah.manifest.parser import parse_ability_manifest, parse_agent_manifest
from ghrah.types.results import ActionOutcome

_AGENT_YAML = """\
manifest: agent
version: "2"
metadata:
  namespace: w0
  name: consumer
  description: first consumer of the builtin ability source
model:
  endpoint_name: default
system_prompt: Use the selected tools.
max_iterations: 5
abilities:
  - type: conversation
  - type: end_task
  - ref: ghrah.fs.read_file
  - ref: ghrah.fs.write_file
  - ref: ghrah.mcp.mcp_call
"""

_AGENT_YAML_FS = """\
manifest: agent
version: "2"
metadata:
  namespace: w0
  name: fs_consumer
model:
  endpoint_name: default
system_prompt: Resolve filesystem refs.
abilities:
  - type: conversation
  - ref: ghrah.fs.read_file
  - ref: ghrah.fs.write_file
"""

_AGENT_YAML_LEGACY_REF = """\
manifest: agent
version: "2"
metadata:
  namespace: w0
  name: legacy_ref
model:
  endpoint_name: default
system_prompt: Resolve a ref that only the store knows.
abilities:
  - ref: legacy.only.ability
"""

# 旧口径能力清单：只放进 Agent 声明存储的能力库，用来证明 builtin 模式的
# ref 不会从这里取件（存储里"有"不代表新模式能解析）。
_POISON_ABILITY_YAML = """\
manifest: ability
version: "1"
metadata:
  namespace: legacy.only
  name: ability
  title: Legacy Only
  description: Only reachable through the legacy ability library
  permissions: {}
implementation:
  type: builtin
  handler: conversation
"""


class _Store:
    """最小 ManifestStoreProtocol stub（记录能力库读取，便于断言不回退）。"""

    def __init__(
        self,
        agents: dict[str, Any],
        abilities: dict[str, AbilityManifest] | None = None,
    ) -> None:
        self._agents = agents
        self._abilities = abilities or {}
        self.ability_reads: list[str] = []

    def load_agent(self, full_name: str) -> Any:
        return self._agents[full_name]

    def list_agents(self, namespace: str | None = None) -> list[str]:
        return list(self._agents)

    def list_abilities(self, namespace: str | None = None) -> list[str]:
        return list(self._abilities)

    def load_ability(self, full_name: str) -> AbilityManifest:
        self.ability_reads.append(full_name)
        return self._abilities[full_name]


def _consumer_store() -> _Store:
    """首个消费者的声明存储：Agent 声明 + 旧口径能力库（含一份 store-only 条目）。"""
    abilities = dict(load_all_builtin_manifests())
    abilities["legacy.only.ability"] = parse_ability_manifest(_POISON_ABILITY_YAML)
    return _Store(
        agents={
            "w0.consumer": parse_agent_manifest(_AGENT_YAML),
            "w0.fs-consumer": parse_agent_manifest(_AGENT_YAML_FS),
            "w0.legacy-ref": parse_agent_manifest(_AGENT_YAML_LEGACY_REF),
        },
        abilities=abilities,
    )


async def _spawn(unit: Any, payload: dict[str, Any]) -> dict[str, Any]:
    ctx = FakeCtx()
    await unit.init(ctx)
    return await unit.handle_command("spawn_agent", payload, None)


async def _abilities_of(unit: Any, name: str) -> dict[str, str]:
    handle = await unit.supervisor.get_agent_handle(name)
    return {
        ability_name: type(ability).__module__
        for ability_name, ability in handle._abilities.items()
    }


def _agent_id(name: str) -> str:
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, name).hex


def _manifest_payload(name: str, manifest_ref: str) -> dict[str, Any]:
    return {
        "project_id": "default",
        "config": {"name": name, "agent_id": _agent_id(name)},
        "manifest_ref": manifest_ref,
    }


async def _open_deployment_store(tmp_path: Path) -> Any:
    """打开最小部署关系库：部署操作员 + 运行实例权威。"""
    from ghrah.ability.access import AbilityAccessStore

    return await AbilityAccessStore.open(
        tmp_path / "ability.db",
        "default",
        operators=frozenset({"deployment"}),
        runs=lambda agent: f"{agent}-run",
    )


async def _authorized_builtin_unit(
    tmp_path: Path,
    *,
    manifest_store: Any,
    policy: Any = None,
    llm_factory: Any = None,
) -> tuple[Any, Any]:
    """构造带授权装配边界的 builtin CoreUnit（关系库 + 策略 + 工作区）。"""
    from ghrah.ability.access import GrantPolicy

    store = await _open_deployment_store(tmp_path)
    unit = create_core_unit(
        CoreUnitConfig(
            project_id="default",
            manifest_store=manifest_store,
            ability_source="builtin",
            workspace_root=str(tmp_path / "workspace"),
            ability_store=store,
            ability_grant_policy=policy if policy is not None else GrantPolicy(rules=()),
            llm_factory=llm_factory,
        )
    )
    return unit, store


# ----------------------------------------------------------------
# 模式选择本身
# ----------------------------------------------------------------


class TestModeSelection:
    def test_default_mode_is_legacy(self) -> None:
        unit = create_core_unit(CoreUnitConfig(project_id="default"))

        assert unit.ability_source == "legacy"

    def test_mode_accepts_case_and_whitespace(self) -> None:
        assert AbilitySourceMode.parse(" BUILTIN ") is AbilitySourceMode.BUILTIN

    def test_unknown_mode_is_rejected_not_guessed(self) -> None:
        with pytest.raises(AbilityAssemblyError, match="unknown ability source mode"):
            AbilityAssembly.select("new")

    async def test_invalid_mode_fails_at_assembly(self) -> None:
        unit = create_core_unit(CoreUnitConfig(project_id="default", ability_source="new"))

        with pytest.raises(AbilityAssemblyError, match="unknown ability source mode"):
            await unit.init(FakeCtx())


# ----------------------------------------------------------------
# 未选择模式：现有运行方式不变
# ----------------------------------------------------------------


class TestLegacyMode:
    async def test_manifest_ref_spawn_keeps_legacy_instances(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(CoreUnitConfig(project_id="default", manifest_store=store))

        receipt = await _spawn(unit, _manifest_payload("fs_consumer", "w0.fs-consumer"))

        assert receipt["success"], receipt.get("error")
        modules = await _abilities_of(unit, "fs_consumer")
        assert modules["read_file"].startswith("ghrah.abilities.")
        assert modules["write_file"].startswith("ghrah.abilities.")

    async def test_ref_is_read_from_the_agent_store(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(CoreUnitConfig(project_id="default", manifest_store=store))

        receipt = await _spawn(unit, _manifest_payload("legacy_ref", "w0.legacy-ref"))

        assert receipt["success"], receipt.get("error")
        assert store.ability_reads == ["legacy.only.ability"]

    async def test_direct_wire_abilities_still_work(self) -> None:
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", default_abilities=("conversation", "end_task"))
        )

        receipt = await _spawn(
            unit,
            {
                "project_id": "default",
                "config": {"name": "direct"},
                "abilities": [{"ability_type": "conversation", "params": {}}],
            },
        )

        assert receipt["success"], receipt.get("error")

    async def test_skill_root_wires_the_legacy_reader(self, tmp_path: Path) -> None:
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", skill_root=str(tmp_path / "skill-ability"))
        )

        await unit.init(FakeCtx())

        assert unit.supervisor is not None
        assert unit.supervisor.skill_provider is not None


# ----------------------------------------------------------------
# 选择新模式：目录、工具实例与读取服务同源
# ----------------------------------------------------------------


class TestBuiltinMode:
    async def test_manifest_ref_spawn_uses_new_operations(self, tmp_path: Path) -> None:
        store = _consumer_store()
        unit, ability_store = await _authorized_builtin_unit(tmp_path, manifest_store=store)
        try:
            receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

            assert receipt["success"], receipt.get("error")
            modules = await _abilities_of(unit, "consumer")
            # 工具实例经授权装配边界包装：执行实现来自新版容器，schema 与接纳
            # 固定在投影层，不再直接暴露旧注册表类模块。
            assert modules["read_file"] == "ghrah.ability.access.startup"
            assert modules["write_file"] == "ghrah.ability.access.startup"
            # mcp_call 只在新版容器定义里存在：命中它即证明 ref/type 都走新来源
            assert modules["mcp_call"] == "ghrah.ability.access.startup"
            # 声明存储里的能力库没有被读（同一模式不混第二套目录）
            assert store.ability_reads == []
        finally:
            await ability_store.close()

    async def test_spawn_without_ability_store_fails_closed(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", manifest_store=store, ability_source="builtin")
        )

        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

        # 已移除“按模板实例化”的过渡退路：未接线关系库时显式失败
        assert receipt["success"] is False
        assert "启动授权关系库" in receipt["error"]

    async def test_unknown_ref_fails_without_store_fallback(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", manifest_store=store, ability_source="builtin")
        )

        receipt = await _spawn(unit, _manifest_payload("legacy_ref", "w0.legacy-ref"))

        assert receipt["success"] is False
        assert "manifest resolve failed" in receipt["error"]
        assert store.ability_reads == []

    async def test_direct_wire_abilities_are_refused(self) -> None:
        unit = create_core_unit(CoreUnitConfig(project_id="default", ability_source="builtin"))

        receipt = await _spawn(
            unit,
            {
                "project_id": "default",
                "config": {"name": "direct"},
                "abilities": [{"ability_type": "conversation", "params": {}}],
            },
        )

        assert receipt["success"] is False
        assert "legacy registry" in receipt["error"]

    async def test_spawn_without_manifest_is_refused(self) -> None:
        unit = create_core_unit(CoreUnitConfig(project_id="default", ability_source="builtin"))

        receipt = await _spawn(unit, {"project_id": "default", "config": {"name": "no-manifest"}})

        assert receipt["success"] is False
        assert "default ability set" in receipt["error"]

    async def test_register_and_unregister_are_refused(self, tmp_path: Path) -> None:
        store = _consumer_store()
        unit, ability_store = await _authorized_builtin_unit(tmp_path, manifest_store=store)
        await unit.init(FakeCtx())
        spawn = await unit.handle_command(
            "spawn_agent", _manifest_payload("consumer", "w0.consumer"), None
        )
        assert spawn["success"], spawn.get("error")
        agent_id = spawn["data"]["agent_id"]

        register = await unit.handle_command(
            "register_ability",
            {
                "project_id": "default",
                "agent_id": agent_id,
                "agent_name": "consumer",
                "ability": {"ability_type": "conversation", "params": {}},
            },
            None,
        )
        unregister = await unit.handle_command(
            "unregister_ability",
            {
                "project_id": "default",
                "agent_id": agent_id,
                "agent_name": "consumer",
                "ability_name": "read_file",
            },
            None,
        )

        assert register["success"] is False
        assert "access store" in register["error"]
        assert unregister["success"] is False
        assert "access store" in unregister["error"]
        # 拒绝发生在改动之前：工具表保持 manifest 投影（撤销成员不在其中）
        assert "read_file" in await _abilities_of(unit, "consumer")
        await ability_store.close()

    async def test_skill_root_is_not_read(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        root = tmp_path / "skill-ability"
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", skill_root=str(root), ability_source="builtin")
        )

        caplog.set_level(logging.WARNING, logger="ghrah.core.unit")
        await unit.init(FakeCtx())

        assert unit.supervisor is not None
        assert unit.supervisor.skill_provider is None
        assert "ignores skill_root" in caplog.text
        # 装配不创建旧目录：新模式不读它，就不该让它看起来"已就绪"
        assert not root.exists()


# ----------------------------------------------------------------
# 工具实例来自新来源后，现有执行器仍能驱动它（W0 只锁装配，不改循环）
# ----------------------------------------------------------------


async def test_builtin_operation_runs_through_the_agent_executor(tmp_path: Path) -> None:
    store = _consumer_store()
    unit, ability_store = await _authorized_builtin_unit(tmp_path, manifest_store=store)
    try:
        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))
        assert receipt["success"], receipt.get("error")
        handle = await unit.supervisor.get_agent_handle("consumer")

        target = tmp_path / "workspace" / "note.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("builtin source content", encoding="utf-8")
        result = await handle._ability_executor.execute_ability(
            handle._abilities["read_file"],
            make_call(arguments={"file_path": str(target)}),
            make_services(),
        )

        assert result.outcome is ActionOutcome.SUCCESS
        assert result.data["content"] == "builtin source content"
    finally:
        await ability_store.close()


# ----------------------------------------------------------------
# 启动授权关系库接线：工具表只投影获授成员
# ----------------------------------------------------------------


class TestBuiltinGrantMaterialization:
    """注入关系库后，builtin 模式的工具表由启动核定决定。"""

    async def test_granted_members_only_reach_the_tool_table(self, tmp_path: Path) -> None:
        from ghrah.ability.access import (
            AbilityAccessStore,
            GrantPolicy,
            GrantRule,
        )

        store = await AbilityAccessStore.open(
            tmp_path / "ability.db",
            "default",
            operators=frozenset({"deployment"}),
            runs=lambda agent: f"{agent}-run",
        )
        try:
            policy = GrantPolicy(
                rules=(GrantRule(package="ghrah.fs", operation="write_file", effect="revoke"),)
            )
            unit = create_core_unit(
                CoreUnitConfig(
                    project_id="default",
                    manifest_store=_consumer_store(),
                    ability_source="builtin",
                    workspace_root=str(tmp_path / "workspace"),
                    ability_store=store,
                    ability_grant_policy=policy,
                )
            )

            receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

            assert receipt["success"], receipt.get("error")
            abilities = await _abilities_of(unit, "consumer")
            assert abilities["read_file"] == "ghrah.ability.access.startup"
            assert abilities["mcp_call"] == "ghrah.ability.access.startup"
            assert "write_file" not in abilities
        finally:
            await store.close()

    async def test_unavailable_member_is_bound_but_not_projected(self, tmp_path: Path) -> None:
        from ghrah.ability.access import (
            AbilityAccessStore,
            AccessIdentity,
            GrantPolicy,
            GrantRule,
        )

        store = await AbilityAccessStore.open(
            tmp_path / "ability.db",
            "default",
            operators=frozenset({"deployment"}),
            runs=lambda agent: f"{agent}-run",
        )
        try:
            policy = GrantPolicy(
                rules=(
                    GrantRule(
                        package="ghrah.fs",
                        operation="read_file",
                        available=False,
                    ),
                )
            )
            unit = create_core_unit(
                CoreUnitConfig(
                    project_id="default",
                    manifest_store=_consumer_store(),
                    ability_source="builtin",
                    workspace_root=str(tmp_path / "workspace"),
                    ability_store=store,
                    ability_grant_policy=policy,
                )
            )

            receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

            assert receipt["success"], receipt.get("error")
            abilities = await _abilities_of(unit, "consumer")
            assert "read_file" not in abilities
            deploy = AccessIdentity("default", "deployment", "deployment", True, "deployment")
            names = await store.tool_names(deploy, _agent_id("consumer"))
            assert "read_file" in names
            assert {"read_file", "write_file", "mcp_call"} <= set(names)
        finally:
            await store.close()

    async def test_no_policy_means_no_grant(self, tmp_path: Path) -> None:
        from ghrah.ability.access import AbilityAccessStore

        store = await AbilityAccessStore.open(
            tmp_path / "ability.db",
            "default",
            operators=frozenset({"deployment"}),
            runs=lambda agent: f"{agent}-run",
        )
        try:
            unit = create_core_unit(
                CoreUnitConfig(
                    project_id="default",
                    manifest_store=_consumer_store(),
                    ability_source="builtin",
                    workspace_root=str(tmp_path / "workspace"),
                    ability_store=store,
                )
            )

            receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

            assert receipt["success"] is False
            assert "未配置启动授权策略" in receipt["error"]
        finally:
            await store.close()


# ----------------------------------------------------------------
# 子 Agent spawn：与顶层 spawn 汇合到同一授权装配边界
# ----------------------------------------------------------------


async def test_subagent_spawn_uses_authorized_boundary(tmp_path: Path) -> None:
    from ghrah.ability.access import AccessIdentity, GrantPolicy, GrantRule
    from ghrah.ability.access.startup import AuthorizedToolTable
    from ghrah.ability.builtin.cluster.operations.spawn_agent import _assemble_child_abilities

    manifest_store = _consumer_store()
    # 策略按包声明生效：子 Agent 模板里只有 fs 包（引用未解析包会失败）
    policy = GrantPolicy(rules=(GrantRule(package="ghrah.fs"),))
    unit, ability_store = await _authorized_builtin_unit(
        tmp_path, manifest_store=manifest_store, policy=policy
    )
    try:
        await unit.init(FakeCtx())
        assert unit.supervisor is not None
        assembly = unit._require_assembly()
        resolved = assembly.manifest_resolver().resolve(
            manifest_store.load_agent("w0.fs-consumer"), runtime_name="child"
        )
        services = make_services(supervisor=unit.supervisor)

        from dataclasses import replace

        resolved.config = replace(resolved.config, agent_id=_agent_id("child"))
        table, materialized, tool_table_projector = await _assemble_child_abilities(
            services=services, manifest_ref="w0.fs-consumer", resolved=resolved
        )

        assert isinstance(table, AuthorizedToolTable)
        assert materialized is not None
        assert tool_table_projector is not None
        assert any(getattr(ability, "name", "") == "read_file" for ability in table)

        # 子 Agent 的授权事实写在关系库，与顶层 spawn 同一套事实
        deployment = AccessIdentity("default", "deployment", "deployment", True, "deployment")
        records = await ability_store.grants(deployment, resolved.config.agent_id)
        assert [record.alias for record in records] == ["fs"]

        await unit.supervisor.spawn_agent(
            resolved.config,
            abilities=table,
            tool_table_projector=tool_table_projector,
        )
        handle = await unit.supervisor.get_agent_handle(resolved.config.name)
        target = tmp_path / "workspace" / "child.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("child ok", encoding="utf-8")
        result = await handle._ability_executor.execute_ability(
            handle._abilities["read_file"],
            make_call(arguments={"file_path": str(target)}),
            make_services(),
        )
        assert result.outcome is ActionOutcome.SUCCESS
        assert result.data["content"] == "child ok"
    finally:
        await ability_store.close()


async def test_subagent_spawn_without_boundary_fails_closed() -> None:
    from ghrah.ability.builtin.cluster.operations.spawn_agent import _assemble_child_abilities
    from ghrah.types.results import ActionResult

    manifest_store = _consumer_store()
    unit = create_core_unit(
        CoreUnitConfig(
            project_id="default", manifest_store=manifest_store, ability_source="builtin"
        )
    )
    await unit.init(FakeCtx())
    assembly = unit._require_assembly()
    resolved = assembly.manifest_resolver().resolve(
        manifest_store.load_agent("w0.fs-consumer"), runtime_name="child"
    )

    result = await _assemble_child_abilities(
        services=make_services(supervisor=unit.supervisor),
        manifest_ref="w0.fs-consumer",
        resolved=resolved,
    )

    assert isinstance(result, ActionResult)
    assert result.outcome is ActionOutcome.FAILURE
    assert "授权装配边界" in result.data["error"]


async def test_builtin_supervisor_rejects_caller_assembled_tools() -> None:
    from ghrah.communication.errors import RegistryError

    manifest_store = _consumer_store()
    unit = create_core_unit(
        CoreUnitConfig(
            project_id="default", manifest_store=manifest_store, ability_source="builtin"
        )
    )
    await unit.init(FakeCtx())
    assembly = unit._require_assembly()
    resolved = assembly.manifest_resolver().resolve(
        manifest_store.load_agent("w0.fs-consumer"), runtime_name="direct"
    )
    plain_list = assembly.instantiate(resolved.abilities, workspace_root=None)

    from dataclasses import replace

    resolved.config = replace(resolved.config, agent_id=_agent_id("direct"))
    with pytest.raises(RegistryError, match="authorized assembly"):
        await unit.supervisor.spawn_agent(resolved.config, abilities=plain_list)


# ----------------------------------------------------------------
# 工具面持续投影：撤销后下一轮不再出现在模型视野
# ----------------------------------------------------------------


async def test_actor_tool_table_follows_revoke_without_restart(tmp_path: Path) -> None:
    """关系库撤销后刷新工具表：工具从模型视野消失，控制成员保留。"""
    from ghrah.protocol.ability_tree import RevokeAbilityRequest

    from ghrah.ability.access import AccessIdentity

    store = _consumer_store()
    unit, ability_store = await _authorized_builtin_unit(tmp_path, manifest_store=store)
    try:
        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))
        assert receipt["success"], receipt.get("error")
        handle = await unit.supervisor.get_agent_handle("consumer")

        def tool_names() -> set[str]:
            return {item["function"]["name"] for item in handle._bound_tools}

        assert {"read_file", "write_file", "mcp_call"} <= tool_names()

        actor = AccessIdentity("default", "deployment", "control", True, "deployment")
        revision = await ability_store.agent_revision(actor, _agent_id("consumer"))
        await ability_store.write_revoke(
            actor,
            _agent_id("consumer"),
            RevokeAbilityRequest(
                alias="fs",
                members=["operations/write_file"],
                expected_revision=revision,
            ),
        )

        await handle.refresh_tool_projection()

        assert "write_file" not in tool_names()
        assert "read_file" in tool_names()
        # 循环控制成员不属于授权面，不随关系库投影消失
        assert "end_task" in tool_names()

        # 执行侧仍拒绝：工具表刷新只是投影，接纳才是闸门
        denied = await handle._ability_executor.execute_ability(
            handle._abilities["write_file"],
            make_call(arguments={"file_path": str(tmp_path / "x.txt"), "content": "x"}),
            make_services(),
        )
        assert denied.outcome is ActionOutcome.FAILURE
    finally:
        await ability_store.close()


# ----------------------------------------------------------------
# W2 出口：Mock LLM 在真实 receive 循环里完成工具轮与回复轮
# ----------------------------------------------------------------


async def test_builtin_mock_llm_tool_round_feeds_next_input(tmp_path: Path) -> None:
    """真实 receive 循环：工具轮结果与 tool_call_id 进入下一轮输入，节点已提交。"""
    from _helpers import ToolScriptedLLM

    from ghrah.chat.content import ToolCallBlock
    from ghrah.core.message import AgentMessage, MessageType

    target = tmp_path / "workspace" / "note.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("builtin tool round", encoding="utf-8")

    scripted = ToolScriptedLLM(
        rounds=[
            [
                ToolCallBlock(
                    id="call-1",
                    name="read_file",
                    arguments={"file_path": str(target)},
                )
            ]
        ],
        final_text="读完了",
    )
    unit, ability_store = await _authorized_builtin_unit(
        tmp_path,
        manifest_store=_consumer_store(),
        llm_factory=lambda config: scripted,
    )
    try:
        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))
        assert receipt["success"], receipt.get("error")
        handle = await unit.supervisor.get_agent_handle("consumer")

        reply = await handle.receive(
            AgentMessage(
                sender="user",
                recipient="consumer",
                content="读文件",
                type=MessageType.CHAT,
            )
        )

        assert reply.content == "读完了"
        assert len(scripted.calls) >= 2

        tool_results = [
            block for message in scripted.calls[1] for block in getattr(message, "tool_results", [])
        ]
        assert tool_results, "工具结果必须进入下一次模型输入"
        assert tool_results[0].tool_call_id == "call-1"
        assert "builtin tool round" in tool_results[0].content

        cm = handle._context_manager
        assert cm.active_head is not None
        committed = [name for node in cm.get_history() for name in node.ability_names]
        assert "read_file" in committed
        assert "conversation" in committed

        # 同一真实 Agent 闭环继续验证撤销、下一轮工具面与陈旧工具调用。
        from ghrah.protocol.ability_tree import RevokeAbilityRequest

        from ghrah.ability.access import AccessIdentity

        actor = AccessIdentity("default", "deployment", "control", True, "deployment")
        agent_id = handle.config.agent_id
        old_tool = handle._abilities["read_file"]
        revision = await ability_store.agent_revision(actor, agent_id)
        await ability_store.write_revoke(
            actor,
            agent_id,
            RevokeAbilityRequest(
                alias="fs", members=["operations/read_file"], expected_revision=revision
            ),
        )
        next_reply = await handle.receive(
            AgentMessage(sender="user", recipient="consumer", content="继续")
        )
        assert next_reply.content == "读完了"
        assert "read_file" not in {tool["function"]["name"] for tool in scripted.tools}
        call, services = handle._build_ability_call(
            old_tool, {"file_path": str(target)}, {}, tool_call_id="after-revoke"
        )
        denied = await handle._ability_executor.execute_ability(old_tool, call, services)
        assert denied.outcome is ActionOutcome.FAILURE
    finally:
        await ability_store.close()


async def test_builtin_spawn_generates_stable_id_before_authorization(tmp_path):
    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    try:
        payload = _manifest_payload("planner", "w0.fs-consumer")
        payload["config"].pop("agent_id")
        receipt = await _spawn(unit, payload)
        assert receipt["success"], receipt
        handle = await unit.supervisor.get_agent_handle("planner")
        agent_id = handle.config.agent_id
        assert len(agent_id) == 32 and agent_id != "planner"
        from ghrah.ability.access import AccessIdentity

        actor = AccessIdentity("default", "deployment", "control", True, "deployment")
        assert await db.grants(actor, agent_id)
        from ghrah.ability.access import AccessError

        with pytest.raises(AccessError, match="not_found"):
            await db.grants(actor, "planner")
        identity = handle._invocation_identity()
        assert identity.agent == agent_id and identity.agent_name == "planner"
        assert identity.run == f"{agent_id}-run"
        assert identity.project_id == "default"
    finally:
        await unit.stop()
        await db.close()


async def test_duplicate_and_concurrent_spawn_cannot_rewrite_existing_grants(tmp_path):
    import asyncio

    from ghrah.ability.access import AccessIdentity

    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    actor = AccessIdentity("default", "deployment", "control", True, "deployment")
    await unit.init(FakeCtx())

    async def spawn(payload):
        return await unit.handle_command("spawn_agent", payload, None)

    try:
        payload = _manifest_payload("consumer", "w0.consumer")
        receipts = await asyncio.gather(spawn(payload), spawn(payload))
        assert sum(bool(r["success"]) for r in receipts) == 1
        before = await db.grants(actor, _agent_id("consumer"))
        revision = await db.agent_revision(actor, _agent_id("consumer"))
        duplicate = await spawn(_manifest_payload("consumer", "w0.fs-consumer"))
        assert not duplicate["success"]
        assert await db.grants(actor, _agent_id("consumer")) == before
        assert await db.agent_revision(actor, _agent_id("consumer")) == revision
    finally:
        await unit.stop()
        await db.close()


async def test_failed_run_binding_removes_newly_registered_actor(tmp_path, monkeypatch):
    from ghrah.ability.access import StartupGrantError

    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    from dataclasses import replace

    from ghrah.context.persistence.sqlite_backend import SqliteBackend

    checkpoint_path = tmp_path / "failed-context.db"
    unit._config = replace(
        unit._config, persistence_factory=lambda _: SqliteBackend(checkpoint_path)
    )

    async def fail_binding(*args):
        raise StartupGrantError("run binding unavailable")

    await unit.init(FakeCtx())
    monkeypatch.setattr(unit._startup_materializer, "bind_run", fail_binding)
    try:
        receipt = await unit.handle_command(
            "spawn_agent", _manifest_payload("consumer", "w0.fs-consumer"), None
        )
        assert not receipt["success"] and "run binding unavailable" in receipt["error"]
        assert not unit.supervisor._registry.exists("consumer")
        assert unit.supervisor._spawn_owner is None
        agent_id = _agent_id("consumer")
        assert await db._row("ability_agents", "id=?", agent_id) is None
        assert await db._row("ability_registrations", "agent=?", agent_id) is None
        reader = SqliteBackend(checkpoint_path)
        await reader.connect()
        assert await reader.load_checkpoint(agent_id) is None
        await reader.close()

    finally:
        await unit.stop()
        await db.close()


async def test_real_spawn_tool_assigns_child_own_id_and_bound_run(tmp_path):
    from ghrah.ability.access import AccessIdentity

    store = _consumer_store()
    store._agents["w0.parent"] = parse_agent_manifest(
        _AGENT_YAML.replace("  - ref: ghrah.mcp.mcp_call", "  - ref: ghrah.cluster.spawn_agent")
    )
    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=store)
    try:
        receipt = await _spawn(unit, _manifest_payload("parent", "w0.parent"))
        assert receipt["success"], receipt
        parent = await unit.supervisor.get_agent_handle("parent")
        spawn = parent._abilities["spawn_agent"]
        call, services = parent._build_ability_call(
            spawn, {"manifest_ref": "w0.fs-consumer", "name": "child"}, {}, tool_call_id="spawn-1"
        )
        result = await spawn.execute(call, services)
        assert result.outcome is ActionOutcome.SUCCESS, result.data
        child = await unit.supervisor.get_agent_handle("child")
        assert len(child.config.agent_id) == 32 and child.config.agent_id != parent.config.agent_id
        identity = child._invocation_identity()
        assert identity.agent == child.config.agent_id and identity.agent_name == "child"
        assert identity.run == f"{child.config.agent_id}-run"
        actor = AccessIdentity("default", "deployment", "control", True, "deployment")
        grants = await db.grants(actor, child.config.agent_id)
        assert [grant.alias for grant in grants] == ["fs"]
        assert "spawn_agent" not in child._abilities
    finally:
        await unit.stop()
        await db.close()


async def test_recreated_name_with_new_id_does_not_inherit_old_authorization(tmp_path):
    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    try:
        first = await _spawn(unit, _manifest_payload("planner", "w0.fs-consumer"))
        assert first["success"], first
        await unit.supervisor.terminate_agent("planner")
        unit._startup_materializer._policy = None
        payload = _manifest_payload("planner", "w0.fs-consumer")
        payload["config"]["agent_id"] = _agent_id("new-stable-id")
        receipt = await unit.handle_command("spawn_agent", payload, None)
        assert not receipt["success"]
        assert "没有存量授权事实" in receipt["error"]
        assert not unit.supervisor._registry.exists("planner")
    finally:
        await unit.stop()
        await db.close()


@pytest.mark.parametrize("entry", ["llm", "command", "routed", "child"])
@pytest.mark.parametrize("member", ["query_agents", "session_info"])
async def test_builtin_approval_is_identical_across_real_entrypoints(
    tmp_path, monkeypatch, entry, member
):
    import asyncio
    from unittest.mock import AsyncMock

    from _helpers import ToolScriptedLLM

    from ghrah.chat.content import ToolCallBlock
    from ghrah.core.message import AgentMessage

    package = "cluster" if member == "query_agents" else "core"
    store = _consumer_store()
    text = _AGENT_YAML_FS[: _AGENT_YAML_FS.index("abilities:")] + (
        f"abilities:\n  - type: conversation\n  - ref: ghrah.{package}.{member}\n"
        "    permissions:\n      require_hitl: true\n"
    )
    store._agents["w0.secure"] = parse_agent_manifest(text)
    scripted = ToolScriptedLLM(
        [[ToolCallBlock(id="real-call", name=member, arguments={})]], final_text="done"
    )
    unit, db = await _authorized_builtin_unit(
        tmp_path,
        manifest_store=store,
        llm_factory=lambda _: scripted,
    )
    try:
        if entry == "child":
            store._agents["w0.parent"] = parse_agent_manifest(
                _AGENT_YAML.replace(
                    "  - ref: ghrah.mcp.mcp_call", "  - ref: ghrah.cluster.spawn_agent"
                )
            )
            receipt = await _spawn(unit, _manifest_payload("parent", "w0.parent"))
            assert receipt["success"], receipt
            parent = await unit.supervisor.get_agent_handle("parent")
            spawn = parent._abilities["spawn_agent"]
            call, services = parent._build_ability_call(
                spawn, {"manifest_ref": "w0.secure", "name": "consumer"}, {}, tool_call_id="spawn"
            )
            assert (await spawn.execute(call, services)).outcome is ActionOutcome.SUCCESS
        else:
            receipt = await _spawn(unit, _manifest_payload("consumer", "w0.secure"))
            assert receipt["success"], receipt
        handle = await unit.supervisor.get_agent_handle("consumer")
        tool = handle._abilities[member]
        actual = tool._inner._inner  # 投影 -> 审批委托 -> 实际 Operation。
        effect = AsyncMock(wraps=actual.execute)
        monkeypatch.setattr(actual, "execute", effect)
        if entry == "command":
            task = asyncio.create_task(
                unit.handle_command(
                    "execute_ability",
                    {
                        "request_id": "explicit-call",
                        "agent_name": "consumer",
                        "ability_name": member,
                        "tool_args": {},
                    },
                    None,
                )
            )
        elif entry == "routed":
            task = asyncio.create_task(handle._execute_routed_ability({}, member))
        elif entry == "child":
            call, services = handle._build_ability_call(tool, {}, {}, tool_call_id="child-call")
            task = asyncio.create_task(
                handle._ability_executor.execute_ability(tool, call, services)
            )
        else:
            task = asyncio.create_task(
                handle.receive(AgentMessage(sender="user", recipient="consumer", content="run"))
            )
        async with asyncio.timeout(3):
            while not handle._ability_executor.hitl_store.list_pending():
                assert not task.done(), task.result() if task.done() else ""
                await asyncio.sleep(0)
        assert not effect.called
        _, target, call_id = handle._ability_executor.hitl_store.list_pending()[0]
        assert target == member
        handle._ability_executor.receive_hitl_response(member, call_id, approved=True)
        await task
        assert effect.call_count == 1
        assert not handle._ability_executor.hitl_store.list_pending()
        assert not unit._hitl_promises.list_pending()
        if entry == "llm":
            assert len(scripted.calls) == 2
            assert handle._context_manager.active_head is not None
    finally:
        await unit.stop()
        await db.close()


async def test_cross_cluster_same_uuid_is_rejected_before_authorization_changes(tmp_path):
    from ghrah.ability.access import AccessIdentity

    first, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    second, other = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    actor = AccessIdentity("default", "deployment", "control", True, "deployment")
    try:
        assert (await _spawn(first, _manifest_payload("consumer", "w0.consumer")))["success"]
        before = await db.grants(actor, _agent_id("consumer"))
        payload = _manifest_payload("consumer", "w0.fs-consumer")
        payload["config"]["name"] = "different-display"
        assert not (await _spawn(second, payload))["success"]
        assert await db.grants(actor, _agent_id("consumer")) == before
        await first.supervisor.terminate_agent("consumer")
        result = await second.handle_command("spawn_agent", payload, None)
        assert result["success"], result
    finally:
        await first.stop()
        await second.stop()
        await other.close()
        await db.close()


async def test_failed_resume_preserves_prior_checkpoint_and_authorization(tmp_path, monkeypatch):
    from dataclasses import replace

    from ghrah.ability.access import AccessIdentity, StartupGrantError
    from ghrah.context.persistence.sqlite_backend import SqliteBackend

    unit, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    path = tmp_path / "context.db"
    unit._config = replace(unit._config, persistence_factory=lambda _: SqliteBackend(path))
    actor = AccessIdentity("default", "deployment", "control", True, "deployment")
    agent_id = _agent_id("consumer")
    try:
        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.fs-consumer"))
        assert receipt["success"], receipt
        handle = await unit.supervisor.get_agent_handle("consumer")
        handle._context_manager.begin_iteration()
        handle._context_manager.apply_state_changes({"preserve": True})
        handle._context_manager.commit_iteration(ability_names=[])
        await unit.supervisor.terminate_agent("consumer")
        reader = SqliteBackend(path)
        await reader.connect()
        before = await reader.load_checkpoint(agent_id)
        await reader.close()
        grants = await db.grants(actor, agent_id)

        async def fail(*args):
            raise StartupGrantError("cannot bind resumed run")

        monkeypatch.setattr(unit._startup_materializer, "bind_run", fail)
        failed = await unit.handle_command(
            "spawn_agent", _manifest_payload("consumer", "w0.consumer"), None
        )
        assert not failed["success"]
        assert await db.grants(actor, agent_id) == grants
        reader = SqliteBackend(path)
        await reader.connect()
        after = await reader.load_checkpoint(agent_id)
        await reader.close()
        assert before == after
        assert not unit.supervisor._registry.exists("consumer")
    finally:
        await unit.stop()
        await db.close()


async def test_real_same_display_names_in_two_clusters_keep_distinct_grants(tmp_path):
    from ghrah.protocol.ability_tree import RevokeAbilityRequest

    from ghrah.ability.access import AccessIdentity

    first, db = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    second, other = await _authorized_builtin_unit(tmp_path, manifest_store=_consumer_store())
    actor = AccessIdentity("default", "deployment", "control", True, "deployment")
    try:
        pa, pb = (
            _manifest_payload("worker", "w0.fs-consumer"),
            _manifest_payload("worker", "w0.consumer"),
        )
        pa["config"]["agent_id"], pb["config"]["agent_id"] = "a" * 32, "b" * 32
        assert (await _spawn(first, pa))["success"]
        assert (await _spawn(second, pb))["success"]
        before = await db.grants(actor, "a" * 32)
        revision = await other.agent_revision(actor, "b" * 32)
        await other.write_revoke(
            actor, "b" * 32, RevokeAbilityRequest(alias="fs", expected_revision=revision)
        )
        assert await db.grants(actor, "a" * 32) == before
        handle = await first.supervisor.get_agent_handle("worker")
        target = tmp_path / "workspace" / "kept.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text("a-only")
        tool = handle._abilities["read_file"]
        call, services = handle._build_ability_call(tool, {"file_path": str(target)}, {})
        result = await handle._ability_executor.execute_ability(tool, call, services)
        assert result.outcome is ActionOutcome.SUCCESS and result.data["content"] == "a-only"
    finally:
        await first.stop()
        await second.stop()
        await other.close()
        await db.close()
