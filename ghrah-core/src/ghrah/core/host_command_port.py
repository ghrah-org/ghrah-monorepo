# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""HostCommandPort：Core → 宿主命令面的通用窄端口。

安装包无法 import Subject 宿主。本端口经 :class:`SubjectCommandBridge`
派发**不透明**命令字符串，允许集由装配期 ``host_command_grants`` 注入的
scoped 视图约束。Core 不认识任何领域命令名或发行版包身份。

执行服务表只在命中 grants 的成员调用路径注入 scoped 端口
（见 ActorAgent._build_ability_call），避免共授其它 operation 绕过授权。
"""

from __future__ import annotations

from typing import Any

from ghrah.core.subject_bridge import SubjectCommandBridge

__all__ = [
    "HOST_COMMAND_PORT_UNAVAILABLE_ERROR",
    "HostCommandPort",
    "ScopedHostCommandPort",
]

HOST_COMMAND_PORT_UNAVAILABLE_ERROR = (
    "Host command port is not available. A Subject host context "
    "(Ouroboros ctx.serial) must be wired via CoreUnit."
)


class ScopedHostCommandPort:
    """带固定命令白名单的派发视图。"""

    def __init__(
        self,
        bridge: SubjectCommandBridge | None,
        allowed: frozenset[str],
    ) -> None:
        self._bridge = bridge
        self._allowed = frozenset(allowed)

    @property
    def allowed(self) -> frozenset[str]:
        """本视图允许派发的命令名。"""
        return self._allowed

    async def dispatch(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """派发命令；不在白名单或未接线时返回显式失败回执。"""
        if command not in self._allowed:
            return {
                "success": False,
                "error": f"command {command!r} is not allowed on this host command port",
            }
        if self._bridge is None:
            return {"success": False, "error": HOST_COMMAND_PORT_UNAVAILABLE_ERROR}
        return await self._bridge.dispatch(command, payload)


class HostCommandPort:
    """宿主命令端口：按允许集产出 scoped 视图。"""

    def __init__(self, bridge: SubjectCommandBridge | None) -> None:
        self._bridge = bridge

    def scoped(self, allowed: frozenset[str]) -> ScopedHostCommandPort:
        """返回仅允许 ``allowed`` 内命令的派发视图。"""
        return ScopedHostCommandPort(self._bridge, frozenset(allowed))
