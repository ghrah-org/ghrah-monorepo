# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

import asyncio
import threading
from multiprocessing import Pipe

import pytest
from _mechanism.scenarios import open_message_provider, run_message_roundtrip

from ghrah_kernel.client import RemoteError
from ghrah_kernel.endpoint import AsyncEndpoint
from ghrah_kernel.wire import MessageConnection, MessageTooLarge


def assert_no_endpoint_workers():
    assert not [thread.name for thread in threading.enumerate() if thread.name.startswith("lab-")]


def test_final_send_gate_runs_after_backpressure_and_failed_gate_returns_slot():
    async def scenario():
        a, b = Pipe(duplex=True)
        endpoint = AsyncEndpoint(MessageConnection(a))
        receiver = AsyncEndpoint(MessageConnection(b))
        try:
            # 用单个发送名额建立确定的背压，不依赖 socket 缓冲恰好被填满。
            endpoint._send_slots = asyncio.Semaphore(1)
            await endpoint._send_slots.acquire()
            allowed = True
            entered = asyncio.Event()

            def gate():
                entered.set()
                if not allowed:
                    raise PermissionError("提交前撤权")
                return b"secret"

            blocked = asyncio.create_task(endpoint.send(b"", prepare=gate))
            await asyncio.sleep(0)
            assert not entered.is_set()
            allowed = False
            endpoint._send_slots.release()
            with pytest.raises(PermissionError):
                await blocked
            await endpoint.send(b"healthy")
            assert await receiver.receive() == b"healthy"
        finally:
            await endpoint.aclose()
            await receiver.aclose()

    asyncio.run(scenario())
    assert_no_endpoint_workers()


def test_real_child_process_roundtrip_and_normal_exit():
    report = asyncio.run(run_message_roundtrip())
    assert report["passed"] is True
    assert report["provider"]["pid"] != report["parent_pid"]
    assert report["child_exit_code"] == 0
    assert_no_endpoint_workers()


def test_correlated_calls_remote_errors_and_recovery_after_oversize():
    async def scenario():
        async with open_message_provider() as (client, process):
            values = await asyncio.gather(
                *(client.call("echo", {"index": index}) for index in range(8))
            )
            assert values == [{"index": index} for index in range(8)]
            with pytest.raises(RemoteError) as unknown:
                await client.call("missing", {})
            assert unknown.value.code == "UNKNOWN_ACTION"
            with pytest.raises(RemoteError) as invalid:
                await client.call("add", {"a": "bad", "b": 2})
            assert invalid.value.code == "INVALID_ARGUMENT"
            with pytest.raises(MessageTooLarge):
                await client.call("echo", {"text": "x" * 70_000})
            assert await client.call("add", {"a": 2, "b": 3}) == {"sum": 5}
        assert process.returncode == 0

    asyncio.run(scenario())
    assert_no_endpoint_workers()


def test_timed_out_wait_does_not_start_second_receiver_or_misroute_late_reply():
    async def scenario():
        async with open_message_provider() as (client, process):
            await client.call("identity", {})
            with pytest.raises(TimeoutError):
                await client.call("delay", {"milliseconds": 100, "value": "late"}, timeout=0.02)
            assert await client.call("echo", {"after": "timeout"}) == {"after": "timeout"}
        assert process.returncode == 0

    asyncio.run(scenario())
    assert_no_endpoint_workers()


def test_cancelled_call_keeps_shared_receiver_alive():
    async def scenario():
        async with open_message_provider() as (client, process):
            await client.call("identity", {})
            pending = asyncio.create_task(
                client.call("delay", {"milliseconds": 100, "value": "late"})
            )
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert await client.call("echo", {"after": "cancel"}) == {"after": "cancel"}
        assert process.returncode == 0

    asyncio.run(scenario())
    assert_no_endpoint_workers()


def test_endpoint_close_wakes_idle_receive_and_joins_workers():
    async def scenario():
        a, b = Pipe(duplex=True)
        endpoint = AsyncEndpoint(MessageConnection(a))
        receiving = asyncio.create_task(endpoint.receive())
        try:
            await asyncio.sleep(0)
            await asyncio.wait_for(endpoint.aclose(), timeout=1)
            with pytest.raises((EOFError, OSError, ConnectionError)):
                await receiving
            await endpoint.aclose()
        finally:
            b.close()

    asyncio.run(scenario())
    assert_no_endpoint_workers()


def test_peer_crash_releases_waiter_and_io_workers():
    async def scenario():
        async with open_message_provider() as (client, process):
            # 先完成一次往返，确保子进程已启动并完成 SDK 初始化。
            await client.call("identity", {})
            process.kill()
            await process.wait()
            with pytest.raises((ConnectionError, OSError)):
                await client.call("echo", {"after": "crash"})

    asyncio.run(scenario())
    assert_no_endpoint_workers()
