import asyncio
import time

import pytest
from _mechanism.bootstrap import open_service_chain
from test_service_chain import assert_clean

from ghrah_kernel.client import RemoteError
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.tables import KernelFault
from ghrah_kernel.protocol import Envelope, encode_envelope
from ghrah_kernel.wire import MAX_MESSAGE_BYTES


async def denied(awaitable, code):
    with pytest.raises(RemoteError) as failed:
        await awaitable
    assert failed.value.code == code


async def probe(host):
    return await host.control.call(host.services["tools"]["scheme_handles"]["ability"], "probe", {})


async def release(host, gate):
    return await host.control.call(
        host.services["tools"]["scheme_handles"]["ability"], "release", {"gate": gate}
    )


async def until(check):
    async with asyncio.timeout(2):
        while not await check():
            await asyncio.sleep(0.002)


async def active(host, gate):
    return gate in (await probe(host))["active_calls"]


def basis(host, grant="main", scheme="ability"):
    return {
        "type": "basis",
        "scheme_id": host.services["tools"]["scheme_refs"][scheme]["scheme_id"],
        "grant_id": grant,
        "revision": 1,
    }


def test_fixture_limits_and_same_session_attenuation_do_not_reopen_broader_rights():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            readonly = await client.open("/scheme/ability/main/readonly")
            base = (await client.dup(client.session["ns_root_handle"], can_open=False))["handle_id"]
            await denied(client.open("/scheme/ability/main", base=base), "ACCESS_DENIED")
            await denied(client.dup(base, can_open=True), "ACCESS_DENIED")
            await denied(client.invoke(readonly, "counter", {}), "ACCESS_DENIED")
            await denied(client.open("", base=readonly), "ACCESS_DENIED")
            child = (
                await client.dup(
                    readonly, actions=["echo"], depth_left=0, transfer_policy={"kind": "none"}
                )
            )["handle_id"]
            await denied(client.dup(child, actions=["echo", "counter"]), "ACCESS_DENIED")
            await denied(client.dup(child, depth_left=1), "ACCESS_DENIED")
            await denied(
                client.dup(
                    child, transfer_policy={"kind": "allowlist", "principals": ["observer"]}
                ),
                "ACCESS_DENIED",
            )
            await client.close(readonly)
            assert await client.call(child, "echo", {"rights": ["counter"]}) == {
                "rights": ["counter"]
            }
            await denied(client.invoke(child, "identity", {}), "ACCESS_DENIED")
            await client.close(child)
        assert_clean(host)

    asyncio.run(scenario())


def test_running_invocation_expiry_and_effectful_cooperative_cancel_keep_evidence():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            short = await client.open("/scheme/ability/main/short")
            invocation = await client.invoke(
                short, "delay", {"gate": "expires", "ignore_cancel": True}
            )
            await until(lambda: active(host, "expires"))
            expired = await client.wait(invocation)
            assert expired.settlement_state == "REVOKED" and expired.delivery_status == "EXPIRED"
            assert expired.effect_evidence == "UNKNOWN"
            await release(host, "expires")
            handle = await client.open("/scheme/ability/main")
            effectful = await client.invoke(handle, "counter", {"gate": "effect-cancel"})
            await until(lambda: active(host, "effect-cancel"))
            await client.cancel(effectful)
            result = await client.wait(effectful)
            assert result.settlement_state == "CANCELLED"
            assert result.effect_evidence == "PROVIDER_REPORTED_EFFECT"
            assert (await probe(host))["count"] == 1
        assert_clean(host)

    asyncio.run(scenario())


def test_transfer_is_atomic_opt_in_bounded_and_session_local():
    async def scenario():
        async with open_service_chain(peer_accepts=False) as host:
            source, target = host.observer, host.peer
            handle = await source.open("/scheme/ability/main")
            before = await source.stats()
            await denied(
                source.dup(handle, target_session=target.session["session_id"]),
                "TRANSFER_NOT_ACCEPTED",
            )
            assert (await source.stats())["handles"] == before["handles"]
        assert_clean(host)
        async with open_service_chain(peer_accepts=True) as host:
            source, target = host.observer, host.peer
            handle = await source.open("/scheme/ability/main")
            receipts = [
                await source.dup(
                    handle, actions=["echo"], target_session=target.session["session_id"]
                )
                for _ in range(8)
            ]
            before = await source.stats()
            await denied(
                source.dup(handle, target_session=target.session["session_id"]), "EVENT_QUEUE_FULL"
            )
            assert (await source.stats())["handles"] == before["handles"]
            receipt = receipts[0]
            event = await target.wait_event()
            assert event == {"type": "handle_installed", **receipt}
            assert receipt["depth_left"] == 0
            await denied(source.invoke(receipt["handle_id"], "echo", {}), "INVALID_HANDLE")
            await denied(
                target.dup(receipt["handle_id"], target_session=host.control.session["session_id"]),
                "ACCESS_DENIED",
            )
            await source.close(handle)
            assert await target.call(receipt["handle_id"], "echo", {"transferred": True}) == {
                "transferred": True
            }
            for receipt in receipts:
                await target.close(receipt["handle_id"])
        assert_clean(host)

    asyncio.run(scenario())


def test_same_principal_and_no_transfer_policies_and_sender_disconnect():
    async def scenario():
        async with open_service_chain(peer_accepts=True) as host:
            source, target = host.observer, host.peer
            handle = await source.open("/scheme/ability/main/same")
            await denied(
                source.dup(handle, target_session=host.control.session["session_id"]),
                "ACCESS_DENIED",
            )
            never = await source.open("/scheme/ability/main/none")
            await denied(
                source.dup(never, target_session=target.session["session_id"]), "ACCESS_DENIED"
            )
            receipt = await source.dup(
                handle, actions=["echo"], target_session=target.session["session_id"]
            )
            assert (await target.wait_event())["handle_id"] == receipt["handle_id"]
            await source.aclose()
            assert await target.call(receipt["handle_id"], "echo", "still held") == "still held"
            await host.control.revoke({"type": "handle", "handle": handle})
            await denied(target.invoke(receipt["handle_id"], "echo", {}), "REVOKED")
        assert_clean(host)

    asyncio.run(scenario())


def test_closed_ancestor_revokes_descendants_without_touching_independent_open():
    async def scenario():
        async with open_service_chain(peer_accepts=True) as host:
            owner = host.observer
            parent = await owner.open("/scheme/ability/main")
            child = (await owner.dup(parent))["handle_id"]
            grandchild = (await owner.dup(child, target_session=host.peer.session["session_id"]))[
                "handle_id"
            ]
            independent = await owner.open("/scheme/ability/main")
            await owner.close(parent)
            await owner.close(child)
            await denied(host.peer.revoke({"type": "handle", "handle": parent}), "ACCESS_DENIED")
            first = await owner.revoke({"type": "handle", "handle": parent})
            repeat = await owner.revoke({"type": "handle", "handle": parent})
            assert first["invalidated"] == 3
            assert repeat["invalidated"] == 0 and repeat["revision"] == first["revision"]
            await denied(host.peer.invoke(grandchild, "echo", {}), "REVOKED")
            assert await owner.call(independent, "add", {"a": 3, "b": 4}) == {"sum": 7}
        assert_clean(host)

    asyncio.run(scenario())


def test_basis_scope_completed_facts_and_payload_delivery_after_revocation():
    async def scenario():
        async with open_service_chain(peer_accepts=True) as host:
            client = host.observer
            parent = await client.open("/scheme/ability/main")
            child = (await client.dup(parent, target_session=host.peer.session["session_id"]))[
                "handle_id"
            ]
            view = await client.open("/scheme/view/main")
            invocation = await client.invoke(parent, "echo", {"secret": 42})
            first = await client.wait(invocation)
            assert first.value == {"secret": 42} and first.delivery_status == "DELIVERED"
            await denied(client.revoke(basis(host)), "ACCESS_DENIED")
            revoked = await host.control.revoke(basis(host))
            repeat = await host.control.revoke(basis(host))
            assert repeat["revision"] == revoked["revision"] and repeat["invalidated"] == 0
            second = await client.wait(invocation)
            assert (second.settlement_state, second.dispatch_fact, second.effect_evidence) == (
                first.settlement_state,
                first.dispatch_fact,
                first.effect_evidence,
            )
            assert second.value is None and second.delivery_status == "REVOKED"
            await denied(host.peer.invoke(child, "echo", {}), "REVOKED")
            await denied(client.open("/scheme/ability/main"), "REVOKED")
            assert (await client.call(view, "identity", {}))["pid"] == host.services["tools"]["pid"]
        assert_clean(host)

    asyncio.run(scenario())


def test_slow_open_rechecks_basis_and_parent_and_releases_provider_record():
    async def scenario(use_parent):
        async with open_service_chain() as host:
            client = host.observer
            base = (await client.dup(client.session["ns_root_handle"]))["handle_id"]
            opening = asyncio.create_task(client.open("/scheme/ability/main/slow", base=base))

            async def pending():
                return "slow" in (await probe(host))["pending_opens"]

            await until(pending)
            before = await probe(host)
            if use_parent:
                await client.revoke({"type": "handle", "handle": base})
            else:
                await host.control.revoke(basis(host, "slow"))
            await release(host, "open:slow")
            await denied(opening, "REVOKED")
            after = await probe(host)
            assert after["opened"] == before["opened"] + 1
            assert after["closed"] == before["closed"] + 1
        assert_clean(host)

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_expiry_cannot_be_extended_and_is_rechecked_after_slow_open():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main/short")
            await denied(client.dup(handle, expiry=time.monotonic() + 60), "ACCESS_DENIED")
            child = (await client.dup(handle, expiry=time.monotonic() + 0.03))["handle_id"]
            await asyncio.sleep(0.04)
            await denied(client.invoke(child, "echo", {}), "EXPIRED")
            assert await client.call(handle, "echo", 1) == 1
            opening = asyncio.create_task(client.open("/scheme/ability/main/expiring-open"))

            async def pending():
                return "expiring-open" in (await probe(host))["pending_opens"]

            await until(pending)
            await asyncio.sleep(0.06)
            before = await probe(host)
            await release(host, "open:expiring-open")
            await denied(opening, "EXPIRED")
            after = await probe(host)
            assert after["closed"] == before["closed"] + 1
        assert_clean(host)

    asyncio.run(scenario())


def test_queued_cancel_never_dispatches_and_wait_timeout_does_not_cancel():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            running = await client.invoke(handle, "delay", {"gate": "queue"})
            await until(lambda: active(host, "queue"))
            queued = await client.invoke(handle, "counter", {})
            await denied(client.wait(queued, timeout_ms=0), "WAIT_TIMEOUT")
            assert (await client.cancel(queued))["accepted"]
            result = await client.wait(queued)
            assert (result.settlement_state, result.dispatch_fact, result.effect_evidence) == (
                "CANCELLED",
                "NOT_DISPATCHED",
                "NOT_DISPATCHED",
            )
            assert not (await client.cancel(queued))["accepted"]
            await release(host, "queue")
            assert (await client.wait(running)).settlement_state == "COMPLETED"
            assert (await probe(host))["count"] == 0
        assert_clean(host)

    asyncio.run(scenario())


def test_dispatched_cancel_uses_provider_evidence_or_finite_unknown_and_ignores_late_reply():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            cooperative = await client.invoke(handle, "delay", {"gate": "cooperative"})
            await until(lambda: active(host, "cooperative"))
            await client.cancel(cooperative)
            result = await client.wait(cooperative)
            assert (result.settlement_state, result.dispatch_fact, result.effect_evidence) == (
                "CANCELLED",
                "DISPATCHED",
                "PROVIDER_REPORTED_NO_EFFECT",
            )
            stubborn = await client.invoke(
                handle,
                "delay",
                {"gate": "stubborn", "ignore_cancel": True, "value": {"late": True}},
            )
            await until(lambda: active(host, "stubborn"))
            await client.cancel(stubborn)
            frozen = await client.wait(stubborn)
            assert (frozen.settlement_state, frozen.dispatch_fact, frozen.effect_evidence) == (
                "CANCELLED",
                "DISPATCHED",
                "UNKNOWN",
            )
            await client.close(handle)
            await release(host, "stubborn")

            async def late():
                return (await client.stats())["late_replies"] > 0

            await until(late)
            assert await client.wait(stubborn) == frozen
        assert_clean(host)

    asyncio.run(scenario())


def test_deadline_and_revoke_stop_execution_without_killing_domain():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            expired = await client.invoke(handle, "counter", {}, deadline=time.monotonic() - 1)
            result = await client.wait(expired)
            assert (result.settlement_state, result.dispatch_fact, result.effect_evidence) == (
                "TIMED_OUT",
                "NOT_DISPATCHED",
                "NOT_DISPATCHED",
            )
            invocation = await client.invoke(
                handle, "delay", {"gate": "deadline", "ignore_cancel": True}, timeout_ms=60
            )
            await until(lambda: active(host, "deadline"))
            result = await client.wait(invocation)
            assert result.settlement_state == "TIMED_OUT" and result.effect_evidence == "UNKNOWN"
            assert result.policy_action_required
            await release(host, "deadline")
            running = await client.invoke(handle, "delay", {"gate": "revoked"})
            await until(lambda: active(host, "revoked"))
            queued = await client.invoke(handle, "counter", {})
            await client.revoke({"type": "handle", "handle": handle})
            first, second = await asyncio.gather(client.wait(running), client.wait(queued))
            assert first.settlement_state == "REVOKED" and first.delivery_status == "REVOKED"
            assert second.settlement_state == "REVOKED" and second.dispatch_fact == "NOT_DISPATCHED"
            assert not (await host.control.status(host.services["tools"]["domain_id"]))["reaped"]
            assert (await probe(host))["count"] == 0
        assert_clean(host)

    asyncio.run(scenario())


def test_effect_before_provider_death_is_not_misreported_as_no_effect():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle, "counter", {"gate": "effect", "ignore_cancel": True}
            )
            await until(lambda: active(host, "effect"))
            assert (await probe(host))["count"] == 1
            await host.control.kill(host.services["tools"]["domain_id"])
            result = await client.wait(invocation)
            assert result.settlement_state == "FAILED" and result.effect_evidence == "UNKNOWN"
            assert result.dispatch_fact == "DISPATCHED"
        assert_clean(host)

    asyncio.run(scenario())


def test_exact_domain_revocation_does_not_target_new_generation():
    async def scenario():
        async with open_service_chain() as host:
            old = host.services["tools"]
            first = await host.observer.open("/scheme/ability/main")
            replacement = await host.start_service("tools", "ability")
            await host.mount("/scheme/ability/main", replacement, "ability")
            fresh = await host.observer.open("/scheme/ability/main")
            selector = {
                "type": "domain_generation",
                "domain": old["domain_id"],
                "generation": old["domain_generation"],
            }
            await denied(host.control.revoke({**selector, "generation": 99}), "STALE_REFERENCE")
            await host.control.revoke(selector)
            await denied(host.observer.invoke(first, "echo", {}), "REVOKED")
            assert await host.observer.call(fresh, "add", {"a": 8, "b": 9}) == {"sum": 17}
            assert not (await host.control.status(old["domain_id"]))["reaped"]
        assert_clean(host)

    asyncio.run(scenario())


def test_revocation_tombstones_refuse_overflow_without_forgetting_first_basis():
    async def scenario():
        async with open_service_chain() as host:
            await host.control.revoke(basis(host))
            for index in range(255):
                await host.control.revoke(basis(host, f"unused-{index}"))
            before = await host.control.stats()
            assert before["revocation_tombstones"] == 256
            await denied(host.control.revoke(basis(host, "overflow")), "QUOTA_EXCEEDED")
            await denied(host.observer.open("/scheme/ability/main"), "REVOKED")
            assert (await host.control.revoke(basis(host)))["revision"] == before[
                "revocation_revision"
            ]
        assert_clean(host)

    asyncio.run(scenario())


def test_oversize_provider_envelope_is_rejected_before_dispatch_without_poisoning_domain():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            meta = {"op": "invoke", "handle": handle, "action": "echo"}
            header = len(encode_envelope(Envelope("request", client.rpc._next_id + 1, meta, b"")))
            reply = await client.rpc.exchange(meta, b"x" * (MAX_MESSAGE_BYTES - header - 4))
            assert reply.error is None
            result = await client.wait(reply.meta["invocation_id"])
            assert result.dispatch_fact == result.effect_evidence == "NOT_DISPATCHED"
            assert result.error.code == "MESSAGE_TOO_LARGE"
            assert await client.call(handle, "echo", "healthy") == "healthy"
        assert_clean(host)

    asyncio.run(scenario())


def test_failed_initial_handle_install_does_not_publish_partial_session():
    async def scenario():
        kernel = Kernel()
        admin = kernel._new_session("bootstrap", None, "bootstrap", False)
        # sys 许可根先被撤销，新会话的初始句柄安装应整体失败。
        await kernel.references.revoke(
            admin, {"type": "basis", "scheme_id": kernel.sys_id, "grant_id": "root", "revision": 1}
        )
        kernel.root_ns = kernel.proc_id
        before = kernel.stats()
        with pytest.raises(KernelFault) as failed:
            kernel._new_session("observer", None, "observer", False)
        assert failed.value.code == "REVOKED"
        after = kernel.stats()
        assert all(before[key] == after[key] for key in ("sessions_active", "handles", "objects"))
        await kernel.aclose()

    asyncio.run(scenario())
