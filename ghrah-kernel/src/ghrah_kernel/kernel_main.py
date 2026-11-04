"""独立 Kernel 宿主，控制连接关闭后回收本轮所有直属服务。"""

import json
from multiprocessing.connection import Connection
from pathlib import Path

from .kernel.runtime import Kernel


async def run_kernel(
    control_fd, control_token, observer_fd, observer_token, report_path, peer_fd, peer_token
):
    kernel = Kernel()
    kernel.attach_caller(Connection(control_fd), control_token, "bootstrap")
    kernel.attach_caller(Connection(observer_fd), observer_token, "observer")
    kernel.attach_caller(Connection(peer_fd), peer_token, "observer")
    try:
        await kernel.stop.wait()
    finally:
        await kernel.aclose()
        Path(report_path).write_text(json.dumps(kernel.report(), ensure_ascii=False, indent=2))
