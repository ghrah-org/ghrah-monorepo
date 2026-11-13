# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Core 自持的单元上下文。

CoreUnit 对宿主的要求极窄——实测只用到 ``emit`` / ``provide`` / ``request_id``
三个成员。因此 Core 不必依赖 Subject 的容器：一个最小上下文即可托管
单元的生命周期，这也是「Agent 生命周期归 Core」的前提。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["UnitContext"]


class UnitContext:
    """供单元使用的最小宿主上下文。

    Attributes:
        request_id: 当前请求标识（单元用于日志关联）
    """

    def __init__(
        self,
        request_id: str = "core-runtime",
        *,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        serial: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._publish = emit
        self.serial = serial
        self._request_id = request_id
        self._values: dict[str, Any] = {}
        self._events: list[tuple[str, dict[str, Any]]] = []

    @property
    def request_id(self) -> str:
        """当前请求标识。"""
        return self._request_id

    def emit(self, event: str, payload: dict[str, Any] | None = None, **extra: Any) -> None:
        """记录单元发出的事件（Core 内部先落内存，订阅派发由上层决定）。

        Args:
            event: 事件名
            payload: 事件载荷
            extra: 额外字段
        """
        merged: dict[str, Any] = dict(payload or {})
        merged.update(extra)
        if self._publish is None:
            self._events.append((event, merged))
        else:
            self._publish(event, merged)

    def provide(self, key: str, value: Any, **extra: Any) -> None:
        """登记单元对外提供的服务。

        Args:
            key: 服务键
            value: 服务实现
            extra: 额外元数据（忽略）
        """
        self._values[key] = value

    def get(self, key: str, default: Any = None, **extra: Any) -> Any:
        """读取服务；不存在时返回默认值。

        Args:
            key: 服务键
            default: 缺省值
            extra: 预留（忽略）

        Returns:
            已登记的服务或默认值
        """
        return self._values.get(key, default)

    def drain_events(self) -> list[tuple[str, dict[str, Any]]]:
        """取走并清空已记录事件。

        Returns:
            事件列表
        """
        events = self._events
        self._events = []
        return events
