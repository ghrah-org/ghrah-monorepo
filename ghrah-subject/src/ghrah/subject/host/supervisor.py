# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""监督策略的宿主适配；进程操作均委托内核。"""

from ghrah_kernel.kernel_client import KernelClient
from ghrah_kernel.supervisor import ServiceSpec
from ghrah_kernel.supervisor import Supervisor as KernelSupervisor


class Supervisor:
    """组合既有监督者，启动必须全成，停止沿用逆序 close、drain、kill。"""

    def __init__(self, control: KernelClient, specs: list[ServiceSpec], schemes: dict) -> None:
        self._supervisor = KernelSupervisor(control, specs, schemes)

    @property
    def failure(self) -> str | None:
        """读取监督循环失败事实。"""
        return self._supervisor.failure

    async def start(self, *, monitor: bool = True) -> None:
        """全部必需服务 Running 后才算成功；失败由 Init 统一回收。"""
        await self._supervisor.start(monitor=monitor)
        failed = [item for item in self.view() if item["phase"] != "Running"]
        if failed:
            raise RuntimeError(f"必需服务未就绪：{failed}")

    def view(self) -> list[dict]:
        """派生服务视图，不建立第二份可写的服务状态。"""
        return [self._supervisor.view(key) for key in self._supervisor.states]

    async def shutdown(self, *, mode: str = "drain") -> dict:
        """先 quiesce，再逆序关闭应用资源，最后由内核排空和回收。"""
        return await self._supervisor.shutdown(mode=mode)
