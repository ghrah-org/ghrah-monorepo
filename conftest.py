# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""仓库级测试引导。

aiosqlite 的工作线程默认是非 daemon（`aiosqlite/core.py` 里
`from threading import Thread`）。只要有任何用例漏关连接、且连接被别的对象
持有（等不到 GC 回收），解释器退出时主线程就会永久停在
`threading._shutdown()` 的 join 上：pytest 打印完 summary，进程却不退出，
CI 上表现为步计时一直涨、后续步骤全部 pending。

这里把测试进程里的该线程替换成 daemon 版，保证跑完必退。放在仓库根，
使任意子集（单包 / 单目录 / 全量）都生效。

注意：这是兜底，不是修复。连接泄漏本身仍要按用例逐个补 `close()`；
未关闭连接还会留下 fd 与「Event loop is closed」类告警。
"""

from __future__ import annotations

import threading
from typing import Any

from aiosqlite import core as _aiosqlite_core


class _DaemonThread(threading.Thread):
    """daemon 版线程：不参与解释器退出的 join。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["daemon"] = True
        super().__init__(*args, **kwargs)


_aiosqlite_core.Thread = _DaemonThread
