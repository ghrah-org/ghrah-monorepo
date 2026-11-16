# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""项目 → Core 端口实例的注册表。

Subject 是唯一知道 Project 的一层：它把「项目」翻译成 Core 需要的两样东西——
**数据根**（ActionChain sqlite 路径）与**不透明作用域 token**——然后按需构造
并缓存一个端口实例。Core 侧不再有「按项目取绑定视图」的概念。

本注册表只做三件事：登记项目根定位符、按项目懒建/复用端口实例、关闭时逐个
``aclose``。它不解释项目语义，也不改 Core 的生命周期模型。

消费方只应通过 :class:`CorePorts` 取端口，不直接构造 ``build_core_port``。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ghrah.protocol.ports.core import CorePort

logger = logging.getLogger(__name__)

__all__ = ["CorePorts"]


class CorePorts:
    """按项目维护 Core 端口实例。

    Args:
        factory: ``project_id -> CorePort`` 的构造器（由装配单元提供，内部负责
            解析项目根与单元配置）
    """

    def __init__(self, factory: Callable[[str], CorePort]) -> None:
        self._factory = factory
        self._ports: dict[str, CorePort] = {}
        self._locators: dict[str, str] = {}

    # ── 项目根定位符 ──

    def remember_locator(self, project_id: str, locator: str) -> None:
        """登记项目根定位符（供端口构造解析数据根）。"""
        if project_id and locator:
            self._locators[project_id] = locator

    def locator_for(self, project_id: str) -> str | None:
        """按项目取已登记的根定位符。"""
        return self._locators.get(project_id)

    # ── 端口实例 ──

    def for_project(self, project_id: str) -> CorePort:
        """取（或懒建）项目端口实例。

        Args:
            project_id: 项目标识

        Returns:
            该项目的 Core 端口实例

        Raises:
            ValueError: 项目标识为空，或项目根定位符未登记
        """
        if not project_id:
            raise ValueError("project_id required")
        port = self._ports.get(project_id)
        if port is None:
            port = self._factory(project_id)
            self._ports[project_id] = port
        return port

    def has_project(self, project_id: str) -> bool:
        """该项目是否已构造端口实例。"""
        return project_id in self._ports

    def started_project_ids(self) -> list[str]:
        """列出已构造端口实例的项目（快照；用于回程命令扇出）。"""
        return list(self._ports)

    async def close_project(self, project_id: str) -> None:
        """关闭并移除项目端口实例（未构造时无操作）。"""
        port = self._ports.pop(project_id, None)
        if port is not None:
            await port.aclose()

    async def aclose(self) -> None:
        """关闭全部端口实例（单个失败不影响其余）。"""
        ports = list(self._ports.values())
        self._ports.clear()
        errors: list[Exception] = []
        for port in ports:
            try:
                await port.aclose()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("failed to close Core ports", errors)
