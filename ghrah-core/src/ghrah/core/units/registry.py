# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 自持的运行时注册表。

职责：按 ``project_id`` 创建、启动、复用 CoreUnit，并把命令转交给它。

这是「Agent 生命周期归 Core」的落地件。此前 CoreUnit 的创建与挂载都在
Subject 的集群注册表里，导致写能力无法在 Core 侧实现。CoreUnit 对宿主只要
求三个成员（见 :class:`UnitContext`），所以 Core 可以自行托管。

粒度与 CoreUnit 一致：**一个项目一个单元**，Agent 在单元内通过
``spawn_agent`` / ``terminate_agent`` 等命令增删。

**进程内语义**：注册表持有活动实例，不承担跨重启的持久化；持久化随 Subject
侧注册表迁移一并落地。
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ghrah.core.unit import CoreUnitConfig, create_core_unit
from ghrah.core.units.context import UnitContext

__all__ = ["RuntimeHandle", "RuntimeRegistry"]

type UnitFactory = Callable[[CoreUnitConfig], Any]
type ConfigFactory = Callable[[str], CoreUnitConfig]
type ManagerFactory = Callable[[str], Awaitable[Any]]
type ConfigHook = Callable[[str, CoreUnitConfig], Awaitable[CoreUnitConfig]]
type AbilityStoreFactory = Callable[[str], Awaitable[Any]]
type ManifestResolver = Callable[[str], Any]
type ManifestResolver = Callable[[str], Any]


@dataclass
class RuntimeHandle:
    """一个项目单元的活动实例。"""

    project_id: str
    run_id: str
    unit: Any
    context: UnitContext


class RuntimeRegistry:
    """按项目维护活动单元。

    Args:
        config_factory: 由装配注入的 CoreUnitConfig 构造器（携带部署设置）
        unit_factory: 单元构造器（默认 ``create_core_unit``，测试可替换）
        manager_factory: 项目消息管理器工厂（Agent 动态注册，可省略）
    """

    def __init__(
        self,
        *,
        config_factory: ConfigFactory,
        unit_factory: UnitFactory = create_core_unit,
        manager_factory: ManagerFactory | None = None,
        manifest_resolver: ManifestResolver | None = None,
        config_hook: ConfigHook | None = None,
        ability_store_factory: AbilityStoreFactory | None = None,
    ) -> None:
        self._config_factory = config_factory
        self._unit_factory = unit_factory
        self._manager_factory = manager_factory
        self._manifest_resolver = manifest_resolver
        self._config_hook = config_hook
        self._ability_store_factory = ability_store_factory
        self._units: dict[str, RuntimeHandle] = {}
        self._managers: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def set_ability_store_factory(self, factory: AbilityStoreFactory | None) -> None:
        """设置启动授权关系库工厂（装配方在构造后注入）。"""
        self._ability_store_factory = factory

    async def ensure(self, project_id: str) -> RuntimeHandle:
        """取（或懒启动）项目单元。

        Args:
            project_id: 项目标识

        Returns:
            运行时句柄

        Raises:
            ValueError: ``project_id`` 为空
        """
        if not project_id:
            raise ValueError("project_id required")
        handle = self._units.get(project_id)
        if handle is not None:
            return handle
        async with self._lock:
            handle = self._units.get(project_id)
            if handle is not None:
                return handle
            config = self._config_factory(project_id)
            if self._manager_factory is not None:
                manager = await self._manager_factory(project_id)
                self._managers[project_id] = manager
                config = replace(config, message_manager=manager)
            if self._config_hook is not None:
                config = await self._config_hook(project_id, config)
            if config.ability_store is None and self._ability_store_factory is not None:
                store = await self._ability_store_factory(project_id)
                config = replace(config, ability_store=store)
            projector = getattr(config, "room_projector", None)
            if projector is not None and config.message_manager is not None:
                configure = getattr(config.message_manager, "configure_room_projector", None)
                if configure is not None:
                    configure(projector)
            unit = self._unit_factory(config)
            context = UnitContext(request_id=project_id)
            await unit.init(context)
            try:
                await unit.start()
            except BaseException:
                await unit.stop()
                raise
            handle = RuntimeHandle(
                project_id=project_id,
                run_id=uuid.uuid4().hex,
                unit=unit,
                context=context,
            )
            self._units[project_id] = handle
            return handle

    async def resolve_manifest(self, manifest_ref: str) -> Any:
        """解析一个 manifest 引用。

        Args:
            manifest_ref: manifest 引用（如 ``agents.coder``）

        Returns:
            解析器返回值（通常是 manifest 对象）

        Raises:
            LookupError: 装配层没有提供解析源
        """
        if self._manifest_resolver is None:
            raise LookupError("manifest_source_unavailable")
        result = self._manifest_resolver(manifest_ref)
        if inspect.isawaitable(result):
            return await result
        return result

    def message_manager(self, project_id: str) -> Any:
        """取项目消息管理器；未启动时懒启动不可行，返回 ``None``。

        Args:
            project_id: 项目标识

        Returns:
            管理器实例；单元未启动时为 ``None``
        """
        return self._managers.get(project_id)

    async def retry_delivery(self, *, project_id: str, delivery_id: str) -> None:
        """重试一条失败投递（沿用消息管理器的重试规则）。"""
        await self.ensure(project_id)
        manager = self._managers.get(project_id)
        if manager is None:
            raise LookupError("message_manager_unavailable")
        await manager.retry(delivery_id)

    async def resume_delivery(self, *, project_id: str, delivery_id: str) -> None:
        """恢复一条中断投递。"""
        await self.ensure(project_id)
        manager = self._managers.get(project_id)
        if manager is None:
            raise LookupError("message_manager_unavailable")
        await manager.resume_delivery(delivery_id)

    async def recover_rooms(self, *, project_id: str) -> int:
        """修复 Room 投影，返回修复条数。"""
        await self.ensure(project_id)
        manager = self._managers.get(project_id)
        if manager is None:
            raise LookupError("message_manager_unavailable")
        return int(await manager.recover_room_projections())

    def get(self, project_id: str) -> RuntimeHandle | None:
        """取已启动的项目单元；未启动返回 ``None``。"""
        return self._units.get(project_id)

    async def handle_command(
        self,
        *,
        project_id: str,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """把命令转交给项目单元（懒启动）。

        Args:
            project_id: 项目标识
            command: 命令名
            payload: 命令载荷

        Returns:
            单元返回的结果字典

        Raises:
            RuntimeError: 单元返回非字典
        """
        handle = await self.ensure(project_id)
        result = await handle.unit.handle_command(command, payload, handle.context)
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected result for {command}")
        return result

    def tools_for(self, project_id: str, agent_id: str) -> tuple[str, ...] | None:
        """同步读取某 Agent 当前装载的工具名（离线返回 ``None``）。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            工具名元组；不在线时为 ``None``
        """
        handle = self._units.get(project_id)
        if handle is None:
            return None
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return None
        data = method(project_id, agent_id)
        if not isinstance(data, dict) or data.get("availability") != "online":
            return None
        tools = data.get("loaded_abilities")
        if not isinstance(tools, (list, tuple)):
            return None
        return tuple(str(item) for item in tools)

    def run_id_for(self, project_id: str, agent_id: str) -> str | None:
        """同步解析某 Agent 当前的生命周期 run（离线返回 ``None``）。

        能力关系库的 ``runs`` 回调是同步的，因此这里必须同步返回。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            run 标识；不在线时为 ``None``
        """
        handle = self._units.get(project_id)
        if handle is None:
            return None
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return None
        data = method(project_id, agent_id)
        if not isinstance(data, dict) or data.get("availability") != "online":
            return None
        value = data.get("incarnation_id")
        return str(value) if value else None

    async def get_entity_runtime(self, *, project_id: str, agent_id: str) -> dict[str, Any]:
        """读取 Agent 的运行时快照。

        单元把该方法暴露为普通方法（不在命令表里），因此这里直连调用。

        Args:
            project_id: 项目标识
            agent_id: 稳定 Agent ID

        Returns:
            单元返回的运行时快照字典
        """
        handle = self._units.get(project_id)
        if handle is None:
            return {"availability": "offline", "incarnation_id": None}
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return {"availability": "offline", "incarnation_id": None}
        result = method(project_id, agent_id)
        return result if isinstance(result, dict) else {"availability": "offline"}

    async def stop(self, project_id: str) -> bool:
        """停止并移除项目单元。

        Args:
            project_id: 项目标识

        Returns:
            是否确实停止了一个活动实例
        """
        async with self._lock:
            handle = self._units.pop(project_id, None)
        if handle is None:
            return False
        await handle.unit.stop()
        return True

    async def aclose(self) -> None:
        """停止全部项目单元。"""
        async with self._lock:
            handles = list(self._units.values())
            self._units.clear()
        for handle in handles:
            await handle.unit.stop()
