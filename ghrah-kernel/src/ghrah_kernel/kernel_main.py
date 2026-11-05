# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""独立 Kernel 宿主，控制连接关闭后回收本轮所有直属服务。"""

import json
from dataclasses import asdict
from multiprocessing.connection import Connection
from pathlib import Path

from .integration.assembly import host_configuration
from .kernel.runtime import Kernel


async def run_kernel(
    control_fd,
    control_token,
    observer_fd,
    observer_token,
    report_path,
    peer_fd,
    peer_token,
    configuration,
):
    catalog, bindings = host_configuration(configuration)
    kernel = Kernel(launch_catalog=catalog)
    kernel.attach_caller(
        Connection(control_fd), control_token, "bootstrap", **asdict(bindings["control"])
    )
    kernel.attach_caller(
        Connection(observer_fd), observer_token, "observer", **asdict(bindings["observer"])
    )
    kernel.attach_caller(Connection(peer_fd), peer_token, "observer", **asdict(bindings["peer"]))
    try:
        await kernel.stop.wait()
    finally:
        await kernel.aclose()
        Path(report_path).write_text(json.dumps(kernel.report(), ensure_ascii=False, indent=2))
