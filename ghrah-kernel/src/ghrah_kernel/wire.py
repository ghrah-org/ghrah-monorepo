# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""标准库完整消息 API 的薄适配，不实现分帧。"""

import errno
import socket
from multiprocessing.connection import Connection

MAX_MESSAGE_BYTES = 64 * 1024


def message_pipe() -> tuple[Connection, Connection]:
    """创建可继承的完整消息连接；Windows 也使用 socket，而非命名管道句柄。"""
    left, right = socket.socketpair()
    left.setblocking(True)
    right.setblocking(True)
    return Connection(left.detach()), Connection(right.detach())


class MessageTooLarge(ValueError):  # noqa: N818
    """发送前即可拒绝的超限消息。"""


class MessageConnection:
    def __init__(self, connection: Connection, max_bytes: int = MAX_MESSAGE_BYTES):
        self.connection = connection
        self.max_bytes = max_bytes

    def send(self, data: bytes) -> None:
        if len(data) > self.max_bytes:
            raise MessageTooLarge(f"消息超过 {self.max_bytes} 字节上限")
        self.connection.send_bytes(data)

    def receive(self) -> bytes:
        return self.connection.recv_bytes(maxlength=self.max_bytes)

    def shutdown(self) -> None:
        """唤醒 socket 上阻塞的收发；描述符始终由 Connection 唯一持有。"""
        if self.connection.closed:
            return
        wake = socket.socket(fileno=self.connection.fileno())
        try:
            try:
                wake.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    raise
        finally:
            wake.detach()

    def close(self) -> None:
        self.connection.close()
