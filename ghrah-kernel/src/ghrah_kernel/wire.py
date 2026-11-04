"""标准库完整消息 API 的薄适配，不实现分帧。"""

import errno
import os
import socket
from multiprocessing.connection import Connection

MAX_MESSAGE_BYTES = 64 * 1024


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
        """本实验只接受 Linux 本地双工 Pipe 的 socket 端点。"""
        if self.connection.closed:
            return
        duplicate = os.dup(self.connection.fileno())
        try:
            wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=duplicate)
        except OSError:
            os.close(duplicate)
            raise
        with wake:
            try:
                wake.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    raise

    def close(self) -> None:
        self.connection.close()
