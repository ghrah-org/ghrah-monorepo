# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 自持的运行时注册表（单作用域）。

职责：为一个**作用域**创建、启动、复用 CoreUnit，并把命令转交给它。

作用域由构造参数 ``scope_id`` 固定：一个注册表只承载一个单元，不再按
``project_id`` 做多槽字典。作用域对 Core 是**不透明 token**（装配层通常传
Project 标识），只用于数据库分区、事件/命令的归属与日志关联。

这是「Agent 生命周期归 Core」的落地件。CoreUnit 对宿主只要求三个成员
（见 :class:`UnitContext`），所以 Core 可以自行托管。

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
type ConfigFactory = Callable[[], CoreUnitConfig]
type ManagerFactory = Callable[[], Awaitable[Any]]
type ConfigHook = Callable[[CoreUnitConfig], Awaitable[CoreUnitConfig]]
type AbilityStoreFactory = Callable[[], Awaitable[Any]]
type ManifestResolver = Callable[[str], Any]


@dataclass
class RuntimeHandle:
    """一个作用域单元的活动实例。"""

    scope_id: str
    run_id: str
    unit: Any
    context: UnitContext


class RuntimeRegistry:
    """维护单个作用域的活动单元。

    Args:
        scope_id: 不透明作用域 token（装配层注入，Core 不解释其含义）
        config_factory: 由装配注入的 CoreUnitConfig 构造器（携带部署设置）
        unit_factory: 单元构造器（默认 ``create_core_unit``，测试可替换）
        manager_factory: 消息管理器工厂（Agent 动态注册，可省略）
        manifest_resolver: manifest 解析源（可省略）
        config_hook: 单元创建前的异步配置钩子（注入 ability_store 等）
        ability_store_factory: 启动授权关系库工厂（装配方在构造后注入亦可）
        emit: 宿主事件投影回调
        serial: 宿主命令入口，由 Core 的受限桥转发
    """

    def __init__(
        self,
        *,
        scope_id: str,
        config_factory: ConfigFactory,
        unit_factory: UnitFactory = create_core_unit,
        manager_factory: ManagerFactory | None = None,
        manifest_resolver: ManifestResolver | None = None,
        config_hook: ConfigHook | None = None,
        ability_store_factory: AbilityStoreFactory | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        serial: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._scope_id = scope_id
        self._config_factory = config_factory
        self._unit_factory = unit_factory
        self._manager_factory = manager_factory
        self._manifest_resolver = manifest_resolver
        self._config_hook = config_hook
        self._ability_store_factory = ability_store_factory
        self._emit = emit
        self._serial = serial
        self._handle: RuntimeHandle | None = None
        self._manager: Any = None
        self._lock = asyncio.Lock()

    @property
    def scope(self) -> str:
        """本实例的作用域 token（不透明）。"""
        return self._scope_id

    def set_ability_store_factory(self, factory: AbilityStoreFactory | None) -> None:
        """设置启动授权关系库工厂（装配方在构造后注入）。"""
        self._ability_store_factory = factory

    async def ensure(self) -> RuntimeHandle:
        """取（或懒启动）本作用域单元。

        Returns:
            运行时句柄

        Raises:
            RuntimeError: 单元启动失败（资源已回滚）
        """
        handle = self._handle
        if handle is not None:
            return handle
        async with self._lock:
            handle = self._handle
            if handle is not None:
                return handle
            config = self._config_factory()
            unit = None
            try:
                if self._manager_factory is not None:
                    manager = await self._manager_factory()
                    self._manager = manager
                    config = replace(config, message_manager=manager)
                if self._config_hook is not None:
                    config = await self._config_hook(config)
                if config.ability_store is None and self._ability_store_factory is not None:
                    store = await self._ability_store_factory()
                    config = replace(config, ability_store=store)
                if config.room_projector is not None and config.message_manager is not None:
                    config.message_manager.configure_room_projector(config.room_projector)
                unit = self._unit_factory(config)
                context = UnitContext(
                    request_id=self._scope_id, emit=self._emit, serial=self._serial
                )
                await unit.init(context)
                await unit.start()
            except BaseException:
                try:
                    if unit is not None:
                        await unit.stop()
                finally:
                    await self._close_resources()
                raise
            handle = RuntimeHandle(
                scope_id=self._scope_id,
                run_id=uuid.uuid4().hex,
                unit=unit,
                context=context,
            )
            self._handle = handle
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

    def message_manager(self) -> Any:
        """取本作用域的消息管理器；未启动时返回 ``None``。"""
        return self._manager

    async def retry_delivery(self, *, delivery_id: str) -> None:
        """重试一条失败投递（沿用消息管理器的重试规则）。"""
        await self.ensure()
        if self._manager is None:
            raise LookupError("message_manager_unavailable")
        await self._manager.retry(delivery_id)

    async def resume_delivery(self, *, delivery_id: str) -> None:
        """恢复一条中断投递。"""
        await self.ensure()
        if self._manager is None:
            raise LookupError("message_manager_unavailable")
        await self._manager.resume_delivery(delivery_id)

    async def recover_rooms(self) -> int:
        """修复 Room 投影，返回修复条数。"""
        await self.ensure()
        if self._manager is None:
            raise LookupError("message_manager_unavailable")
        return int(await self._manager.recover_room_projections())

    def get(self) -> RuntimeHandle | None:
        """取已启动的单元；未启动返回 ``None``。"""
        return self._handle

    async def handle_command(self, *, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """把命令转交给本作用域单元（懒启动）。

        Args:
            command: 命令名
            payload: 命令载荷（作用域由单元自身补齐，调用方不传）

        Returns:
            单元返回的结果字典

        Raises:
            RuntimeError: 单元返回非字典
        """
        handle = await self.ensure()
        result = await handle.unit.handle_command(command, payload, handle.context)
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected result for {command}")
        return result

    async def try_dispatch_command(
        self, *, command: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """把命令转交给**已启动**的单元；未启动或单元穿透时返回 ``None``。

        与 :meth:`handle_command` 的差异：不懒启动单元（Observer 回程命令
        不应激活任何作用域），且把单元的 ``None`` 回执（「不是我的」穿透
        约定，如 ``hitl_response`` 的 promise 未被本单元持有）原样透传，
        由调用方继续扇出或兜底。

        Args:
            command: 命令名
            payload: 命令载荷（作用域由单元自身补齐，调用方不传）

        Returns:
            单元的结果字典；单元未启动或返回 ``None`` 时为 ``None``
        """
        handle = self._handle
        if handle is None:
            return None
        result = await handle.unit.handle_command(command, payload, handle.context)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected result for {command}")
        return result

    def tools_for(self, agent_id: str) -> tuple[str, ...] | None:
        """同步读取某 Agent 当前装载的工具名（离线返回 ``None``）。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            工具名元组；不在线时为 ``None``
        """
        handle = self._handle
        if handle is None:
            return None
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return None
        data = method(handle.scope_id, agent_id)
        if not isinstance(data, dict) or data.get("availability") != "online":
            return None
        tools = data.get("loaded_abilities")
        if not isinstance(tools, (list, tuple)):
            return None
        return tuple(str(item) for item in tools)

    def run_id_for(self, agent_id: str) -> str | None:
        """同步解析某 Agent 当前的生命周期 run（离线返回 ``None``）。

        能力关系库的 ``runs`` 回调是同步的，因此这里必须同步返回。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            run 标识；不在线时为 ``None``
        """
        handle = self._handle
        if handle is None:
            return None
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return None
        data = method(handle.scope_id, agent_id)
        if not isinstance(data, dict) or data.get("availability") != "online":
            return None
        value = data.get("incarnation_id")
        return str(value) if value else None

    async def get_entity_runtime(self, *, agent_id: str) -> dict[str, Any]:
        """读取本作用域内 Agent 的运行时快照。

        单元把该方法暴露为普通方法（不在命令表里），因此这里直连调用。

        Args:
            agent_id: 稳定 Agent ID

        Returns:
            单元返回的运行时快照字典
        """
        handle = self._handle
        if handle is None:
            return {"availability": "offline", "incarnation_id": None}
        method = getattr(handle.unit, "get_entity_runtime", None)
        if method is None:
            return {"availability": "offline", "incarnation_id": None}
        result = method(handle.scope_id, agent_id)
        return result if isinstance(result, dict) else {"availability": "offline"}

    async def stop(self) -> bool:
        """停止并释放本作用域单元。

        Returns:
            是否确实停止了一个活动实例
        """
        async with self._lock:
            handle = self._handle
            if handle is None:
                return False
            try:
                await handle.unit.stop()
            finally:
                self._handle = None
                await self._close_resources()
            return True

    async def _close_resources(self) -> None:
        manager = self._manager
        self._manager = None
        if manager is not None:
            await manager.close()

    async def aclose(self) -> None:
        """停止本作用域单元并释放资源（幂等）。"""
        await self.stop()
