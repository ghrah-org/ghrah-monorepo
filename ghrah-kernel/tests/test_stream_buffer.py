# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Windows 有界流后端与既有协议的背压、EOF、取消和撤权语义。"""

import asyncio
from types import SimpleNamespace

import pytest

from ghrah_kernel.kernel import streams as streams_module
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.streams import Stream
from ghrah_kernel.kernel.tables import KernelFault, ObjectRecord, Scheme
from ghrah_kernel.protocol import Envelope


@pytest.fixture
async def stream_pair():
    kernel = Kernel()
    session = kernel._new_session("observer", None, "observer", False)
    source = kernel.references.nodes[session.hello["sys_handle"]]
    obj = ObjectRecord(kernel.new_id("object"), kernel.sys_id, "test", root=True, kind="channel")
    reader = kernel.derived(session, obj, source, {"read"})
    writer = kernel.derived(session, obj, source, {"write"})
    stream = Stream(
        obj.object_id, "test", reader.handle_id, writer.handle_id, -1, -1, 4096, buffer=bytearray()
    )
    kernel.streams.records[obj.object_id] = stream
    try:
        yield kernel, session, source, reader, writer, stream
    finally:
        await kernel.aclose()


async def test_bounded_buffer_backpressure_partial_write_and_eof(stream_pair):
    kernel, session, _, reader, writer, stream = stream_pair
    streams = kernel.streams

    async def write(data):
        return await streams.io(
            session, Envelope("request", 1, {"op": "write", "handle": writer.handle_id}, data)
        )

    async def read(size):
        return await streams.io(
            session,
            Envelope("request", 2, {"op": "read", "handle": reader.handle_id, "max_bytes": size}),
        )

    assert (await write(b"x" * 8192))[0]["accepted_bytes"] == 4096
    blocked = asyncio.create_task(write(b"y"))
    await asyncio.sleep(0)
    assert not blocked.done() and len(stream.buffer) == stream.capacity
    assert (await read(4096))[1] == b"x" * 4096
    assert (await blocked)[0]["accepted_bytes"] == 1
    streams.close(writer)
    assert (await read(8))[1] == b"y"
    assert (await read(8))[0]["eof"] is True


async def test_waiting_reader_deadline_cancellation_and_revocation(stream_pair):
    kernel, session, source, reader, _, stream = stream_pair
    request = Envelope(
        "request",
        1,
        {
            "op": "read",
            "handle": reader.handle_id,
            "max_bytes": 16,
            "deadline": asyncio.get_running_loop().time() + 0.02,
        },
    )
    with pytest.raises(KernelFault) as error:
        await kernel.streams.io(session, request)
    assert error.value.code == "TIMED_OUT"
    request.meta.pop("deadline")
    task = asyncio.create_task(kernel.streams.io(session, request))
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not stream.watchers and not stream.reading
    task = asyncio.create_task(kernel.streams.io(session, request))
    await asyncio.sleep(0)
    await kernel.references.revoke(session, {"type": "handle", "handle": source.handle_id})
    with pytest.raises(KernelFault) as error:
        await task
    assert error.value.code == "CHANNEL_REVOKED"
    assert not stream.watchers


async def test_buffer_backend_channel_creation_skips_pipe(monkeypatch):
    """缓冲后端的 create 路径：不建 FD，容量即协商额度，读写仍走同一套协议。"""
    monkeypatch.setattr(streams_module, "_BUFFER_BACKEND", True)
    kernel = Kernel()
    owner = kernel._new_session("observer", None, "observer", False)
    owner.domain_id = "provider"
    owner.accept_channels = True
    sid = kernel.new_id("scheme")
    kernel.schemes[sid] = Scheme(sid, "provider", "ability", "test", frozenset({"stream"}))
    kernel.processes.domains["provider"] = SimpleNamespace(
        state="ready", reaped=False, domain_id="provider"
    )
    handle = kernel._root_handle(owner, sid, {"stream"})

    async def rpc(domain, meta, payload=None, *, prepare=None):
        if prepare:
            prepare(None)
        if meta["op"] == "close":
            return Envelope("response", 1, {"closed": True})
        return Envelope(
            "response",
            1,
            {
                "provider_object_id": "accepted",
                "peer_target_session_id": owner.session_id,
                "effective_quota": 4096,
            },
        )

    kernel._rpc = rpc

    async def write(data):
        return await kernel.streams.io(
            owner, Envelope("request", 1, {"op": "write", "handle": receipt["handle_id"]}, data)
        )

    async def read(size):
        return await kernel.streams.io(
            owner,
            Envelope(
                "request",
                2,
                {
                    "op": "read",
                    "handle": receipt["delivery_receipt"]["handle_id"],
                    "max_bytes": size,
                },
            ),
        )

    try:
        receipt = await kernel.streams.create(owner, {"handle": handle, "direction": "upload"})
        stream = kernel.streams.records[receipt["channel_id"]]
        assert stream.buffer == bytearray()
        assert (stream.read_fd, stream.write_fd) == (-1, -1)
        assert stream.capacity == receipt["effective_quota"] == 4096
        assert kernel.stats()["pipe_fds"] == 0
        assert (await write(b"x" * 4096))[0]["accepted_bytes"] == 4096
        blocked = asyncio.create_task(write(b"y"))
        await asyncio.sleep(0)
        assert not blocked.done()
        assert (await read(4096))[1] == b"x" * 4096
        assert (await blocked)[0]["accepted_bytes"] == 1
        kernel.streams.close(owner.handles[receipt["handle_id"]])
        assert (await read(8))[1] == b"y"
        assert (await read(8))[0]["eof"] is True
    finally:
        for reference in list(owner.handles):
            await kernel._close(owner, reference)
        kernel.processes.domains.clear()
        await kernel.aclose()
