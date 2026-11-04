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
from test_core_unit import FakeCtx

from ghrah.abilities.context import AbilityExecutionContext
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


def _manifest_payload(name: str, manifest_ref: str) -> dict[str, Any]:
    return {
        "project_id": "default",
        "config": {"name": name},
        "manifest_ref": manifest_ref,
    }


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
    async def test_manifest_ref_spawn_uses_new_operations(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", manifest_store=store, ability_source="builtin")
        )

        receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))

        assert receipt["success"], receipt.get("error")
        modules = await _abilities_of(unit, "consumer")
        assert modules["read_file"].startswith("ghrah.ability.")
        assert modules["write_file"].startswith("ghrah.ability.")
        # mcp_call 只在新版容器定义里存在：命中它即证明 ref/type 都走新来源
        assert modules["mcp_call"].startswith("ghrah.ability.")
        # 声明存储里的能力库没有被读（同一模式不混第二套目录）
        assert store.ability_reads == []

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

    async def test_register_and_unregister_are_refused(self) -> None:
        store = _consumer_store()
        unit = create_core_unit(
            CoreUnitConfig(project_id="default", manifest_store=store, ability_source="builtin")
        )
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
        # 拒绝发生在改动之前：工具表保持 manifest 投影
        assert "read_file" in await _abilities_of(unit, "consumer")

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
    unit = create_core_unit(
        CoreUnitConfig(
            project_id="default",
            manifest_store=store,
            ability_source="builtin",
            workspace_root=str(tmp_path),
        )
    )
    receipt = await _spawn(unit, _manifest_payload("consumer", "w0.consumer"))
    assert receipt["success"], receipt.get("error")
    handle = await unit.supervisor.get_agent_handle("consumer")

    target = tmp_path / "note.txt"
    target.write_text("builtin source content", encoding="utf-8")
    result = await handle._ability_executor.execute_ability(
        handle._abilities["read_file"],
        AbilityExecutionContext(tool_args={"file_path": str(target)}),
    )

    assert result.outcome is ActionOutcome.SUCCESS
    assert result.data["content"] == "builtin source content"
