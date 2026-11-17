# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""系统阶段由 Init 独占写入；探针与工具链只读。"""

from enum import StrEnum


class SystemPhase(StrEnum):
    """宿主本轮生命周期，不用内核 ready 代替服务就绪。"""

    BOOTING = "Booting"
    READY = "Ready"
    STOPPING = "Stopping"
    STOPPED = "Stopped"
    FAILED = "Failed"


class SystemState:
    """通过私有写者凭证约束状态迁移；读者只能取得快照。"""

    _transitions = {
        SystemPhase.BOOTING: {SystemPhase.READY, SystemPhase.STOPPING, SystemPhase.FAILED},
        SystemPhase.READY: {SystemPhase.STOPPING, SystemPhase.FAILED},
        SystemPhase.STOPPING: {SystemPhase.STOPPED, SystemPhase.FAILED},
        SystemPhase.STOPPED: set(),
        SystemPhase.FAILED: set(),
    }

    def __init__(self, writer: object) -> None:
        self._writer = writer
        self._phase = SystemPhase.BOOTING

    @property
    def phase(self) -> SystemPhase:
        """只读当前阶段。"""
        return self._phase

    def transition(self, phase: SystemPhase, *, writer: object) -> None:
        """只允许唯一写者执行声明过的迁移。"""
        if writer is not self._writer:
            raise PermissionError("系统阶段只能由 Init 写入")
        if phase not in self._transitions[self._phase]:
            raise ValueError(f"非法系统阶段迁移：{self._phase} → {phase}")
        self._phase = phase

    def snapshot(self) -> dict:
        """返回可独立序列化的只读派生快照。"""
        return {"status": self._phase.value}
