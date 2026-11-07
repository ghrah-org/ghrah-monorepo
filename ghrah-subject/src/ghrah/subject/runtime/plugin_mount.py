# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""插件装配链：启动期一次性挂载已启用插件。

步骤：发现（调用方传入）→ 信任闸（Subject ``plugin_trust.trust_set``，未信任
即拒 + 日志；跳闸直接在 Project 声明 = 拒）→ 聚合 enabled（多 project 保序
并集）→ ``sort_for_mount`` 排序 → 形状校验 → unit 工厂解析
→ ``mount_dynamic_unit``（exclusive 命令 + 崩溃 wrapper + timeout spec）
→ ``PluginSessionState`` 登记（negotiator 输入源）。

不做热装配：ProjectRecord.plugins 变更不触发自动挂/卸（归后续 reconciler）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ghrah.plugin.assembly import PluginAssembly, validate_assembly
from ghrah.plugin.negotiator import PythonHalfInfo, negotiate, python_half_from_spec
from ghrah.plugin.registry import sort_for_mount
from ghrah.plugin.spec import PluginSpec

from ghrah.subject.runtime.ouroboros_bridge import (
    PluginCrashCallback,
    mount_dynamic_unit,
    unmount_unit,
)
from ghrah.subject.runtime.plugin_session import PluginSession
from ghrah.subject.unit.base import SubjectUnit

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ghrah.plugin.loader import DiscoveredPlugin
    from ouroboros import Context, Fiber

    from ghrah.subject.config import SubjectConfig
    from ghrah.subject.runtime.route_registry import UnitRouteRegistry

__all__ = [
    "CheckerSink",
    "PluginMountOutcome",
    "PluginMountReport",
    "PluginSessionState",
    "mount_enabled_plugins",
]

logger = logging.getLogger(__name__)

# checker 装配 sink：(plugin_id, {checker_name: checker}) → 由装配层注入注册/注销。
# 崩溃/卸载路径回调空 dict 注销（防死插件残留，否则归因失真）。
if TYPE_CHECKING:
    CheckerSink = Callable[[str, dict[str, Any]], Awaitable[None]]
else:
    CheckerSink = Any


@dataclass
class PluginSessionState:
    """协商会话状态：已挂载插件 spec 集（negotiator 输入源）。

    由装配链持有并注入 ``PluginSession``；崩溃卸载路径同步移除（插件死了
    清单变了）。
    """

    specs: dict[str, PluginSpec] = field(default_factory=dict)

    def snapshot(self) -> list[PythonHalfInfo]:
        """构造 Python 半快照（instances 恒空：实例展开未启用）。"""
        return [python_half_from_spec(spec) for spec in self.specs.values()]

    def negotiate(self, ts: list[Any]) -> Any:
        """连接级与变更级共用的协商入口（纯函数，零状态分歧）。"""
        return negotiate(python=self.snapshot(), ts=ts)


@dataclass
class PluginMountOutcome:
    """单插件挂载结果。

    Attributes:
        plugin_id: 插件身份标识。
        status: mounted / not_trusted / not_discovered / spec_only /
            multi_instance_unsupported / invalid_assembly / mount_failed。
        detail: 人读错误摘要（失败时）。
    """

    plugin_id: str
    status: str
    detail: str = ""


@dataclass
class PluginMountReport:
    """装配链整体报告（逐插件结果，不炸整体）。"""

    outcomes: list[PluginMountOutcome] = field(default_factory=list)

    def by_status(self, status: str) -> list[PluginMountOutcome]:
        return [o for o in self.outcomes if o.status == status]


def _resolve_unit_factory(spec: PluginSpec, entry: DiscoveredPlugin) -> SubjectUnit | None:
    """unit 工厂解析（消费发现期 duck-typing 探测结果）。

    ``DiscoveredPlugin.unit_factory``（发现期探测：spec 子类类属性
    ``unit_factory`` / ``create_unit``，或 entry point 模块的同名属性）→
    零参调用构造 SubjectUnit；探测不到或构造失败/类型不符 → None
    （spec-only：可协商不可挂载）。
    """
    factory = entry.unit_factory
    if factory is None:
        return None
    try:
        unit = factory()
    except Exception:  # noqa: BLE001
        logger.warning("unit factory for plugin '%s' failed to instantiate.", spec.plugin_id)
        return None
    if isinstance(unit, SubjectUnit):
        return unit
    logger.warning(
        "unit factory for plugin '%s' did not produce a SubjectUnit (got %s).",
        spec.plugin_id,
        type(unit).__name__,
    )
    return None


async def mount_enabled_plugins(
    ctx: Context,
    config: SubjectConfig,
    *,
    discovered: list[DiscoveredPlugin],
    assemblies: list[tuple[str, PluginAssembly]],
    route_registry: UnitRouteRegistry,
    fibers: dict[str, Fiber],
    state: PluginSessionState,
    session: PluginSession,
    project_statuses: dict[str, str] | None = None,
    on_checkers: CheckerSink | None = None,
) -> PluginMountReport:
    """启动期装配已启用插件（信任闸 → 聚合 → 排序 → 校验 → 挂载）。

    Args:
        discovered: 发现成功的插件清单（发现 ≠ 启用）。
        assemblies: (project_id, assembly) 清单；仅 status=ACTIVE 的 project
            参与（``project_statuses`` 提供 status 映射；缺省视为 ACTIVE）。
        route_registry: owner 表（互斥裁决）。
        fibers: 挂载登记表（与 builtin/third_party 共享）。
        state: 协商会话状态（成功挂载登记）。
        session: 协商 unit（崩溃/挂载后广播回调）。
        project_statuses: project_id → status（ACTIVE 之外不装配）。
        on_checkers: checker 装配 sink（挂载成功 → 注册；崩溃/卸载 → 空 dict 注销）。
    """

    report = PluginMountReport()
    trust_set = config.plugin_trust.trust_set
    by_id = {d.spec.plugin_id: d for d in discovered}
    statuses = project_statuses or {}

    # 聚合 enabled（多 project 保序并集：project 序 × enabled 序）
    enabled_order: list[str] = []
    seen: set[str] = set()
    merged = PluginAssembly()
    for project_id, assembly in assemblies:
        if statuses.get(project_id, "active").lower() != "active":
            continue
        for plugin_id in assembly.enabled:
            if plugin_id not in seen:
                seen.add(plugin_id)
                enabled_order.append(plugin_id)
        for plugin_id, instances in assembly.instances.items():
            merged.instances.setdefault(plugin_id, {}).update(instances)
    merged.enabled = enabled_order

    # 信任闸（跳闸直接在 Project 声明 = 拒）
    candidates: list[PluginSpec] = []
    for plugin_id in enabled_order:
        if plugin_id not in trust_set:
            logger.warning(
                "Plugin '%s' is enabled in project config but not in the trust list; "
                "refusing to mount.",
                plugin_id,
            )
            report.outcomes.append(
                PluginMountOutcome(plugin_id, "not_trusted", "not in plugin_trust discoverable")
            )
            continue
        entry = by_id.get(plugin_id)
        if entry is None:
            logger.warning("Plugin '%s' is trusted and enabled but was not discovered.", plugin_id)
            report.outcomes.append(PluginMountOutcome(plugin_id, "not_discovered"))
            continue
        candidates.append(entry.spec)

    # 装配排序（after 声明；被引用缺失不阻塞）
    for spec in sort_for_mount(candidates):
        outcome = await _mount_one(
            ctx,
            spec=spec,
            entry=by_id[spec.plugin_id],
            assembly=merged,
            route_registry=route_registry,
            fibers=fibers,
            state=state,
            session=session,
            on_checkers=on_checkers,
        )
        report.outcomes.append(outcome)
        if outcome.status == "mounted":
            logger.info("Plugin '%s' mounted.", spec.plugin_id)

    mounted = [o.plugin_id for o in report.outcomes if o.status == "mounted"]
    if mounted:
        await session.broadcast_negotiated(mounted)
    return report


async def _mount_one(
    ctx: Context,
    *,
    spec: PluginSpec,
    entry: DiscoveredPlugin,
    assembly: PluginAssembly,
    route_registry: UnitRouteRegistry,
    fibers: dict[str, Fiber],
    state: PluginSessionState,
    session: PluginSession,
    on_checkers: CheckerSink | None = None,
) -> PluginMountOutcome:
    """单插件挂载：形状校验 → multi_instance 拒挂 → 工厂探测 → 挂载 → checker 注册。"""

    # 形状校验（只看本插件相关条目）
    scoped = PluginAssembly(
        enabled=[spec.plugin_id] if spec.plugin_id in assembly.enabled else [],
        instances={spec.plugin_id: assembly.instances.get(spec.plugin_id, {})}
        if spec.plugin_id in assembly.instances
        else {},
    )
    errors = validate_assembly(spec, scoped)
    # instances 条目属未启用插件等跨插件错误不在本插件职责内（聚合面校验归后续 reconciler）
    errors = [e for e in errors if spec.plugin_id in e]
    if errors:
        return PluginMountOutcome(spec.plugin_id, "invalid_assembly", "; ".join(errors))

    # multi_instance 拒挂（实例展开/路由未启用）
    if spec.multi_instance:
        return PluginMountOutcome(
            spec.plugin_id,
            "multi_instance_unsupported",
            "multi_instance plugin requires instance expansion (not yet supported)",
        )

    unit = _resolve_unit_factory(spec, entry)
    if unit is None:
        logger.info(
            "Plugin '%s' is spec-only (no unit factory discovered); negotiable but not mountable.",
            spec.plugin_id,
        )
        return PluginMountOutcome(spec.plugin_id, "spec_only")

    plugin_id = spec.plugin_id
    on_crash = _make_crash_handler(
        plugin_id=plugin_id,
        fibers=fibers,
        route_registry=route_registry,
        state=state,
        session=session,
        on_checkers=on_checkers,
    )
    try:
        await mount_dynamic_unit(
            ctx,
            unit,
            fibers,
            name=plugin_id,
            route_registry=route_registry,
            exclusive=True,
            plugin_spec=spec,
            on_crash=on_crash,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Plugin '%s' mount failed: %s", plugin_id, exc)
        return PluginMountOutcome(plugin_id, "mount_failed", str(exc))
    state.specs[plugin_id] = spec
    await _register_plugin_checkers(plugin_id, spec, entry, on_checkers)
    return PluginMountOutcome(plugin_id, "mounted")


async def _register_plugin_checkers(
    plugin_id: str,
    spec: PluginSpec,
    entry: DiscoveredPlugin,
    on_checkers: CheckerSink | None,
) -> None:
    """挂载成功后按 ``spec.provides.checkers`` 逐个经工厂构造并回调注册。

    工厂缺失或构造失败：该 checker 不注册（内核 submit 按缺口记录候选），
    其余 checker 不受影响；sink 不在场（unit 未挂载）→ 日志降级跳过。
    """

    if on_checkers is None or not spec.provides.checkers:
        return
    registered: dict[str, Any] = {}
    if entry.checker_factory is None:
        logger.info(
            "Plugin '%s' declares checkers %s but exposes no checker factory.",
            plugin_id,
            spec.provides.checkers,
        )
    else:
        for name in spec.provides.checkers:
            try:
                registered[name] = entry.checker_factory(name)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Plugin '%s' checker factory failed for '%s'; skipped.",
                    plugin_id,
                    name,
                )
    if registered:
        logger.info("Plugin '%s' registered checkers: %s", plugin_id, sorted(registered))
    await on_checkers(plugin_id, registered)


def _make_crash_handler(
    *,
    plugin_id: str,
    fibers: dict[str, Fiber],
    route_registry: UnitRouteRegistry,
    state: PluginSessionState,
    session: PluginSession,
    on_checkers: CheckerSink | None = None,
) -> PluginCrashCallback:
    """崩溃路径：failed 标记 + 自动 unmount + owner 清理 + checker 注销 + 双广播。"""

    async def on_crash(crashed_id: str, command: str, error: str) -> None:
        logger.warning(
            "Plugin '%s' crashed in command '%s': %s — unmounting.", crashed_id, command, error
        )
        await unmount_unit(fibers, crashed_id, route_registry=route_registry)
        crashed_spec = state.specs.pop(crashed_id, None)
        if on_checkers is not None:
            # 空 dict = 注销语义：sink 以挂载期记录的 checker 名为准清理
            if crashed_spec is not None:
                await on_checkers(
                    crashed_id,
                    {name: None for name in crashed_spec.provides.checkers},  # type: ignore[dict-item]
                )
            else:
                await on_checkers(crashed_id, {})
        from ghrah.protocol.payloads.plugin import PluginCrashedPayload

        await session.publish_event(
            "plugin_crashed",
            PluginCrashedPayload(plugin_id=crashed_id, command=command, error=error).model_dump(),
        )
        # 插件死了清单变了 → 重协商信号
        await session.broadcast_negotiated([crashed_id])

    return on_crash
