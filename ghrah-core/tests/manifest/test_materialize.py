# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""instantiate_resolved_abilities 测试（ResolvedAgent → Ability 直连）。

覆盖（对齐独立库 examples/agents_manifest/runner 范式，不经 wire DTO 往返）：
1. builtin handler 直接构造（conversation/end_task）
2. FS handler：PermissionFlags → FSPermissionChecker（含 {{workspace}} 模板展开）
3. execute_command：CommandSafetyChecker + CommandApprovalHook 注入
4. 非 builtin 跳过 + warning
5. HITL 覆盖层（auto_approve > manifest require_hitl > 兜底默认）
6. 无 manifest_ref：_handle_spawn_agent 原样走直传 abilities 路径
7. standalone 无 store：manifest_ref spawn 明确报错
"""

from __future__ import annotations

from typing import Any

import pytest

# 触发内置 Ability 注册（instantiate_resolved_abilities 依赖 AbilityRegistry）
import ghrah.abilities  # noqa: F401
from ghrah.abilities.builtin.fs_permissions import AccessApprovalHook, FSPermissionChecker
from ghrah.manifest.agent import (
    AbilityRef,
    AgentManifest,
    AgentMetadata,
    ModelConfig,
)
from ghrah.manifest.materialize import instantiate_resolved_abilities
from ghrah.manifest.resolver import ManifestResolver
from ghrah.manifest.store import BuiltinManifestStore
from ghrah.manifest.types import ImplementationDef, PermissionFlags


def _agent_manifest(
    abilities: list[AbilityRef],
    *,
    namespace: str = "test_ns",
    name: str = "test_agent",
) -> AgentManifest:
    return AgentManifest(
        manifest="agent",
        version="2",
        metadata=AgentMetadata(namespace=namespace, name=name, description="test"),
        model=ModelConfig(endpoint_name="default"),
        system_prompt="test",
        max_iterations=10,
        communication_timeout=300.0,
        abilities=abilities,
    )


def _resolved(abilities: list[AbilityRef], store: Any = None) -> list:
    store = store or BuiltinManifestStore()
    manifest = _agent_manifest(abilities)
    return ManifestResolver(store).resolve(manifest).abilities


class TestInstantiateResolvedAbilities:
    def test_builtin_conversation_direct(self) -> None:
        abilities = _resolved([AbilityRef(type="conversation")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        assert len(result) == 1
        assert result[0].name == "conversation"

    def test_builtin_end_task_toolcall_mode(self) -> None:
        abilities = _resolved([AbilityRef(type="end_task")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        assert result[0].name == "end_task"

    def test_fs_write_file_permission_checker_with_template(self, tmp_path) -> None:
        ws = str(tmp_path / "workspace")
        abilities = _resolved(
            [
                AbilityRef(
                    ref="ghrah.fs.write_file",
                    permissions=PermissionFlags(
                        require_hitl=False,
                        allowed_paths=["{{workspace}}/out"],
                    ),
                )
            ]
        )
        result = instantiate_resolved_abilities(abilities, workspace_root=ws)
        assert len(result) == 1
        checker = result[0]._checker  # type: ignore[attr-defined]
        assert isinstance(checker, FSPermissionChecker)
        # 模板展开后 allowed_paths 指向 workspace/out
        assert any("out" in str(p) for p in checker._allowed_paths)  # type: ignore[attr-defined]

    def test_execute_command_hook_injected(self) -> None:
        abilities = _resolved([AbilityRef(type="execute_command")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None, command_runner=None)
        assert result[0].name == "execute_command"
        hooks = result[0].get_hooks()
        assert len(hooks) == 1

    def test_non_builtin_skipped_with_warning(self, caplog) -> None:
        # 构造非 builtin implementation：python 类型无 handler
        from ghrah.manifest.ability import AbilityHooks
        from ghrah.manifest.resolver import ResolvedAbility

        ra = ResolvedAbility(
            ability_name="custom.python",
            tool_schema=None,
            permissions=PermissionFlags(),
            implementation=ImplementationDef(type="python", handler=None),
            hooks=AbilityHooks(),
        )
        with caplog.at_level("WARNING"):
            result = instantiate_resolved_abilities([ra], workspace_root=None)
        assert result == []
        assert any("non-builtin" in r.message.lower() for r in caplog.records)

    def test_hitl_auto_approve_overrides_manifest(self) -> None:
        abilities = _resolved(
            [
                AbilityRef(
                    ref="ghrah.fs.write_file",
                    permissions=PermissionFlags(require_hitl=True),
                )
            ]
        )
        result = instantiate_resolved_abilities(
            abilities,
            workspace_root=None,
            auto_approve_abilities=("write_file",),
        )
        checker = result[0]._checker  # type: ignore[attr-defined]
        assert checker._require_approval is False  # type: ignore[attr-defined]

    def test_hitl_manifest_require_hitl_respected(self) -> None:
        abilities = _resolved(
            [
                AbilityRef(
                    ref="ghrah.fs.write_file",
                    permissions=PermissionFlags(require_hitl=True),
                )
            ]
        )
        result = instantiate_resolved_abilities(
            abilities, workspace_root=None, require_approval_by_default=False
        )
        checker = result[0]._checker  # type: ignore[attr-defined]
        assert checker._require_approval is True  # type: ignore[attr-defined]

    def test_fs_require_hitl_mounts_approval_hook(self) -> None:
        """断链回归：require_hitl 的 FS ability 必须带 AccessApprovalHook。

        此前装配只注入 permission_checker 不挂 hooks，FS 内联检查把
        (True, "pending") 当放行——白名单外访问被静默放行，HITL 从未生效。
        """
        abilities = _resolved(
            [
                AbilityRef(
                    ref="ghrah.fs.write_file",
                    permissions=PermissionFlags(require_hitl=True),
                )
            ]
        )
        result = instantiate_resolved_abilities(
            abilities, workspace_root=None, require_approval_by_default=False
        )
        hooks = result[0].get_hooks()
        assert len(hooks) == 1
        assert isinstance(hooks[0], AccessApprovalHook)

    def test_fs_no_approval_keeps_hard_deny(self) -> None:
        """不声明审批的 FS ability（builtin read_file 无 require_hitl）不挂 hook。

        注：builtin ghrah.fs.write_file 自带 require_hitl: true（merge 为 or
        语义，False 无法覆盖），其审批 Hook 挂载由上一个测试覆盖。
        白名单外维持直接拒绝语义。
        """
        abilities = _resolved([AbilityRef(ref="ghrah.fs.read_file")])
        result = instantiate_resolved_abilities(
            abilities, workspace_root=None, require_approval_by_default=False
        )
        assert result[0].get_hooks() == []

    def test_mixed_abilities(self) -> None:
        abilities = _resolved(
            [
                AbilityRef(type="conversation"),
                AbilityRef(type="end_task"),
                AbilityRef(ref="ghrah.fs.read_file"),
            ]
        )
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        assert {a.name for a in result} == {"conversation", "end_task", "read_file"}


class TestGenericHITLWiring:
    """通用 HITL 接线：非 FS/非 command ability 的 require_hitl 声明
    必须经 _HITLRequiredAbility 包装实际挂接 PRE_EXECUTE hook——
    现状兜底分支裸实例化会静默忽略该声明（纸面门禁）。"""

    def test_cluster_ability_require_hitl_mounts_hook(self) -> None:
        """spawn_agent manifest 声明 require_hitl → 实际挂 PRE_EXECUTE HITL hook。"""
        abilities = _resolved([AbilityRef(ref="ghrah.cluster.spawn_agent")])
        # resolver 权限合并：builtin yaml 的 require_hitl=True 应透传
        assert abilities[0].permissions.require_hitl is True

        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        assert len(result) == 1
        wrapped = result[0]
        assert wrapped.name == "spawn_agent"
        hooks = wrapped.get_hooks()
        hitl_hooks = [
            h
            for h in hooks
            if getattr(h, "hook_point", None) is not None and h.hook_point.name == "PRE_EXECUTE"
        ]
        assert len(hitl_hooks) == 1

    async def test_hitl_hook_returns_hitl_result(self) -> None:
        """包装后的 hook 执行返回 HookResult.hitl（requires_hitl=True）。"""
        from ghrah.abilities.context import AbilityExecutionContext
        from ghrah.abilities.hooks import HookResult

        abilities = _resolved([AbilityRef(ref="ghrah.cluster.spawn_agent")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        hitl_hook = [h for h in result[0].get_hooks() if h.hook_point.name == "PRE_EXECUTE"][0]

        ctx = AbilityExecutionContext(
            current_ability_name="spawn_agent",
            tool_args={"manifest_ref": "ghrah.designer"},
        )
        assert await hitl_hook.should_trigger(ctx) is True
        hook_result = await hitl_hook.execute(ctx, None)
        assert isinstance(hook_result, HookResult)
        assert hook_result.requires_hitl is True
        assert hook_result.should_continue is False
        assert "spawn_agent" in (hook_result.message or "")

    def test_auto_approve_suppresses_generic_hitl_wiring(self) -> None:
        """auto_approve 白名单 > manifest require_hitl——包装不生效。"""
        abilities = _resolved([AbilityRef(ref="ghrah.cluster.spawn_agent")])
        result = instantiate_resolved_abilities(
            abilities,
            workspace_root=None,
            auto_approve_abilities=("spawn_agent",),
        )
        assert len(result) == 1
        # 不应包装：hooks 中无 HITL hook（原生 spawn_agent.get_hooks() == []）
        assert result[0].get_hooks() == []

    def test_no_require_hitl_no_wrapping(self) -> None:
        """未声明 require_hitl 的普通 ability 保持裸实例化。"""
        abilities = _resolved([AbilityRef(type="query_agents")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        assert len(result) == 1
        assert result[0].name == "query_agents"
        assert result[0].get_hooks() == []

    def test_wrapper_delegates_bind_tool_and_state(self) -> None:
        """包装不破坏宿主 ability 的工具面与默认状态（组合委托）。"""
        abilities = _resolved([AbilityRef(ref="ghrah.cluster.spawn_agent")])
        result = instantiate_resolved_abilities(abilities, workspace_root=None)
        wrapped = result[0]
        tool = wrapped.bind_tool()
        assert tool is not None
        assert tool["function"]["name"] == "spawn_agent"
        assert "manifest_ref" in tool["function"]["parameters"]["properties"]


class TestHandleSpawnAgentManifestRef:
    """CoreUnit._handle_spawn_agent manifest_ref 路径（不经 wire DTO）。"""

    @pytest.fixture
    def unit_with_store(self, tmp_path) -> Any:
        from ghrah.core.unit import CoreUnitConfig, create_core_unit
        from ghrah.manifest.ability import AbilityManifest
        from ghrah.manifest.parser import parse_agent_manifest
        from ghrah.manifest.protocols import ManifestStoreProtocol
        from ghrah.manifest.store import BuiltinManifestStore

        planner_yaml = """\
manifest: agent
version: "2"
metadata:
  namespace: ghrah.examples
  name: planner
  description: planner
model:
  endpoint_name: planner
system_prompt: You are a planner.
max_iterations: 10
abilities:
  - type: conversation
  - type: end_task
  - ref: ghrah.fs.write_file
    permissions:
      require_hitl: false
      allowed_paths:
        - "{{workspace}}/planner"
  - ref: ghrah.fs.list_directory
    permissions:
      allowed_paths:
        - "{{workspace}}/planner"
"""

        class _CompositeStore(ManifestStoreProtocol):
            """builtin abilities + 自定义 agent manifest。"""

            def __init__(self) -> None:
                self._builtin = BuiltinManifestStore()
                self._agents: dict[str, AgentManifest] = {
                    "ghrah.examples.planner": parse_agent_manifest(planner_yaml)
                }

            def load_ability(self, full_name: str) -> AbilityManifest:
                return self._builtin.load_ability(full_name)

            def load_agent(self, full_name: str) -> AgentManifest:
                if full_name in self._agents:
                    return self._agents[full_name]
                from ghrah.manifest.errors import ManifestNotFoundError

                raise ManifestNotFoundError(f"Agent manifest not found: {full_name}")

            def list_abilities(self, namespace: str | None = None) -> list[str]:
                return self._builtin.list_abilities(namespace)

            def list_agents(self, namespace: str | None = None) -> list[str]:
                return list(self._agents)

        config = CoreUnitConfig(
            workspace_root=str(tmp_path / "workspace"),
            project_id="default",
            manifest_store=_CompositeStore(),
        )
        return create_core_unit(config), config

    async def test_manifest_ref_spawns_all_abilities(self, unit_with_store) -> None:
        unit, _ = unit_with_store
        await unit.init(_FakeEmitCtx())
        result = await unit.handle_command(
            "spawn_agent",
            {
                "project_id": "default",
                "config": {"name": "planner-1"},
                "manifest_ref": "ghrah.examples.planner",
            },
            None,
        )
        assert result["success"], result.get("error")

        # 断言：get_agent_info 返回 manifest 全部能力 + endpoint_name
        info = await unit.handle_command(
            "get_agent_info",
            {"project_id": "default", "agent_id": "planner-1", "name": "planner-1"},
            None,
        )
        assert info["success"], info.get("error")
        ability_names = list(info["data"]["abilities"])
        assert set(ability_names) == {
            "conversation",
            "end_task",
            "write_file",
            "list_directory",
        }
        agents = await unit.handle_command("list_agents", {"project_id": "default"}, None)
        names = [a["name"] for a in agents.get("data", {}).get("agents", [])]
        assert "planner-1" in names

    async def test_manifest_ref_no_store_errors(self, tmp_path) -> None:
        from ghrah.core.unit import CoreUnitConfig, create_core_unit

        unit = create_core_unit(CoreUnitConfig(project_id="default"))  # manifest_store=None
        await unit.init(_FakeEmitCtx())
        result = await unit.handle_command(
            "spawn_agent",
            {"project_id": "default", "config": {"name": "x"}, "manifest_ref": "ghrah.any"},
            None,
        )
        assert result["success"] is False
        assert "manifest_store" in result["error"]

    async def test_no_manifest_ref_direct_abilities_path(self, tmp_path) -> None:
        from ghrah.core.unit import CoreUnitConfig, create_core_unit

        unit = create_core_unit(CoreUnitConfig(project_id="default"))
        await unit.init(_FakeEmitCtx())
        # 无 manifest_ref，直传 abilities（wire DTO 路径保留）
        result = await unit.handle_command(
            "spawn_agent",
            {
                "project_id": "default",
                "config": {"name": "plain"},
                "abilities": [{"ability_type": "conversation"}],
            },
            None,
        )
        assert result["success"], result.get("error")


class _FakeEmitCtx:
    """Minimal ctx with provide/emit for CoreUnit.init."""

    def provide(self, name: str, value: Any) -> None:
        pass

    def emit(self, event_type: str, payload: dict) -> None:
        pass
