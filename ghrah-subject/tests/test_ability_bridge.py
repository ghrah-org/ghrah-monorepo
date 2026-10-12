# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""AbilityBridgeUnit 测试：场景 A/B 装配、闭环校验、P1 缺席、P2 卸载撤销、
crash 后 reconcile、spawn/terminate 事件联动、信任闸外不装配、幂等 diff。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ghrah.plugin.assembly import PluginAssembly
from ghrah.plugin.loader import DiscoveredPlugin
from ghrah.plugin.spec import PluginSpec
from ouroboros import Context  # type: ignore[import-untyped]

from ghrah.subject.config import PluginTrustConfig, SubjectConfig
from ghrah.subject.runtime.ability_bridge import AbilityBridgeUnit
from ghrah.subject.runtime.ouroboros_bridge import mount_dynamic_unit, unmount_unit
from ghrah.subject.runtime.plugin_mount import PluginSessionState, mount_enabled_plugins
from ghrah.subject.runtime.plugin_session import PLUGIN_SESSION_SERVICE, PluginSession
from ghrah.subject.runtime.route_registry import UnitRouteRegistry
from ghrah.subject.runtime.service_keys import (
    ABILITY_BRIDGE,
    CORE_CLUSTER_REGISTRY,
)
from ghrah.subject.unit.base import CommandContext, RouteSpec, SubjectUnit, UnitMeta

# ----------------------------------------------------------------
# fakes
# ----------------------------------------------------------------


@dataclass
class _FakeHandle:
    """单 cluster 假 handle：记录 dispatch、可配置回执与 agent 清单。"""

    project_id: str = "proj-1"
    agents: list[dict[str, Any]] = field(default_factory=list)
    dispatched: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_next: bool = False

    async def list_agents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [dict(a) for a in self.agents]

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.dispatched.append((command, dict(payload)))
        if self.fail_next:
            self.fail_next = False
            return {"success": False, "error": "boom"}
        return {"success": True, "data": {}}


@dataclass
class _FakeRegistry:
    clusters: dict[str, _FakeHandle] = field(default_factory=dict)

    @property
    def cluster_ids(self) -> list[str]:
        return list(self.clusters)

    def get_handle(self, cluster_id: str) -> _FakeHandle:
        return self.clusters[cluster_id]


class _BridgeService:
    """场景 B 能力包装服务 fake（AbilityBridgeService 契约）。"""

    def __init__(self, caps: dict[str, dict[str, Any]]) -> None:
        self._caps = {k: dict(v) for k, v in caps.items()}

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._caps.items()}

    async def register(self, capability: str, descriptor: dict[str, Any]) -> None:
        self._caps[capability] = dict(descriptor)

    async def remove(self, capability: str) -> None:
        self._caps.pop(capability, None)


class _CommandPluginUnit(SubjectUnit):
    """场景 A/B 的命令插件 unit（目标命令处理器）。"""

    def __init__(self, plugin_id: str, command: str, *, crash: bool = False) -> None:
        self._meta = UnitMeta(name=plugin_id, routes=RouteSpec(commands=frozenset({command})))
        self._crash = crash

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def handle_command(
        self, command: str, payload: dict[str, Any], cmd_ctx: CommandContext
    ) -> dict[str, Any]:
        if self._crash:
            raise RuntimeError("plugin exploded")
        return {"success": True, "data": {"plugin": self._meta.name}}


class _BridgeProviderUnit(SubjectUnit):
    """场景 B 的包装插件 unit：init 时 provide ABILITY_BRIDGE 服务。"""

    def __init__(self, name: str, service: Any) -> None:
        self._meta = UnitMeta(name=name)
        self._service = service

    @property
    def meta(self) -> UnitMeta:
        return self._meta

    async def init(self, ctx: Any) -> None:
        ctx.provide(ABILITY_BRIDGE.name, self._service)


def _spec(plugin_id: str, **kwargs: Any) -> PluginSpec:
    return PluginSpec(plugin_id=plugin_id, version="0.1.0", **kwargs)


def _config(trust: list[str]) -> SubjectConfig:
    config = SubjectConfig()
    config._plugin_trust = PluginTrustConfig(discoverable=trust)  # noqa: SLF001
    return config


def _discovered(*factories: tuple[PluginSpec, Any]) -> list[DiscoveredPlugin]:
    out = []
    for spec, factory in factories:
        out.append(
            DiscoveredPlugin(
                spec=spec, entry_point_name=spec.plugin_id, dist_name=None, unit_factory=factory
            )
        )
    return out


async def _mount_bridge(
    ctx: Context,
    registry: _FakeRegistry,
    specs: list[PluginSpec] | None = None,
    service: Any | None = None,
) -> AbilityBridgeUnit:
    """提供 registry/session/服务依赖并挂载桥接 unit。"""
    ctx.provide(CORE_CLUSTER_REGISTRY.name, registry)
    state = PluginSessionState()
    for spec in specs or []:
        state.specs[spec.plugin_id] = spec
    ctx.provide(PLUGIN_SESSION_SERVICE.name, PluginSession(state))
    if service is not None:
        ctx.provide(ABILITY_BRIDGE.name, service)
    unit = AbilityBridgeUnit()
    fibers: dict[str, Any] = {}
    await mount_dynamic_unit(ctx, unit, fibers)
    return unit


def _registers(handle: _FakeHandle) -> list[dict[str, Any]]:
    return [p for command, p in handle.dispatched if command == "register_ability"]


def _unregisters(handle: _FakeHandle) -> list[dict[str, Any]]:
    return [p for command, p in handle.dispatched if command == "unregister_ability"]


# ----------------------------------------------------------------
# 场景 A：插件自声明（默认 descriptor）
# ----------------------------------------------------------------


async def test_scenario_a_assembles_default_descriptor() -> None:
    """场景 A：cap ``foo:run`` ↔ command ``run`` 同名配对，默认 descriptor 装配到全部存量 Agent。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    registry = _FakeRegistry(
        {
            "c1": _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "proj-1"}]),
            "c2": _FakeHandle(agents=[{"agent_id": "b1", "name": "beta", "project_id": "proj-2"}]),
        }
    )
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, registry, specs=[spec])
        await unit.reconcile()

        for handle in registry.clusters.values():
            registers = _registers(handle)
            assert len(registers) == 1
            payload = registers[0]
            assert payload["agent_name"] == handle.agents[0]["name"]
            assert payload["project_id"] == handle.agents[0]["project_id"]
            assert payload["ability"]["ability_type"] == "command_proxy"
            params = payload["ability"]["params"]
            assert params["tool_name"] == "run"
            assert params["command"] == "run"
            assert params["require_hitl"] is True
            assert params["parameters"] == {
                "type": "object",
                "properties": {"payload": {"type": "object"}},
            }
        assert unit._assembled == {("c1", "a1"): {"run"}, ("c2", "b1"): {"run"}}  # noqa: SLF001


async def test_scenario_a_capability_without_matching_command_skipped() -> None:
    """场景 A 配对失败（name 段无同名命令）→ 不装配 + 不炸。"""
    spec = _spec("foo", provides={"commands": ["other"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[spec])
        await unit.reconcile()
        assert _registers(handle) == []
        assert not unit._assembled  # noqa: SLF001


# ----------------------------------------------------------------
# 场景 B：包装插件服务映射
# ----------------------------------------------------------------


async def test_scenario_b_wrapper_service_assembles() -> None:
    """场景 B：P1 只给命令，P2 经 ABILITY_BRIDGE 补全能力包装。"""
    command_spec = _spec("foo", provides={"commands": ["foo_run"]})
    wrap_spec = _spec("wrap", provides={"capabilities": ["wrap:foo"]})
    service = _BridgeService(
        {
            "wrap:foo": {
                "tool_name": "foo",
                "description": "run foo",
                "parameters": {"type": "object", "properties": {}},
                "command": "foo_run",
                "require_hitl": True,
                "timeout": None,
            }
        }
    )
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(
            ctx,
            _FakeRegistry({"c1": handle}),
            specs=[command_spec, wrap_spec],
            service=service,
        )
        await unit.reconcile()
        registers = _registers(handle)
        assert len(registers) == 1
        params = registers[0]["ability"]["params"]
        assert params["tool_name"] == "foo"
        assert params["command"] == "foo_run"
        assert params["description"] == "run foo"


async def test_scenario_b_service_overrides_scenario_a_default() -> None:
    """同一 capability 有包装映射时服务优先，不落场景 A 默认。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    service = _BridgeService({"foo:run": {"tool_name": "renamed", "command": "run"}})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(
            ctx, _FakeRegistry({"c1": handle}), specs=[spec], service=service
        )
        await unit.reconcile()
        registers = _registers(handle)
        assert [r["ability"]["params"]["tool_name"] for r in registers] == ["renamed"]


async def test_scenario_b_missing_command_plugin_skips() -> None:
    """P1 缺席：服务映射的目标命令不在场 → 不装配（不半装配）。"""
    service = _BridgeService({"wrap:foo": {"tool_name": "foo", "command": "foo_run"}})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), service=service)
        await unit.reconcile()
        assert _registers(handle) == []
        assert not unit._assembled  # noqa: SLF001


# ----------------------------------------------------------------
# 撤销路径
# ----------------------------------------------------------------


async def test_wrapper_unload_disassembles() -> None:
    """P2 卸载：服务失活 → 重算 diff → unregister_ability。"""
    command_spec = _spec("foo", provides={"commands": ["foo_run"]})
    service = _BridgeService({"wrap:foo": {"tool_name": "foo", "command": "foo_run"}})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[command_spec])
        fibers: dict[str, Any] = {}
        await mount_dynamic_unit(ctx, _BridgeProviderUnit("wrap", service), fibers)
        await unit.reconcile()
        assert [r["ability"]["params"]["tool_name"] for r in _registers(handle)] == ["foo"]

        # P2 卸载 → ABILITY_BRIDGE 服务随 fiber dispose 失活
        await unmount_unit(fibers, "wrap")
        await unit.reconcile()
        unregisters = _unregisters(handle)
        assert len(unregisters) == 1
        assert unregisters[0]["ability_name"] == "foo"
        assert not unit._assembled  # noqa: SLF001


async def test_crash_handler_notifies_bridge() -> None:
    """插件崩溃 → crash handler 通知重算 → 已装配工具撤销。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    registry = _FakeRegistry(
        {"c1": _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])}
    )
    async with Context() as ctx:
        await _mount_bridge(ctx, registry)
        discovered = _discovered((spec, lambda: _CommandPluginUnit("foo", "run", crash=True)))
        route_registry = UnitRouteRegistry()
        fibers: dict[str, Any] = {}
        session = ctx.get(PLUGIN_SESSION_SERVICE.name, strict=False)
        report = await mount_enabled_plugins(
            ctx,
            _config(["foo"]),
            discovered=discovered,
            assemblies=[("p1", PluginAssembly(enabled=["foo"]))],
            route_registry=route_registry,
            fibers=fibers,
            state=session.state,
            session=session,
        )
        assert report.by_status("mounted")
        handle = registry.clusters["c1"]
        # 挂载尾部钩子已触发重算：场景 A 工具装配
        assert [r["ability"]["params"]["tool_name"] for r in _registers(handle)] == ["run"]

        # 触发命令崩溃 → unmount + 通知重算 → 撤销
        await ctx.serial("command/run", {})
        unregisters = _unregisters(handle)
        assert len(unregisters) == 1
        assert unregisters[0]["ability_name"] == "run"
        assert not session.state.specs


async def test_mount_hook_fires_reconcile() -> None:
    """挂载成功路径尾部钩子：无需手工 reconcile 即装配。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        await _mount_bridge(ctx, _FakeRegistry({"c1": handle}))
        discovered = _discovered((spec, lambda: _CommandPluginUnit("foo", "run")))
        session = ctx.get(PLUGIN_SESSION_SERVICE.name, strict=False)
        await mount_enabled_plugins(
            ctx,
            _config(["foo"]),
            discovered=discovered,
            assemblies=[("p1", PluginAssembly(enabled=["foo"]))],
            route_registry=UnitRouteRegistry(),
            fibers={},
            state=session.state,
            session=session,
        )
        assert [r["ability"]["params"]["tool_name"] for r in _registers(handle)] == ["run"]


async def test_untrusted_plugin_not_assembled() -> None:
    """信任闸外的插件不进 specs → 不装配（零隐式）。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        await _mount_bridge(ctx, _FakeRegistry({"c1": handle}))
        discovered = _discovered((spec, lambda: _CommandPluginUnit("foo", "run")))
        session = ctx.get(PLUGIN_SESSION_SERVICE.name, strict=False)
        report = await mount_enabled_plugins(
            ctx,
            _config([]),  # foo 未信任
            discovered=discovered,
            assemblies=[("p1", PluginAssembly(enabled=["foo"]))],
            route_registry=UnitRouteRegistry(),
            fibers={},
            state=session.state,
            session=session,
        )
        assert report.by_status("not_trusted")
        assert _registers(handle) == []


# ----------------------------------------------------------------
# 事件联动与幂等
# ----------------------------------------------------------------


async def test_spawn_event_assembles_for_new_agent() -> None:
    """agent_spawned 事件触发全量重算：后续 spawn 的 Agent 可见（Q1 过渡语义）。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[spec])
        await unit.reconcile()
        assert len(_registers(handle)) == 1

        handle.agents.append({"agent_id": "a2", "name": "beta", "project_id": "p"})
        await ctx.parallel("core:agent_spawned", {"name": "beta", "agent_id": "a2"})
        registers = _registers(handle)
        assert {r["agent_id"] for r in registers} == {"a1", "a2"}


async def test_terminate_event_prunes_ledger() -> None:
    """agent_terminated 事件剪除账本；同 agent 再 spawn 时重装配。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[spec])
        await unit.reconcile()
        assert unit._assembled == {("c1", "a1"): {"run"}}  # noqa: SLF001

        await ctx.parallel("core:agent_terminated", {"name": "alpha", "agent_id": "a1"})
        assert not unit._assembled  # noqa: SLF001
        # 账本已剪除 → 重算视为新装配（幂等 diff 不依赖 Core 侧残留）
        handle.dispatched.clear()
        await unit.reconcile()
        assert len(_registers(handle)) == 1


async def test_reconcile_idempotent() -> None:
    """状态未变的重算不重复 dispatch。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[spec])
        await unit.reconcile()
        await unit.reconcile()
        assert len(_registers(handle)) == 1
        assert len(_unregisters(handle)) == 0


async def test_failed_register_not_ledgered_and_retried() -> None:
    """register 回执失败不入账本，下次重算重试。"""
    spec = _spec("foo", provides={"commands": ["run"], "capabilities": ["foo:run"]})
    handle = _FakeHandle(agents=[{"agent_id": "a1", "name": "alpha", "project_id": "p"}])
    handle.fail_next = True
    async with Context() as ctx:
        unit = await _mount_bridge(ctx, _FakeRegistry({"c1": handle}), specs=[spec])
        await unit.reconcile()
        assert not unit._assembled  # noqa: SLF001

        await unit.reconcile()
        assert unit._assembled == {("c1", "a1"): {"run"}}  # noqa: SLF001
        assert len(_registers(handle)) == 2


async def test_no_registry_mounted_is_noop() -> None:
    """registry 不在场（coexistence）时 reconcile 静默跳过。"""
    async with Context() as ctx:
        ctx.provide(PLUGIN_SESSION_SERVICE.name, PluginSession(PluginSessionState()))
        unit = AbilityBridgeUnit()
        # registry 缺席 → 不能经 mount_dynamic_unit 挂载（inject 死等），
        # 直接 init 验证 reconcile 的 registry 防御分支
        await unit.init(ctx)
        await unit.reconcile()  # 不应抛异常
