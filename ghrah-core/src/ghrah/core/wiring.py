# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 端口的装配工厂。

只有装配根调用这里——模块自己不构造端口，也不决定作用域。

两种形态：

- :func:`build_core_port`：**一个作用域一个实例**。数据根（sqlite 路径）与
  作用域 token 在构造时固定，能力请求里不再出现作用域。
- :func:`build_core_catalog`：**全局目录端口**。无作用域、不打开数据库，
  只读能力包安装根与受信配置（零项目时也可用）。

``config_factory`` 是部署设置注入点：装配层（Subject 或进程入口）在此提供
HITL 策略、工作区根、能力安装目录等，Core 自己不读环境变量、不猜配置。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ghrah.core.api.catalog import CoreCatalog
from ghrah.core.api.facade import CoreFacade
from ghrah.core.api.handlers import build_catalog_handlers, build_handlers
from ghrah.core.api.runtime import CoreCatalogRuntime, CoreRuntime
from ghrah.core.read.ability import AbilityReadStore
from ghrah.core.read.chain import ChainReadStore
from ghrah.core.read.mailbox import MailboxReadStore
from ghrah.core.read.turn import TurnReadStore
from ghrah.core.unit import CoreUnitConfig
from ghrah.core.units.registry import (
    ConfigFactory,
    ConfigHook,
    ManagerFactory,
    ManifestResolver,
    RuntimeRegistry,
    UnitFactory,
)

__all__ = ["build_core_catalog", "build_core_port"]


async def _close_all(*closers: Callable[[], Awaitable[None]]) -> None:
    """依次关闭各读表层与运行时（一个失败不影响其余）。"""
    errors: list[Exception] = []
    for closer in closers:
        try:
            await closer()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("failed to close Core port", errors)


def _default_manager_factory(db_path: str, scope_id: str) -> ManagerFactory:
    """构造消息管理器工厂（Agent 动态注册，无需预先知道名单）。"""

    async def factory() -> Any:
        from ghrah.communication import MessageManager

        path = Path(db_path)
        manager = MessageManager(
            path,
            project_id=scope_id,
            agent_ids=(),
            session_routing=True,
            allow_persisted_agents=True,
        )
        await manager.open(prepare_context=True)
        return manager

    return factory


def _default_config_factory(db_path: str, scope_id: str) -> ConfigFactory:
    """构造默认的单元配置工厂：只绑定作用域与链库，其余用单元默认值。"""

    def factory() -> CoreUnitConfig:
        from ghrah.context.persistence.sqlite_backend import SqliteBackend

        path = Path(db_path)

        def persistence_factory(_agent_config: object) -> SqliteBackend:
            return SqliteBackend(db_path=path)

        return CoreUnitConfig(
            project_id=scope_id,
            persistence_factory=persistence_factory,
        )

    return factory


def build_core_port(
    *,
    scope_id: str,
    db_path: str,
    config_factory: ConfigFactory | None = None,
    unit_factory: UnitFactory | None = None,
    manager_factory: ManagerFactory | None = None,
    manifest_resolver: ManifestResolver | None = None,
    install_root: str | None = None,
    agentconf_db_path: str | None = None,
    grant_policy: Any = None,
    config_hook: ConfigHook | None = None,
    emit: Callable[[str, dict[str, Any]], None] | None = None,
    serial: Callable[..., Awaitable[Any]] | None = None,
) -> CoreFacade:
    """构造一个**作用域**端口实例。

    Args:
        scope_id: 不透明作用域 token（装配层通常传 Project 标识）
        db_path: 本作用域 ActionChain sqlite 路径（三个读面共用）
        config_factory: 单元配置构造器（无参；作用域由闭包绑定）；缺省只绑定作用域与链库
        unit_factory: 单元构造器（测试可替换）
        manager_factory: 消息管理器工厂；缺省按 ``db_path`` 打开一个
        manifest_resolver: manifest 解析源；缺省不提供（能力返回 FAILED_PRECONDITION）
        install_root: 能力包安装根；缺省时包目录读面报告「不可用」
        agentconf_db_path: MCP 探测使用的受信配置库路径
        grant_policy: 部署显式配置的启动授权策略
        config_hook: 单元创建前的异步配置钩子（注入 ability_store 等）
        emit: 宿主事件投影回调
        serial: 宿主命令入口，由 Core 的受限桥转发

    Returns:
        作用域端口实例

    Raises:
        RuntimeError: 能力清单与处理器集合不一致
    """
    chain = ChainReadStore(db_path=Path(db_path))
    mailbox = MailboxReadStore(db_path=Path(db_path), scope_id=scope_id)
    registry = RuntimeRegistry(
        scope_id=scope_id,
        config_factory=config_factory or _default_config_factory(db_path, scope_id),
        **({"unit_factory": unit_factory} if unit_factory is not None else {}),
        manager_factory=manager_factory or _default_manager_factory(db_path, scope_id),
        manifest_resolver=manifest_resolver,
        config_hook=config_hook,
        emit=emit,
        serial=serial,
    )
    # 能力关系库的运行时态来自活动单元：离线时 run 为 None，视图据此拒绝。
    abilities = AbilityReadStore(
        db_path=Path(db_path),
        scope_id=scope_id,
        resolve_run=registry.run_id_for,
        resolve_tools=registry.tools_for,
        install_root=install_root,
        agentconf_db_path=agentconf_db_path,
        grant_policy=grant_policy,
        reclaim_stale=True,
    )
    registry.set_ability_store_factory(abilities.store)
    turns = TurnReadStore(db_path=Path(db_path))
    runtime = CoreRuntime(build_handlers(chain, mailbox, abilities, registry, turns))
    return CoreFacade(
        runtime=runtime,
        scope_id=scope_id,
        closer=lambda: _close_all(registry.aclose, chain.aclose, abilities.aclose),
        registry=registry,
    )


def build_core_catalog(
    *,
    install_root: str | None = None,
    agentconf_db_path: str | None = None,
    grant_policy: Any = None,
) -> CoreCatalog:
    """构造全局目录端口（无作用域）。

    Args:
        install_root: 能力包安装根
        agentconf_db_path: MCP 探测使用的受信配置库路径
        grant_policy: 部署显式配置的启动授权策略

    Returns:
        目录端口；零项目时也可用，不打开数据库、不建单元
    """
    abilities = AbilityReadStore(
        db_path=Path(""),
        scope_id=None,
        install_root=install_root,
        agentconf_db_path=agentconf_db_path,
        grant_policy=grant_policy,
    )
    runtime = CoreCatalogRuntime(build_catalog_handlers(abilities))
    return CoreCatalog(runtime=runtime, closer=abilities.aclose)
