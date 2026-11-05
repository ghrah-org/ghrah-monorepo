# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import time
from types import SimpleNamespace

import pytest
from _mechanism.bootstrap import open_service_chain
from _mechanism.scenarios import run_event_stream_chain
from test_references import basis, denied, probe, release, until
from test_service_chain import assert_clean

from ghrah_kernel.kernel.events import EventRecord
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.streams import Stream
from ghrah_kernel.kernel.tables import KernelFault, ObjectRecord, Scheme
from ghrah_kernel.payloads import encode_payload
from ghrah_kernel.protocol import Envelope, decode_envelope


async def stream_command(host, channel, op, **arguments):
    root = host.services["tools"]["scheme_handles"]["ability"]
    return await host.control.call(
        root, "stream", {"channel_id": channel["channel_id"], "op": op, **arguments}
    )


async def io_waiting(host):
    return (await host.control.stats())["io_waiters"] > 0


async def event_waiting(host, baseline):
    return (await host.control.stats())["event_waiters"] > baseline


def test_agent_script_consumes_event_timer_and_both_real_stream_directions():
    result = asyncio.run(run_event_stream_chain())
    assert result["passed"]
    assert result["final"]["stats"]["pipe_fds"] == 0
    assert result["final"]["stats"]["event_records"] == 0


def test_inflight_setup_cannot_install_after_source_close():
    async def scenario(operation):
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open(f"/scheme/ability/main/slow-{operation}")
            establishing = asyncio.create_task(
                client.subscribe(handle) if operation == "subscribe" else client.channel(handle)
            )

            async def pending():
                return operation in (await probe(host))["pending_setups"]

            await until(pending)
            before = await client.stats()
            await client.close(handle)
            await release(host, operation)
            await denied(establishing, "INVALID_HANDLE")
            after = await client.stats()
            assert after["handles"] == before["handles"] - 1
            assert after["channels"] == after["event_records"] == 0
        assert_clean(host)

    asyncio.run(scenario("subscribe"))
    asyncio.run(scenario("channel"))


def test_provider_events_are_ordered_opaque_and_subscription_close_releases_record():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            before = await probe(host)
            subscription = await client.subscribe(handle)
            ready = await client.wait_item(subscription)
            assert ready["value"] == {"ready": True} and not ready["event"]["terminal"]
            value = {"op": "kill", "rights": ["业务内容"], "value": 42}
            await client.call(handle, "emit", value)
            item = await client.wait_item(subscription)
            assert item["value"] == value and item["event"]["seq"] == ready["event"]["seq"] + 1
            await denied(client.dup(subscription), "INVALID_ARGUMENT")
            await denied(host.peer.wait_item(subscription), "INVALID_HANDLE")
            await client.close(handle)
            assert not (await client.stats())["queued_events"]
            await client.close(subscription)
            after = await probe(host)
            assert after["opened"] == before["opened"] + 1
            assert after["closed"] == before["closed"] + 2
        assert_clean(host)

    asyncio.run(scenario())


def test_cursor_is_authorization_scoped_and_current_cursor_has_no_replay():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            subscription = await client.subscribe(handle)
            cursor = (await client.wait_item(subscription))["event"]["cursor"]
            await client.close(subscription)
            resumed = await client.subscribe(handle, cursor=cursor)
            current = await client.wait_item(resumed)
            assert current["event"]["seq"] == cursor["seq"] + 1
            reopened = await client.open("/scheme/ability/main")
            await denied(
                client.subscribe(reopened, cursor=current["event"]["cursor"]), "CURSOR_GAP"
            )
            await host.control.revoke(basis(host))
            await denied(client.subscribe(handle, cursor=cursor), "REVOKED")
            renewed = await client.open("/scheme/ability/main/renewed")
            await denied(client.subscribe(renewed, cursor=current["event"]["cursor"]), "CURSOR_GAP")
            fresh = await client.subscribe(renewed)
            assert (await client.wait_item(fresh))["event"]["cursor"]["epoch"] != cursor["epoch"]
            readonly = await client.open("/scheme/ability/main/readonly")
            await denied(
                client.subscribe(readonly, cursor={"epoch": "unknown", "seq": 1}), "ACCESS_DENIED"
            )
        assert_clean(host)

    asyncio.run(scenario())


def test_full_event_queue_reports_gap_and_revoke_discards_buffer_and_wakes_waiter():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            subscription = await client.subscribe(handle)
            for index in range(9):
                await client.call(handle, "emit", {"index": index})
            gap = await client.wait_item(subscription)
            assert gap["event"]["status"] == "CURSOR_GAP" and gap["value"] is None
            await client.close(subscription)
            active = await client.subscribe(handle)
            await client.wait_item(active)
            baseline = (await client.stats())["event_waiters"]
            pending = asyncio.create_task(client.wait_item(active))
            await until(lambda: event_waiting(host, baseline))
            await denied(client.wait_item(active), "BUSY")
            await client.revoke({"type": "handle", "handle": handle})
            ended = await pending
            assert ended["event"]["status"] == "REVOKED" and ended["value"] is None
            assert (await client.wait_item(active))["event"]["status"] == "REVOKED"
        assert_clean(host)

    asyncio.run(scenario())


def test_timers_and_multiwait_consume_only_selected_queue():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            subscription = await client.subscribe(handle)
            await client.wait_item(subscription)
            timer = await client.create_timer(0)
            await client.call(handle, "emit", {"kept": True})
            result = await client.wait_any(
                [
                    {"kind": "timer", "reference": timer},
                    {"kind": "subscription", "reference": subscription},
                ]
            )
            assert result["target"]["reference"] == timer and result["event"]["status"] == "FIRED"
            assert (await client.wait_item(subscription))["value"] == {"kept": True}
            assert (await client.wait_item(timer, kind="timer"))["event"]["status"] == "FIRED"
            cancelled = await client.create_timer(1000)
            await client.cancel_timer(cancelled)
            assert (await client.wait_item(cancelled, kind="timer"))["event"][
                "status"
            ] == "CANCELLED"
            await denied(client.dup(timer), "INVALID_ARGUMENT")
            await denied(host.peer.cancel_timer(timer), "INVALID_HANDLE")
            invocation = await client.invoke(handle, "echo", "multiwait")
            item = await client.wait_any([{"kind": "invocation", "reference": invocation}])
            assert item["value"] == "multiwait" and item["delivery_status"] == "DELIVERED"
            await denied(
                client.wait_any([{"kind": "timer", "reference": timer}] * 2), "INVALID_ARGUMENT"
            )
        assert_clean(host)

    asyncio.run(scenario())


def test_close_and_expiry_wake_event_waiters_and_release_provider_open():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main/short")
            subscription = await client.subscribe(handle)
            await client.wait_item(subscription)
            expired = await client.wait_item(subscription)
            assert expired["event"]["status"] == "REVOKED" and expired["value"] is None
            ordinary = await client.open("/scheme/ability/main")
            sub = await client.subscribe(ordinary)
            await client.wait_item(sub)
            baseline = (await client.stats())["event_waiters"]
            waiter = asyncio.create_task(client.wait_item(sub))
            await until(lambda: event_waiting(host, baseline))
            await client.close(sub)
            assert (await waiter)["event"]["status"] == "CLOSED"
        assert_clean(host)

    asyncio.run(scenario())


def test_inflight_subscribe_and_channel_rollback_after_revocation():
    async def scenario(operation):
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open(f"/scheme/ability/main/slow-{operation}")
            establishing = asyncio.create_task(
                client.subscribe(handle) if operation == "subscribe" else client.channel(handle)
            )

            async def pending():
                return operation in (await probe(host))["pending_setups"]

            await until(pending)
            before = await probe(host)
            await client.revoke({"type": "handle", "handle": handle})
            await release(host, operation)
            await denied(establishing, "REVOKED")
            after = await probe(host)
            assert after["opened"] == before["opened"] + 1
            # 来源资源与新建立记录都已关闭。
            assert after["closed"] == before["closed"] + 2
            stats = await client.stats()
            assert stats["channels"] == stats["pipe_fds"] == stats["event_records"] == 0
        assert_clean(host)

    asyncio.run(scenario("subscribe"))
    asyncio.run(scenario("channel"))


def test_real_upload_short_write_backpressure_busy_and_normal_eof():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            channel = await client.channel(handle, direction="upload", quota=4096)
            writer = channel["handle_id"]
            assert channel["effective_quota"] == 4096
            await denied(client.dup(writer), "INVALID_ARGUMENT")
            await denied(client.read(writer), "ACCESS_DENIED")
            payload = b"a" * 12000
            accepted = await client.write(writer, payload)
            assert 0 < accepted < len(payload) and accepted == 4096
            blocked = asyncio.create_task(client.write(writer, b"tail"))
            await until(lambda: io_waiting(host))
            await denied(client.write(writer, b"second"), "BUSY")
            first = await stream_command(host, channel, "read")
            assert bytes.fromhex(first["hex"]) == payload[:accepted] and not first["eof"]
            assert await blocked == 4
            await client.close(writer)
            tail = await stream_command(host, channel, "read")
            assert bytes.fromhex(tail["hex"]) == b"tail" and not tail["eof"]
            assert await stream_command(host, channel, "read") == {"hex": "", "eof": True}
            await stream_command(host, channel, "close")
            stats = await client.stats()
            assert stats["channels"] == stats["pipe_fds"] == stats["io_waiters"] == 0
        assert_clean(host)

    asyncio.run(scenario())


def test_download_read_busy_eof_and_wrong_owner():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            channel = await client.channel(handle, direction="download")
            reader = channel["handle_id"]
            pending = asyncio.create_task(client.read(reader))
            await until(lambda: io_waiting(host))
            await denied(client.read(reader), "BUSY")
            await denied(host.peer.read(reader), "INVALID_HANDLE")
            assert (await stream_command(host, channel, "write", hex=b"download".hex()))[
                "accepted_bytes"
            ] == 8
            assert (await pending).data == b"download"
            await stream_command(host, channel, "close")
            final = await client.read(reader)
            assert final.eof and final.data == b""
            await client.close(reader)
        assert_clean(host)

    asyncio.run(scenario())


def test_close_reader_discards_pipe_and_wakes_blocked_writer():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            channel = await client.channel(handle)
            writer = channel["handle_id"]
            await client.write(writer, b"x" * 4096)
            pending = asyncio.create_task(client.write(writer, b"blocked"))
            await until(lambda: io_waiting(host))
            await stream_command(host, channel, "close")
            await denied(pending, "CHANNEL_CLOSED")
            await denied(client.write(writer, b"new"), "CHANNEL_CLOSED")
            await client.close(writer)
        assert_clean(host)

    asyncio.run(scenario())


def test_revoke_wakes_empty_reader_and_full_writer_and_invalidates_both_ends():
    async def scenario(direction):
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            channel = await client.channel(handle, direction=direction)
            endpoint = channel["handle_id"]
            if direction == "upload":
                await client.write(endpoint, b"x" * 4096)
                operation = asyncio.create_task(client.write(endpoint, b"blocked"))
            else:
                operation = asyncio.create_task(client.read(endpoint))
            await until(lambda: io_waiting(host))
            # 仅撤单个端点，也必须使整条通道及对端失效。
            await client.revoke({"type": "handle", "handle": endpoint})
            await denied(operation, "CHANNEL_REVOKED")
            await denied(
                stream_command(
                    host, channel, "read" if direction == "upload" else "write", hex="78"
                ),
                "CHANNEL_REVOKED",
            )
            assert (await client.stats())["pipe_fds"] == 0
            await client.close(endpoint)
            await stream_command(host, channel, "close")
        assert_clean(host)

    asyncio.run(scenario("upload"))
    asyncio.run(scenario("download"))


def test_stream_deadlines_validation_and_subscription_rights_do_not_leak_calls():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            upload = await client.channel(handle)
            download = await client.channel(handle, direction="download")
            await denied(client.read(handle), "INVALID_HANDLE")
            await denied(client.read(download["handle_id"], 0), "INVALID_ARGUMENT")
            await denied(client.write(upload["handle_id"], b""), "INVALID_ARGUMENT")
            await denied(client.write(upload["handle_id"], b"x" * 16385), "INVALID_ARGUMENT")
            await denied(client.read(download["handle_id"], timeout_ms=10), "TIMED_OUT")
            await client.write(upload["handle_id"], b"x" * 4096)
            baseline = (await client.stats())["invocations"]
            await denied(client.write(upload["handle_id"], b"x", timeout_ms=10), "TIMED_OUT")
            assert (await client.stats())["invocations"] == baseline
            readonly = await client.open("/scheme/ability/main/readonly")
            await denied(client.channel(readonly), "ACCESS_DENIED")
        assert_clean(host)

    asyncio.run(scenario())


def test_channel_rejects_invalid_provider_peer_and_provider_refusal_without_half_install():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            for path, code in (
                ("bad-peer", "TRANSFER_NOT_ACCEPTED"),
                ("reject-channel", "ACCESS_DENIED"),
            ):
                handle = await client.open(f"/scheme/ability/main/{path}")
                before = await client.stats()
                await denied(client.channel(handle), code)
                after = await client.stats()
                assert all(
                    after[key] == before[key]
                    for key in ("handles", "objects", "channels", "pipe_fds")
                )
        assert_clean(host)

    asyncio.run(scenario())


def test_service_exit_wakes_event_and_stream_waiters_and_expiry_discards_pipe():
    async def scenario(expiry):
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main" + ("/short" if expiry else ""))
            subscription = await client.subscribe(handle)
            await client.wait_item(subscription)
            channel = await client.channel(handle, direction="download")
            waiting = asyncio.create_task(client.read(channel["handle_id"]))
            await until(lambda: io_waiting(host))
            if not expiry:
                await host.control.kill(host.services["tools"]["domain_id"])
            await denied(waiting, "CHANNEL_REVOKED" if expiry else "CHANNEL_CLOSED")
            event = await client.wait_item(subscription)
            assert event["event"]["status"] == ("REVOKED" if expiry else "PROVIDER_UNAVAILABLE")
            assert (await client.stats())["pipe_fds"] == 0
        assert_clean(host)

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_final_delivery_gate_strips_staged_event_and_pipe_read_after_revoke():
    async def scenario(kind):
        kernel = Kernel()
        session = kernel._new_session("observer", None, "observer", False)
        source = kernel.references.nodes[session.hello["sys_handle"]]
        obj = ObjectRecord(
            kernel.new_id("object"), kernel.sys_id, "local-test", root=True, kind=kind
        )
        node = kernel.derived(session, obj, source, {"wait" if kind == "subscription" else "read"})
        if kind == "subscription":
            record = EventRecord(session.session_id, handle=node.handle_id)
            record.queue.append(({"seq": 1}, encode_payload({"secret": 42})))
            kernel.events.records[node.handle_id] = record
            meta = {"op": "wait", "targets": [{"kind": kind, "reference": node.handle_id}]}
        else:
            writer = kernel.derived(session, obj, source, {"write"})
            read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            kernel.streams.records[obj.object_id] = Stream(
                obj.object_id,
                "local-test",
                node.handle_id,
                writer.handle_id,
                read_fd,
                write_fd,
                4096,
            )
            os.write(write_fd, b"secret")
            meta = {"op": "read", "handle": node.handle_id, "max_bytes": 16}
        entered, released = asyncio.Event(), asyncio.Event()
        replies = []

        class GatedEndpoint:
            async def send(self, data, *, prepare):
                entered.set()
                await released.wait()
                replies.append(decode_envelope(prepare()))

        task = asyncio.create_task(
            kernel._answer(GatedEndpoint(), session, Envelope("request", 1, meta))
        )
        await entered.wait()
        await kernel.references.revoke(session, {"type": "handle", "handle": source.handle_id})
        released.set()
        await task
        assert replies[0].opaque_payload is None
        if kind == "subscription":
            assert replies[0].meta["event"]["status"] == "REVOKED"
        else:
            assert replies[0].error.code == "CHANNEL_REVOKED"
        for handle in list(session.handles):
            await kernel._close(session, handle)
        await kernel.aclose()
        assert kernel.stats()["pipe_fds"] == kernel.stats()["objects"] == 0

    asyncio.run(scenario("subscription"))
    asyncio.run(scenario("channel"))


def test_channel_peer_opt_in_domain_and_notification_reservation_fail_atomically():
    async def scenario():
        kernel = Kernel()
        owner = kernel._new_session("observer", None, "observer", False)
        peer = kernel._new_session("service", "foreign", "foreign", False)
        sid = kernel.new_id("scheme")
        kernel.schemes[sid] = Scheme(sid, "provider", "ability", "test", frozenset({"stream"}))
        kernel.processes.domains["provider"] = SimpleNamespace(
            state="ready", reaped=False, domain_id="provider"
        )
        handle = kernel._root_handle(owner, sid, {"stream"})
        closed = []

        async def rpc(domain, meta, payload=None, *, prepare=None):
            if prepare:
                prepare(None)
            if meta["op"] == "close":
                closed.append(meta["provider_object_id"])
                return Envelope("response", 1, {"closed": True})
            return Envelope(
                "response",
                1,
                {
                    "provider_object_id": "accepted",
                    "peer_target_session_id": peer.session_id,
                    "effective_quota": 4096,
                },
            )

        kernel._rpc = rpc
        for code in ("TRANSFER_NOT_ACCEPTED", "ACCESS_DENIED", "EVENT_QUEUE_FULL"):
            before = kernel.stats()
            with pytest.raises(KernelFault) as failed:
                await kernel.streams.create(owner, {"handle": handle, "direction": "upload"})
            assert failed.value.code == code
            after = kernel.stats()
            assert all(
                before[key] == after[key] for key in ("handles", "objects", "channels", "pipe_fds")
            )
            if code == "TRANSFER_NOT_ACCEPTED":
                peer.accept_channels = True
            elif code == "ACCESS_DENIED":
                peer.domain_id = "provider"
                for _ in range(8):
                    peer.events.put_nowait({"type": "test"})
        assert len(closed) == 3
        kernel.processes.domains.clear()
        for session in (owner, peer):
            for reference in list(session.handles):
                await kernel._close(session, reference)
        await kernel.aclose()

    asyncio.run(scenario())


def test_timer_inherits_shortened_permission_and_wakes_before_due():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            capability = (
                await client.dup(client.session["time_handle"], expiry=time.monotonic() + 0.08)
            )["handle_id"]
            timer = (await client.call(capability, "create_timer", {"milliseconds": 1000}))[
                "handle_id"
            ]
            result = await client.wait_item(timer, kind="timer", timeout_ms=300)
            assert result["event"]["status"] == "REVOKED"
        assert_clean(host)

    asyncio.run(scenario())


def test_logical_retire_ends_event_and_stream_access_without_faking_reap():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            subscription = await client.subscribe(handle)
            await client.wait_item(subscription)
            channel = await client.channel(handle, direction="download")
            pending = asyncio.create_task(client.read(channel["handle_id"]))
            await until(lambda: io_waiting(host))
            status = await host.control.call(
                host.control.session["proc_handle"],
                "retire",
                {"domain": host.services["tools"]["domain_id"]},
            )
            assert not status["reaped"]
            await denied(pending, "CHANNEL_CLOSED")
            assert (await client.wait_item(subscription))["event"][
                "status"
            ] == "PROVIDER_UNAVAILABLE"
        assert_clean(host)

    asyncio.run(scenario())


def test_expected_stop_with_active_stream_and_subscription_keeps_stopped_state():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            subscription = await client.subscribe(handle)
            await client.wait_item(subscription)
            channel = await client.channel(handle, direction="download")
            pending = asyncio.create_task(client.read(channel["handle_id"]))
            await until(lambda: io_waiting(host))
            status = await host.control.kill(host.services["tools"]["domain_id"])
            assert status["state"] == "stopped" and status["reaped"] and status["exit_code"] == -15
            await denied(pending, "CHANNEL_CLOSED")
        assert_clean(host)
        assert all(domain["state"] == "stopped" for domain in host.final_report["domains"])

    asyncio.run(scenario())
