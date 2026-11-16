# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""跨 Unix 的真实 pipe、严格字节额度与等待回收回归。"""

import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ghrah_kernel.kernel import pipe
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.streams import Stream
from ghrah_kernel.kernel.tables import KernelFault, ObjectRecord
from ghrah_kernel.protocol import Envelope


def portable(monkeypatch, *, set_blocking=os.set_blocking):
    fds = []

    def create():
        pair = os.pipe()
        fds.extend(pair)
        return pair

    monkeypatch.setattr(
        pipe,
        "os",
        SimpleNamespace(
            pipe=create,
            set_inheritable=os.set_inheritable,
            set_blocking=set_blocking,
            close=os.close,
        ),
    )
    monkeypatch.setattr(pipe, "fcntl", SimpleNamespace())
    return fds


def test_portable_pipe_nonblocking_noninheritable_and_eof(monkeypatch):
    portable(monkeypatch)
    reader, writer, quota = pipe.create_pipe(4096)
    try:
        assert quota == 4096
        assert not os.get_blocking(reader) and not os.get_blocking(writer)
        assert not os.get_inheritable(reader) and not os.get_inheritable(writer)
        with pytest.raises(BlockingIOError):
            os.read(reader, 1)
        assert os.write(writer, b"bytes") == 5
        assert os.read(reader, 5) == b"bytes"
        os.close(writer)
        writer = -1
        assert os.read(reader, 1) == b""
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)


def test_portable_setup_failure_closes_both_descriptors(monkeypatch):
    calls = []

    def fail_second(fd, blocking):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("nonblocking setup failed")
        os.set_blocking(fd, blocking)

    descriptors = portable(monkeypatch, set_blocking=fail_second)
    with pytest.raises(OSError, match="setup failed"):
        pipe.create_pipe(4096)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_native_pipe2_receives_atomic_flags(monkeypatch):
    calls = []

    def pipe2(flags):
        calls.append(flags)
        fds = os.pipe()
        for fd in fds:
            os.set_inheritable(fd, False)
            os.set_blocking(fd, False)
        return fds

    monkeypatch.setattr(
        pipe,
        "os",
        SimpleNamespace(
            pipe2=pipe2,
            O_NONBLOCK=os.O_NONBLOCK,
            O_CLOEXEC=os.O_CLOEXEC,
            close=os.close,
        ),
    )
    monkeypatch.setattr(pipe, "fcntl", SimpleNamespace())
    reader, writer, _ = pipe.create_pipe(4096)
    try:
        assert calls == [os.O_NONBLOCK | os.O_CLOEXEC]
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize(
    "quota,physical,denied,expected",
    [
        (5001, 8192, False, 5001),
        (65536, 8192, True, 8192),
        (4096, 65536, True, 4096),
    ],
)
def test_native_capacity_is_bounded_by_negotiated_and_host_limits(
    monkeypatch, quota, physical, denied, expected
):
    portable(monkeypatch)
    calls = []

    def fcntl(fd, command, *arguments):
        calls.append((command, arguments))
        if command == 1:
            if denied:
                raise PermissionError("resize unavailable")
            return physical
        assert command == 2
        return physical

    monkeypatch.setattr(pipe, "fcntl", SimpleNamespace(F_SETPIPE_SZ=1, F_GETPIPE_SZ=2, fcntl=fcntl))
    reader, writer, effective = pipe.create_pipe(quota)
    try:
        assert effective == expected
        assert calls == [(1, (quota,)), (2, ())]
    finally:
        os.close(reader)
        os.close(writer)


def test_capacity_query_failure_closes_both_descriptors(monkeypatch):
    descriptors = portable(monkeypatch)

    def fail(*args):
        raise OSError("capacity query failed")

    monkeypatch.setattr(pipe, "fcntl", SimpleNamespace(F_GETPIPE_SZ=2, fcntl=fail))
    with pytest.raises(OSError, match="capacity query failed"):
        pipe.create_pipe(4096)
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@contextmanager
def local_stream(quota=4096):
    kernel = Kernel()
    session = kernel._new_session("observer", None, "observer", False)
    source = kernel.references.nodes[session.hello["sys_handle"]]
    obj = ObjectRecord(kernel.new_id("object"), kernel.sys_id, "local", root=True, kind="channel")
    reader = kernel.derived(session, obj, source, {"read"})
    writer = kernel.derived(session, obj, source, {"write"})
    read_fd, write_fd, quota = pipe.create_pipe(quota)
    stream = Stream(
        obj.object_id, "local", reader.handle_id, writer.handle_id, read_fd, write_fd, quota
    )
    kernel.streams.records[obj.object_id] = stream

    async def read(size=4096):
        return await kernel.streams.io(
            session,
            Envelope(
                "request",
                1,
                {
                    "op": "read",
                    "handle": reader.handle_id,
                    "max_bytes": size,
                },
            ),
        )

    async def write(data, *, deadline=None):
        meta = {"op": "write", "handle": writer.handle_id}
        if deadline is not None:
            meta["deadline"] = deadline
        return await kernel.streams.io(session, Envelope("request", 1, meta, data))

    try:
        yield kernel, stream, read, write
    finally:
        kernel.streams.aclose()


async def test_credit_wait_uses_reader_notification_not_writable_fd(monkeypatch):
    portable(monkeypatch)
    with local_stream() as (kernel, stream, read, write):
        assert (await write(b"x" * 12000))[0]["accepted_bytes"] == 4096
        assert stream.buffered == 4096
        loop = asyncio.get_running_loop()

        def unexpected_writer(*args):
            raise AssertionError("Logical credit must not subscribe to an already writable pipe")

        monkeypatch.setattr(loop, "add_writer", unexpected_writer)
        task = asyncio.create_task(write(b"tail"))
        await asyncio.sleep(0)
        assert not task.done() and len(stream.watchers) == 1
        assert kernel.stats()["io_waiters"] == 1
        assert (await read())[1] == b"x" * 4096
        assert (await asyncio.wait_for(task, 1))[0]["accepted_bytes"] == 4
        assert stream.buffered == 4 and not stream.watchers
        assert (await read())[1] == b"tail" and stream.buffered == 0


async def test_accounting_follows_actual_partial_write_and_read(monkeypatch):
    portable(monkeypatch)
    with local_stream() as (_, stream, read, write):
        original = os.write
        monkeypatch.setattr(os, "write", lambda fd, data: original(fd, data[:19]))
        assert (await write(b"x" * 512))[0]["accepted_bytes"] == 19
        assert stream.buffered == 19
        assert (await read(7))[1] == b"x" * 7 and stream.buffered == 12
        assert (await read())[1] == b"x" * 12 and stream.buffered == 0


async def test_credit_timeout_cancellation_and_retry_clean_waiters(monkeypatch):
    portable(monkeypatch)
    with local_stream() as (_, stream, read, write):
        await write(b"x" * 4096)
        with pytest.raises(KernelFault) as error:
            await write(b"y", deadline=asyncio.get_running_loop().time() + 0.01)
        assert error.value.code == "TIMED_OUT"
        assert not stream.watchers and not stream.writing and stream.buffered == 4096
        pending = asyncio.create_task(write(b"z"))
        await asyncio.sleep(0)
        assert stream.watchers
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not stream.watchers and not stream.writing and stream.buffered == 4096
        await read()
        assert (await write(b"z"))[0]["accepted_bytes"] == 1


async def test_partial_read_releases_only_consumed_credit(monkeypatch):
    portable(monkeypatch)
    # 小额度只用于直接 I/O 单元验收，确保物理 pipe 尚可写；wire 仍要求至少 4096。
    with local_stream(quota=512) as (_, stream, read, write):
        assert (await write(b"x" * 512))[0]["accepted_bytes"] == 512
        pending = asyncio.create_task(write(b"y" * 32))
        await asyncio.sleep(0)
        assert not pending.done()
        assert (await read(7))[1] == b"x" * 7
        assert (await asyncio.wait_for(pending, 1))[0]["accepted_bytes"] == 7
        assert stream.buffered == 512
        assert (await read())[1] == b"x" * 505 + b"y" * 7
        assert stream.buffered == 0
