# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""SubjectCommandBridge：Core → Subject 命令面的最小桥接（command_proxy 回路）。

Core 保持 standalone（零 subject 依赖）：本模块只定义基于 Ouroboros
``ctx.serial`` 命令面的 duck-typed 桥接实现，泛化自 ``SerialRoomBridge``
模式。``command_proxy`` 工具的 ``execute`` 经此把工具调用派发为
``command/<command>`` 协议命令，由宿主 unit 的 RouteSpec 处理器承接。

通道语义：
- ``dispatch(command, payload)`` → ``{"success": bool, "data"?, "error"?}``
  （对齐 CoreUnit ``_ok``/``_err`` 回执形状）；
- serial 不可用或派发异常 → 回执报错，不炸调用方（驱动循环把回执转
  ``ActionResult(Failure)``，对齐插件热卸载窗口 Q4 的裁定：Unknown
  command 转 FAILURE 即可接受，无需额外事务）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = [
    "COMMAND_BRIDGE_UNAVAILABLE_ERROR",
    "SubjectCommandBridge",
]

logger = logging.getLogger(__name__)

COMMAND_BRIDGE_UNAVAILABLE_ERROR = (
    "Command bridge is not available. The command_proxy ability requires a "
    "Subject host context (Ouroboros ctx.serial) wired via CoreUnit."
)

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
            payload: 命令载荷（command_proxy 场景为工具调用参数）

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
