# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

import asyncio
import threading
from copy import deepcopy

import pytest
from _mechanism.bootstrap import open_service_chain
from _mechanism.contracts import SERVICE_SCHEMES
from _mechanism.scenarios import run_service_chain

from ghrah_kernel.client import RemoteError


def assert_clean(host):
    assert host.process.returncode == 0
    assert not host.final_report["errors"]
    stats = host.final_report["stats"]
    for key in (
        "domains_live",
        "sessions_active",
        "handles",
        "objects",
        "invocations",
        "inflight",
        "event_records",
        "pending_subscriptions",
        "queued_events",
        "channels",
        "pipe_fds",
        "io_waiters",
        "event_waiters",
        "policy_requests",
        "supervision_waiters",
    ):
        assert stats[key] == 0, (key, stats)
    assert all(domain["reaped"] for domain in host.final_report["domains"])
    assert not [thread.name for thread in threading.enumerate() if thread.name.startswith("lab-")]


def test_real_agent_ability_chain_and_explicit_replacement():
    report = asyncio.run(run_service_chain())
    assert report["passed"]
    assert report["generations"] == [1, 2]
    assert report["old_handle_result"] == "STALE_REFERENCE"
    assert report["agent_run"]["agent_parent_pid"] == report["kernel_pid"]
    assert report["ability_identity"]["parent_pid"] == report["kernel_pid"]
    assert report["final"]["stats"]["objects"] == 0


def test_spawn_replay_and_atomic_bundle_registration():
    async def scenario():
        async with open_service_chain() as host:
            admin = host.control
            first, replay = await asyncio.gather(
                admin.spawn("atomic", "ability", spawn_key="same"),
                admin.spawn("atomic", "ability", spawn_key="same"),
            )
            assert first["domain_id"] == replay["domain_id"]
            bad = deepcopy(SERVICE_SCHEMES["ability"])
            bad[1]["contract"] = "conflicting-contract"
            with pytest.raises(RemoteError) as failed:
                await admin.register(first["domain_id"], bad, "bad")
            assert failed.value.code == "CONTRACT_MISMATCH"
            status = await admin.status(first["domain_id"])
            assert status["state"] == "starting" and status["schemes"] == {}
            good = await admin.register(first["domain_id"], SERVICE_SCHEMES["ability"], "good")
            assert set(good["scheme_refs"]) == {"ability", "view"}
            assert (
                await admin.register(first["domain_id"], SERVICE_SCHEMES["ability"], "good") == good
            )
            with pytest.raises(RemoteError) as conflict:
                await admin.register(first["domain_id"], SERVICE_SCHEMES["ability"], "other-key")
            assert conflict.value.code == "REGISTRATION_CONFLICT"
            await admin.kill(first["domain_id"])
            with pytest.raises(RemoteError) as retired:
                await admin.spawn("atomic", "ability", spawn_key="same")
            assert retired.value.code == "DOMAIN_RETIRED"
        assert_clean(host)

    asyncio.run(scenario())


def test_session_ownership_action_gates_and_invocation_ownership():
    async def scenario():
        async with open_service_chain() as host:
            guest, admin = host.observer, host.control
            handle = await guest.open("/scheme/ability/main", actions=["echo", "identity"])
            try:
                with pytest.raises(RemoteError) as foreign:
                    await admin.invoke(handle, "identity", {})
                assert foreign.value.code == "INVALID_HANDLE"
                with pytest.raises(RemoteError) as denied:
                    await guest.invoke(handle, "add", {"a": 1, "b": 2})
                assert denied.value.code == "ACCESS_DENIED"
                with pytest.raises(RemoteError) as registration:
                    await guest.register("not-a-domain", [], "guest")
                assert registration.value.code == "ACCESS_DENIED"
                with pytest.raises(RemoteError) as binding:
                    await guest.invoke(guest.session["ns_root_handle"], "bind", {})
                assert binding.value.code == "ACCESS_DENIED"
                business = {"op": "register", "action": "kill", "credential": "业务字段"}
                invocation = await guest.invoke(handle, "echo", business)
                with pytest.raises(RemoteError) as observation:
                    await admin.wait(invocation)
                assert observation.value.code == "INVALID_INVOCATION"
                assert (await guest.wait(invocation)).value == business
                await guest.close(invocation)
            finally:
                await guest.close(handle)
            with pytest.raises(RemoteError) as closed:
                await guest.invoke(handle, "identity", {})
            assert closed.value.code == "INVALID_HANDLE"
        assert_clean(host)

    asyncio.run(scenario())


def test_wait_timeout_and_handle_close_preserve_inflight_call():
    async def scenario():
        async with open_service_chain() as host:
            guest = host.observer
            baseline = await guest.stats()
            handle = await guest.open("/scheme/ability/main")
            invocation = await guest.invoke(
                handle,
                "delay",
                {
                    "milliseconds": 100,
                    "value": {"finished": True},
                },
            )
            with pytest.raises(RemoteError) as waiting:
                await guest.wait(invocation, timeout_ms=0)
            assert waiting.value.code == "WAIT_TIMEOUT"
            await guest.close(handle)
            result = await guest.wait(invocation)
            assert result.value == {"finished": True}
            assert result.settlement_state == "COMPLETED"
            assert result.dispatch_fact == "DISPATCHED"
            assert await guest.wait(invocation) == result
            await guest.close(invocation)
            after = await guest.stats()
            for key in ("handles", "objects", "invocations"):
                assert after[key] == baseline[key], (key, baseline, after)
        assert_clean(host)

    asyncio.run(scenario())


def test_namespace_cannot_install_unregistered_or_wrong_generation_target():
    async def scenario():
        async with open_service_chain() as host:
            ns = host.services["names"]["scheme_handles"]["ns"]
            await host.control.call(
                ns,
                "bind",
                {
                    "path": "/missing",
                    "target": {
                        "scheme_id": "never-registered",
                        "domain_generation": 1,
                    },
                },
            )
            with pytest.raises(RemoteError) as missing:
                await host.observer.open("/missing")
            assert missing.value.code == "NOT_FOUND"
            wrong = dict(host.services["tools"]["scheme_refs"]["ability"])
            wrong["domain_generation"] += 1
            await host.control.call(ns, "bind", {"path": "/wrong", "target": wrong})
            with pytest.raises(RemoteError) as mismatch:
                await host.observer.open("/wrong")
            assert mismatch.value.code == "STALE_REFERENCE"
        assert_clean(host)

    asyncio.run(scenario())


def test_two_live_generations_require_explicit_namespace_rebind():
    async def scenario():
        async with open_service_chain() as host:
            guest = host.observer
            first = host.services["tools"]
            old = await guest.open("/scheme/ability/main")
            second = await host.start_service("tools", "ability")
            still_old = await guest.open("/scheme/ability/main")
            assert (await guest.call(still_old, "identity", {}))["pid"] == first["pid"]
            await guest.close(still_old)
            await host.mount("/scheme/ability/main", second, "ability")
            fresh = await guest.open("/scheme/ability/main")
            assert (await guest.call(fresh, "identity", {}))["pid"] == second["pid"]
            assert (await guest.call(old, "identity", {}))["pid"] == first["pid"]
            await host.control.kill(first["domain_id"])
            with pytest.raises(RemoteError) as stale:
                await guest.invoke(old, "identity", {})
            assert stale.value.code == "STALE_REFERENCE"
            assert await guest.call(fresh, "add", {"a": 4, "b": 5}) == {"sum": 9}
            await guest.close(old)
            await guest.close(fresh)
        assert_clean(host)

    asyncio.run(scenario())


def test_provider_termination_settles_dispatched_call_unknown():
    async def scenario():
        async with open_service_chain() as host:
            guest = host.observer
            handle = await guest.open("/scheme/ability/main")
            invocation = await guest.invoke(handle, "delay", {"milliseconds": 1000})
            ended = await host.control.kill(host.services["tools"]["domain_id"])
            assert ended["reaped"]
            result = await guest.wait(invocation)
            assert result.settlement_state == "FAILED"
            assert result.dispatch_fact == "DISPATCHED" and result.effect_evidence == "UNKNOWN"
            assert result.error.code == "PROVIDER_UNAVAILABLE"
            await guest.close(invocation)
            await guest.close(handle)
        assert_clean(host)

    asyncio.run(scenario())


def test_agent_open_failure_recovers_and_retire_remains_physically_manageable():
    async def scenario():
        async with open_service_chain() as host:
            guest, admin = host.observer, host.control
            agent = await guest.open("/scheme/agent/demo")
            with pytest.raises(RemoteError):
                await guest.call(agent, "run", {"tool_path": "/not-mounted", "steps": []})
            assert (await guest.call(agent, "status", {}))["state"] == "idle"
            result = await guest.call(
                agent,
                "run",
                {
                    "steps": [
                        {"action": "add", "arguments": {"a": 5, "b": 6}},
                    ]
                },
            )
            assert result["outputs"] == [{"sum": 11}]
            retired = await admin.call(
                admin.session["proc_handle"],
                "retire",
                {
                    "domain": host.services["worker"]["domain_id"],
                },
            )
            assert retired["state"] == "stopped" and not retired["reaped"]
            with pytest.raises(RemoteError) as stale:
                await guest.invoke(agent, "status", {})
            assert stale.value.code == "STALE_REFERENCE"
            assert (await admin.kill(retired["domain_id"]))["reaped"]
            await guest.close(agent)
        assert_clean(host)

    asyncio.run(scenario())
