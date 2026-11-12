"""运行时生命周期的进程内诊断快照。"""

from dataclasses import dataclass
from typing import Literal

RuntimePhase = Literal["booting", "ready", "stopping", "stopped", "failed"]


@dataclass(frozen=True)
class RuntimeStatus:
    """不可变状态快照，失败原因保留到实例退出。"""

    phase: RuntimePhase
    boot_id: str
    since: str
    error: str | None = None

    @property
    def ready(self) -> bool:
        """仅完整初始化后的运行时允许业务请求。"""
        return self.phase == "ready"
