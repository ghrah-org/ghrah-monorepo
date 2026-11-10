# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""内核私有有界流：Linux 用非阻塞 pipe，其余平台用有界缓冲；不向客户端交 FD。"""

import asyncio
import math
import os
import sys
from dataclasses import dataclass, field, replace

from ..protocol import ErrorRecord
from .tables import KernelFault, ObjectRecord

if sys.platform == "linux":
    import fcntl


@dataclass
class Stream:
    object_id: str
    domain: str
    reader: str
    writer: str
    read_fd: int
    write_fd: int
    capacity: int
    closed: set[str] = field(default_factory=set)
    terminal: str | None = None
    reading: bool = False
    writing: bool = False
    watchers: dict = field(default_factory=dict)
    expiry: asyncio.TimerHandle | None = None
    buffer: bytearray | None = None


class Streams:
    def __init__(self, kernel):
        self.kernel = kernel
        self.records: dict[str, Stream] = {}

    async def create(self, session, meta):
        source, base, scheme = self.kernel._target(session, meta["handle"])
        if source.kind != "resource" or "stream" not in source.actions or scheme.domain_id is None:
            raise KernelFault("ACCESS_DENIED", "此引用不能建立流")
        direction, quota = meta.get("direction"), meta.get("quota", 4096)
        if (
            direction not in ("upload", "download")
            or type(quota) is not int
            or not 4096 <= quota <= 65536
        ):
            raise KernelFault(
                "INVALID_ARGUMENT", "通道需要单向 upload/download 与 4096..65536 容量"
            )
        if len(self.records) >= 32:
            raise KernelFault("QUOTA_EXCEEDED", "通道表已满")
        domain = self.kernel.processes.get(scheme.domain_id)
        opened = None
        fds = []
        try:
            reply = await self.kernel._rpc(
                domain,
                {
                    "op": "channel",
                    "scheme_id": scheme.scheme_id,
                    "provider_object_id": base.provider_object_id,
                    "direction": direction,
                    "requested_quota": quota,
                },
                prepare=lambda _: self.kernel.source_live(session, source),
            )
            result = self.kernel._require_ok(reply)
            opened = result["provider_object_id"]
            peer = self.kernel.sessions.get(result["peer_target_session_id"])
            self.kernel.source_live(session, source)
            if peer is None or not peer.active or not peer.accept_channels:
                raise KernelFault("TRANSFER_NOT_ACCEPTED", "对端未选择接收通道")
            if peer.domain_id != domain.domain_id:
                raise KernelFault("ACCESS_DENIED", "对端会话不属于提供者实例")
            if peer.events.full():
                raise KernelFault("EVENT_QUEUE_FULL", "无法预留通道安装通知")
            if (
                len(self.records) >= 32
                or len(self.kernel.references.nodes) + 2 > self.kernel.references.max_nodes
            ):
                raise KernelFault("QUOTA_EXCEEDED", "双端引用或通道额度不足")
            self.kernel.references.capacity(peer)
            self.kernel.references.capacity(session)
            if peer is session and len(session.handles) + 2 > self.kernel.references.max_handles:
                raise KernelFault("QUOTA_EXCEEDED", "双端引用额度不足")
            accepted = result["effective_quota"]
            if type(accepted) is not int or not 4096 <= accepted <= quota:
                raise KernelFault("PROTOCOL_ERROR", "提供者返回无效容量")
            if sys.platform == "linux":
                fds = list(os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC))
                try:
                    fcntl.fcntl(fds[1], fcntl.F_SETPIPE_SZ, accepted)
                except PermissionError:
                    pass
                capacity = fcntl.fcntl(fds[1], fcntl.F_GETPIPE_SZ)
            else:
                # Windows Proactor 没有 add_reader；用同样有界的内核私有缓冲。
                # 缓冲不跨进程共享，客户端仍只通过既有 read/write 消息访问。
                capacity = accepted
            if capacity > quota:
                raise KernelFault("NOT_SUPPORTED", "宿主无法提供请求范围内的 pipe 容量")
            obj = ObjectRecord(
                self.kernel.new_id("object"), scheme.scheme_id, opened, kind="channel"
            )
            # 从这一步至双端安装和事件提交之间没有 await。
            local = self.kernel.derived(
                session, obj, source, {"write" if direction == "upload" else "read"}
            )
            try:
                remote = self.kernel.derived(
                    peer, obj, source, {"read" if direction == "upload" else "write"}
                )
            except BaseException:
                session.handles.pop(local.handle_id)
                self.kernel.references.nodes.pop(local.handle_id)
                self.kernel.objects.pop(obj.object_id)
                raise
            reader, writer = (remote, local) if direction == "upload" else (local, remote)
            stream = Stream(
                obj.object_id,
                domain.domain_id,
                reader.handle_id,
                writer.handle_id,
                fds[0] if fds else -1,
                fds[1] if fds else -1,
                capacity,
                buffer=None if fds else bytearray(),
            )
            self.records[obj.object_id] = stream
            fds = []
            if math.isfinite(source.expiry):
                stream.expiry = asyncio.get_running_loop().call_at(
                    source.expiry, self.stop, stream, "CHANNEL_REVOKED"
                )
            receipt = {
                "channel_id": obj.object_id,
                "handle_id": remote.handle_id,
                "direction": direction,
                "effective_quota": capacity,
                "operation": "read" if direction == "upload" else "write",
            }
            peer.events.put_nowait({"type": "channel_installed", **receipt})
            self.kernel.changed.set()
            return {
                "channel_id": obj.object_id,
                "handle_id": local.handle_id,
                "effective_quota": capacity,
                "delivery_receipt": receipt,
            }
        except BaseException:
            for fd in fds:
                os.close(fd)
            if opened is not None:
                await self.kernel.close_provider(domain, scheme.scheme_id, opened)
            raise

    def wake(self, stream):
        loop = asyncio.get_running_loop()
        for fd, (writing, future) in list(stream.watchers.items()):
            if stream.buffer is None:
                (loop.remove_writer if writing else loop.remove_reader)(fd)
            if not future.done():
                future.set_result(None)
        stream.watchers.clear()

    def close_fd(self, stream, name):
        fd = getattr(stream, name)
        if fd >= 0:
            os.close(fd)
            setattr(stream, name, -1)

    def stop(self, stream, status):
        if stream.terminal is not None:
            return
        stream.terminal = status
        self.wake(stream)
        self.close_fd(stream, "read_fd")
        self.close_fd(stream, "write_fd")
        if stream.expiry:
            stream.expiry.cancel()
        for reference in (stream.reader, stream.writer):
            self.kernel.drop_reference(self.kernel.references.nodes[reference])

    def invalidate(self, node):
        stream = self.records.get(node.object_id)
        if node.kind == "channel" and stream:
            self.stop(stream, "CHANNEL_REVOKED")

    def close(self, node):
        stream = self.records.get(node.object_id)
        if stream is None or node.kind != "channel":
            return
        stream.closed.add(node.handle_id)
        self.wake(stream)
        if node.handle_id == stream.reader:
            self.close_fd(stream, "read_fd")
        else:
            self.close_fd(stream, "write_fd")
        if len(stream.closed) == 2:
            if stream.expiry:
                stream.expiry.cancel()
            self.records.pop(node.object_id, None)

    def domain_closed(self, domain):
        for stream in list(self.records.values()):
            if stream.domain == domain:
                self.stop(stream, "CHANNEL_CLOSED")

    def check(self, session, node, stream, operation):
        if stream.terminal:
            raise KernelFault(stream.terminal, "流已结束或撤权")
        if (
            node.handle_id not in session.handles
            or node.handle_id in stream.closed
            or not session.active
        ):
            raise KernelFault("CHANNEL_CLOSED", "本端已关闭")
        try:
            self.kernel.references.check(node)
        except KernelFault as exc:
            self.stop(stream, "CHANNEL_REVOKED")
            raise KernelFault("CHANNEL_REVOKED", "流许可失效") from exc
        if operation not in node.actions:
            raise KernelFault("ACCESS_DENIED", "流端点方向不匹配")
        if operation == "write" and stream.reader in stream.closed:
            raise KernelFault("CHANNEL_CLOSED", "读端已经关闭")

    async def ready(self, stream, fd, writing, deadline):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def notify():
            if not future.done():
                future.set_result(None)

        if stream.buffer is not None:
            fd = int(writing)
        stream.watchers[fd] = (writing, future)
        if stream.buffer is None:
            (loop.add_writer if writing else loop.add_reader)(fd, notify)
        try:
            await asyncio.wait_for(future, max(0, deadline - loop.time()))
        except TimeoutError:
            raise KernelFault("TIMED_OUT", "流零进度等待到期") from None
        finally:
            if stream.watchers.get(fd, (None, None))[1] is future:
                if stream.buffer is None:
                    (loop.remove_writer if writing else loop.remove_reader)(fd)
                stream.watchers.pop(fd, None)

    async def io(self, session, request):
        meta, operation = request.meta, request.meta["op"]
        node = session.handles.get(meta["handle"])
        if node is None or node.kind != "channel":
            raise KernelFault("INVALID_HANDLE", "读写只接受本会话流端点")
        stream = self.records[node.object_id]
        self.check(session, node, stream, operation)
        deadline = meta.get("deadline")
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + 2
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise KernelFault("INVALID_ARGUMENT", "流 deadline 必须为有限单调时钟值")
        if deadline > asyncio.get_running_loop().time() + 3:
            raise KernelFault("INVALID_ARGUMENT", "实验流等待预算不超过 3 秒")
        if operation == "read":
            size = meta["max_bytes"]
            if type(size) is not int or not 1 <= size <= 16384:
                raise KernelFault("INVALID_ARGUMENT", "read 长度范围为 1..16384")
        else:
            data = request.opaque_payload
            if not isinstance(data, bytes) or not 1 <= len(data) <= 16384:
                raise KernelFault("INVALID_ARGUMENT", "write 只接受 1..16384 bytes")
        attribute = "reading" if operation == "read" else "writing"
        if getattr(stream, attribute):
            raise KernelFault("BUSY", "同方向已有在途读写")
        setattr(stream, attribute, True)
        try:
            while True:
                self.check(session, node, stream, operation)
                if deadline <= asyncio.get_running_loop().time():
                    raise KernelFault("TIMED_OUT", "流请求到期且无进度")
                try:
                    if stream.buffer is not None:
                        if operation == "read":
                            if not stream.buffer and stream.writer not in stream.closed:
                                raise BlockingIOError
                            chunk = bytes(stream.buffer[:size])
                            del stream.buffer[:size]
                            self.wake(stream)
                            return {"eof": chunk == b"", "stream_handle": node.handle_id}, chunk
                        accepted = min(len(data), stream.capacity - len(stream.buffer))
                        if not accepted:
                            raise BlockingIOError
                        stream.buffer.extend(data[:accepted])
                        self.wake(stream)
                        return {"accepted_bytes": accepted}, None
                    if operation == "read":
                        chunk = os.read(stream.read_fd, size)
                        return {"eof": chunk == b"", "stream_handle": node.handle_id}, chunk
                    accepted = os.write(stream.write_fd, data)
                    return {"accepted_bytes": accepted}, None
                except BlockingIOError:
                    await self.ready(
                        stream,
                        stream.read_fd if operation == "read" else stream.write_fd,
                        operation == "write",
                        deadline,
                    )
                except BrokenPipeError:
                    raise KernelFault("CHANNEL_CLOSED", "读端已关闭") from None
        finally:
            setattr(stream, attribute, False)

    def delivery(self, session, reply):
        reference = reply.meta.get("stream_handle")
        if reply.error or reference is None:
            return reply
        node = self.kernel.references.nodes[reference]
        stream = self.records.get(node.object_id)
        try:
            if stream is None:
                raise KernelFault("CHANNEL_CLOSED", "流已关闭")
            self.check(session, node, stream, "read")
        except KernelFault as exc:
            return replace(
                reply, meta={}, opaque_payload=None, error=ErrorRecord(exc.code, str(exc))
            )
        return reply

    def aclose(self):
        for stream in self.records.values():
            self.stop(stream, "CHANNEL_CLOSED")
