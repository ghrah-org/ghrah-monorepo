# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""Unix 非阻塞管道创建；无 fcntl 的平台由 Streams 改走内存缓冲。"""

import os
from contextlib import suppress

try:  #  Windows 没有 fcntl；模块仍需可导入，由调用方选择内存缓冲后端。
    import fcntl
except ImportError:  # pragma: no cover - 仅在 Windows 上成立
    fcntl = None


def create_pipe(quota: int) -> tuple[int, int, int]:
    """创建不可继承的非阻塞双端，返回 FD 与待消费字节额度。

    支持 pipe2 的平台原子设置标志；其余平台使用 Python 的不可继承
    pipe，再设置非阻塞模式。Linux 继续尝试调整真实管道容量，最终额度
    不超过协商值及已知物理容量；无容量查询接口时由 Streams 严格核算额度。

    无 ``fcntl`` 的平台（Windows）没有可用的非阻塞 FD 监听，应由调用方改用
    进程内缓冲，不要走到这里。
    """
    if fcntl is None:
        raise OSError("当前平台没有 fcntl，不能创建非阻塞内核管道")
    pipe2 = getattr(os, "pipe2", None)
    fds = pipe2(os.O_NONBLOCK | os.O_CLOEXEC) if pipe2 is not None else os.pipe()
    try:
        if pipe2 is None:
            for fd in fds:
                os.set_inheritable(fd, False)
                os.set_blocking(fd, False)
        set_size = getattr(fcntl, "F_SETPIPE_SZ", None)
        get_size = getattr(fcntl, "F_GETPIPE_SZ", None)
        if set_size is not None:
            try:
                fcntl.fcntl(fds[1], set_size, quota)
            except PermissionError:
                pass
        if get_size is not None:
            quota = min(quota, fcntl.fcntl(fds[1], get_size))
        return *fds, quota
    except BaseException:
        for fd in fds:
            with suppress(OSError):
                os.close(fd)
        raise
