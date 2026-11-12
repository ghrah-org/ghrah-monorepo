# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Subject 装配层（Ouroboros 形态）。

``assemble_subject(ctx, config, profile)``：
1. ``mount_builtin_units``（默认配置 coexistence=7 / full=13，逐个挂载等 ACTIVE，
   规避 SQLite 并发开库锁）；
2. 第三方 unit allowlist 挂载（``mount_third_party_units``）；
3. 插件装配（``mount_enabled_plugins``：信任闸 → Project enabled 聚合 →
   排序 → 挂载）+ ``PluginSession`` 挂载（协商双重触发）；
4. reconcile 启动末尾显式触发（对齐旧 ``engine.start()`` 末尾语义；
   Ouroboros 无 ``internal/status`` 事件，装配完成即全部 ACTIVE）。

Context 所有权归调用方（``async with Context() as ctx:``）；退出时
dispose 全部 fiber（unit.stop 逆序由 Ouroboros 负责）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ghrah.plugin.loader import discover_plugins
from ouroboros import FiberState  # type: ignore[import-untyped]

from ghrah.subject.runtime.plugin_mount import PluginSessionState, mount_enabled_plugins
from ghrah.subject.runtime.plugin_session import PluginSession
from ghrah.subject.runtime.route_registry import UnitRouteRegistry
from ghrah.subject.runtime.service_keys import PROJECT_STORE, RECONCILIATION_SERVICE
from ghrah.subject.runtime.third_party import mount_third_party_units
from ghrah.subject.units import mount_builtin_units

if TYPE_CHECKING:
    from ouroboros import Context, Fiber

    from ghrah.subject.config import SubjectConfig
    from ghrah.subject.unit.base import SubjectUnit

__all__ = ["assemble_subject"]

logger = logging.getLogger(__name__)

#: 启动期后台任务：持有强引用，避免 reconcile 被 GC 回收。
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def _reconcile_in_background(service: Any) -> None:
    """后台执行启动期 reconcile；失败只记日志，不影响服务可用。"""
    try:
        await service.reconcile()
    except Exception:
        logger.exception("reconcile on start failed (non-fatal)")


async def assemble_subject(
    ctx: Context,
    config: SubjectConfig,
    *,
    profile: str = "full",
    core_cluster_unit: SubjectUnit | None = None,
) -> dict[str, Fiber]:
    """装配 Subject 运行时并触发启动期 reconcile（若启用且 recovery unit 在场）。

    ``core_cluster_unit`` 透传 ``mount_builtin_units`` 同名参数（可选替换
    registry unit；程序化注入接缝，如携带 llm_factory 的测试/嵌入装配）。
    """

    route_registry = UnitRouteRegistry()
    fibers = await mount_builtin_units(
        ctx,
        config,
        profile=profile,
        core_cluster_unit=core_cluster_unit,
        route_registry=route_registry,
    )
    fibers.update(await mount_third_party_units(ctx, config, route_registry=route_registry))

    await _assemble_plugins(ctx, config, fibers, route_registry)

    if config.recovery.enabled and config.recovery.reconcile_on_start:
        reconcile_service = ctx.get(RECONCILIATION_SERVICE.name, strict=False)
        if reconcile_service is not None:
            # 后台执行：恢复可能拉起多个 Agent（每个都要解析 manifest、核定授权、
            # 绑定 run），阻塞装配会让服务晚几秒才监听端口。先能服务，再补状态。
            task = asyncio.create_task(
                _reconcile_in_background(reconcile_service),
                name="subject-reconcile-on-start",
            )
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_BACKGROUND_TASKS.discard)
        else:
            logger.info(
                "reconcile_on_start enabled but recovery unit not mounted (profile=%s)",
                profile,
            )

    logger.info("Subject assembled (profile=%s, units=%d)", profile, len(fibers))

    # 启动期健康检查：非 root fiber 停留 PENDING = inject 名单错/依赖缺失
    # （承接旧 topological_sort「启动即发现配置错误」语义；registry 懒挂载
    # 的 CoreUnit fiber 不在 fibers 表内，不受此检查影响）
    pending = [name for name, fiber in fibers.items() if fiber.state is FiberState.PENDING]
    if pending:
        logger.warning(
            "startup health check: %d fiber(s) still PENDING after mount: %s",
            len(pending),
            ", ".join(sorted(pending)),
        )
    return fibers


async def _assemble_plugins(
    ctx: Context,
    config: SubjectConfig,
    fibers: dict[str, Fiber],
    route_registry: UnitRouteRegistry,
) -> None:
    """插件装配：PluginSession 挂载 + 启动期一次性 mount_enabled_plugins。

    Project 装配清单经 ProjectStore 直读（ProjectUnit 已在 builtin 挂载）；
    store 不在场（如 coexistence profile 无 project unit）时跳过装配。
    """

    state = PluginSessionState()
    session = PluginSession(state)
    from ghrah.subject.runtime.ouroboros_bridge import mount_dynamic_unit

    await mount_dynamic_unit(ctx, session, fibers, route_registry=route_registry)

    # checker 装配 sink：解析 TASKSTORE_CHECKERS（unit 不在场 → 日志降级跳过）。
    # 协议：(plugin_id, {name: checker}) 注册；值 None 或空 dict → 注销（崩溃/卸载路径）。
    from ghrah.subject.runtime.service_keys import TASKSTORE_CHECKERS

    checkers_registry = ctx.get(TASKSTORE_CHECKERS.name, strict=False)

    async def on_checkers(plugin_id: str, checkers: dict[str, Any]) -> None:
        if checkers_registry is None:
            if checkers:
                logger.info(
                    "Plugin '%s' provides %d checker(s) but taskstore unit is not mounted; "
                    "skipped.",
                    plugin_id,
                    len(checkers),
                )
            return
        for name, checker in checkers.items():
            if checker is None:
                checkers_registry.unregister(name)
                logger.info("checker '%s' unregistered (plugin '%s' down).", name, plugin_id)
            else:
                checkers_registry.register(name, checker)
                logger.info("checker '%s' registered via plugin '%s'.", name, plugin_id)

    assemblies = []
    statuses: dict[str, str] = {}
    store = ctx.get(PROJECT_STORE.name, strict=False)
    if store is not None:
        records = await store.list(archived=None)
        for record in records:
            if record.config is not None:
                assemblies.append((record.project_id, record.config.plugins))
            statuses[record.project_id] = record.status.value
    discovered, issues = discover_plugins()
    for issue in issues:
        logger.warning("Plugin discovery issue '%s': %s", issue.entry_point, issue.error)
    report = await mount_enabled_plugins(
        ctx,
        config,
        discovered=discovered,
        assemblies=assemblies,
        route_registry=route_registry,
        fibers=fibers,
        state=state,
        session=session,
        project_statuses=statuses,
        on_checkers=on_checkers if checkers_registry is not None else None,
    )
    failed = [o for o in report.outcomes if o.status not in ("mounted", "spec_only")]
    if failed:
        logger.warning(
            "Plugin assembly: %d issue(s): %s",
            len(failed),
            "; ".join(f"{o.plugin_id}={o.status}" for o in failed),
        )
