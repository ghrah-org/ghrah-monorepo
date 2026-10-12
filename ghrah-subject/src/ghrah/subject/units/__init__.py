# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Built-in Subject unit registration (Ouroboros 形态)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ouroboros import Context, Fiber  # type: ignore[import-untyped]

    from ghrah.subject.config import SubjectConfig
    from ghrah.subject.runtime.route_registry import UnitRouteRegistry
    from ghrah.subject.unit.base import SubjectUnit

__all__ = ["mount_builtin_units"]


async def mount_builtin_units(
    ctx: Context,
    config: SubjectConfig,
    *,
    profile: str = "coexistence",
    core_cluster_unit: SubjectUnit | None = None,
    route_registry: UnitRouteRegistry | None = None,
) -> dict[str, Fiber]:
    """Mount built-in units as Ouroboros plugins.

    聚合裁决后收敛：默认配置（``taskstore`` 与 ``room_filter`` 两个可选切片
    均启用）下 coexistence = 7（sandbox/manifest_store/ledger/workspace +
    taskstore + task/desired_state）；full = 13（coexistence 7 +
    websocket_observer_endpoint/core_cluster_registry/project/room/recovery +
    room_filter）。关闭 ``config.taskstore.enabled`` / ``config.room_filter.enabled``
    时对应 unit 不挂载。**逐个挂载并等 ACTIVE**（对齐旧 engine 顺序 start 语义；
    实测并发挂载会让多个 store 同时打开同一 SQLite 文件触发 ``database is locked``）。

    RoomUnit 在 ProjectUnit 之后（requires PROJECT_MANAGER）、CoreUnit 经
    registry 懒挂载必然在后——满足「RoomUnit 先于 CoreUnit 挂载」的
    装配顺序要求（send 回路唯一跨 unit 耦合点）。

    ``core_cluster_unit`` 为可选替换 registry unit（仅 full profile 生效；
    None = 默认 CoreClusterRegistryUnit(config)）。程序化注入接缝——
    典型用途为携带 ``llm_factory`` 的等价配置实例（测试/嵌入方），不影响
    生产默认路径。

    ``route_registry``：owner 表登记面（builtin 非互斥，仅记名）；插件
    挂载路径据此互斥裁决。
    """

    if profile not in {"coexistence", "full"}:
        raise ValueError(f"Unknown built-in Subject unit profile: {profile}")

    from ghrah.subject.runtime.ouroboros_bridge import mount_unit, wait_active
    from ghrah.subject.units.ledger import LedgerUnit
    from ghrah.subject.units.manifest_store import ManifestStoreUnit
    from ghrah.subject.units.recovery import DesiredStateUnit
    from ghrah.subject.units.sandbox import SandboxUnit
    from ghrah.subject.units.task import TaskUnit
    from ghrah.subject.units.taskstore import TaskStoreUnit
    from ghrah.subject.units.workspace import WorkspaceUnit

    units: list[SubjectUnit] = [
        SandboxUnit(config),
        ManifestStoreUnit(config),
        LedgerUnit(config),
        WorkspaceUnit(config),
    ]

    # 任务归因内核 unit（可选组件）：enabled=False 时完全不挂载（零隐式；归因
    # 命令回落 Unknown command）。当前经 builtin 链挂载属**待纠正偏离**——目标
    # 形态是插件（发现 + 信任闸 + Project 装配清单驱动）；此处置于 TaskUnit 之前
    # （builtin 链挂载序 = serial 序，TaskStoreUnit 先注册归因命令）、插件装配
    # 之前——checker 服务在插件接线时已就绪。
    if config.taskstore.enabled:
        units.append(TaskStoreUnit(config))

    units.extend([TaskUnit(config), DesiredStateUnit(config)])

    if profile == "full":
        from ghrah.subject.units.core_cluster import CoreClusterRegistryUnit
        from ghrah.subject.units.project import ProjectUnit
        from ghrah.subject.units.recovery import RecoveryUnit
        from ghrah.subject.units.room import RoomUnit
        from ghrah.subject.units.room_filter import RoomFilterUnit
        from ghrah.subject.units.websocket_observer_endpoint import (
            WebSocketObserverEndpointUnit,
        )

        units.extend(
            [
                WebSocketObserverEndpointUnit(config),
                core_cluster_unit
                if core_cluster_unit is not None
                else CoreClusterRegistryUnit(config),
                ProjectUnit(config),
                RoomUnit(config),
                RecoveryUnit(config),
            ]
        )
        # Room Filter：requires ROOM_MANAGER（RoomUnit 之后挂载）；
        # enabled=False 时完全不挂载（零隐式行为，配置切片控制）
        if config.room_filter.enabled:
            units.append(RoomFilterUnit(config))

        # 能力桥：requires CORE_CLUSTER_REGISTRY（registry 之后）；specs/
        # ABILITY_BRIDGE 服务均 reconcile 时懒解析，挂载序与插件阶段无关
        # （初始装配由 plugin_mount 挂载尾部钩子触发重算）
        from ghrah.subject.runtime.ability_bridge import AbilityBridgeUnit

        units.append(AbilityBridgeUnit())

    fibers: dict[str, Fiber] = {}
    for unit in units:
        fiber = ctx.plugin(mount_unit(unit, route_registry=route_registry))
        await wait_active(fiber, timeout=10.0)
        fibers[unit.meta.name] = fiber
    return fibers
