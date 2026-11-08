# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Core 向 Subject 宿主命令面派发请求的适配器。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = [
    "SubjectCommandBridge",
]

logger = logging.getLogger(__name__)

SerialFn = Callable[[str, dict[str, Any]], Awaitable[Any]]


class SubjectCommandBridge:
    """基于宿主 ``ctx.serial`` 的命令派发桥（协议命令面客户端）。

    Args:
        serial: 宿主 serial 回调（``ctx.serial(name, payload)``），
            duck-typed，Core 不 import Ouroboros。
    """

    def __init__(self, serial: SerialFn) -> None:
        self._serial = serial

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """派发 ``command/<command>`` 并归一化回执。

        Args:
            command: 目标命令名（不含 ``command/`` 前缀）
            payload: 命令载荷

        Returns:
            ``{"success": bool, "data"?, "error"?}``——serial 不可用、
            派发异常或回执非 dict 时一律回 ``{"success": False, "error"}``
        """
        try:
            result = await self._serial(f"command/{command}", payload)
        except Exception as e:
            logger.warning("SubjectCommandBridge: dispatch of '%s' failed: %s", command, e)
            return {"success": False, "error": f"command '{command}' failed: {e}"}
        if not isinstance(result, dict):
            return {"success": False, "error": f"Unexpected result for command '{command}'"}
        return result
