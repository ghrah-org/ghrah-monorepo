# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

import asyncio
from dataclasses import replace

import pytest
from _mechanism.bootstrap import open_supervised_chain
from _mechanism.contracts import DEFAULT_SERVICES
from test_references import active, denied, release, until
from test_service_chain import assert_clean

from ghrah_kernel.client import RemoteError
from ghrah_kernel.protocol import ErrorRecord
from ghrah_kernel.supervisor import ServiceSpec


def services(**changes):
    return (DEFAULT_SERVICES[0], replace(DEFAULT_SERVICES[1], **changes), DEFAULT_SERVICES[2])


async def crash(client, handle, code=17):
    invocation = await client.invoke(handle, "fault", {"mode": "exit", "code": code})
    result = await client.wait(invocation)
    assert result.settlement_state == "FAILED" and result.dispatch_fact == "DISPATCHED"
    await client.close(invocation)


def assert_supervised_clean(host):
    assert_clean(host)
    assert not host.supervisor.failure
    assert host.supervisor._task is None or host.supervisor._task.done()
    assert host.final_report["stats"]["policy_requests"] == 0
    assert host.final_report["stats"]["supervision_waiters"] == 0
    assert not host.final_report["unhandled_policy"]
    assert all(
        host.supervisor.view(name)["desired"] == "stopped" for name in host.supervisor.states
    )


def test_supervisor_boots_real_agent_chain_and_stops_in_reverse_order():
    async def scenario():
        async with open_supervised_chain() as host:
            agent = await host.observer.open("/scheme/agent/demo")
            result = await host.observer.call(
                agent, "run", {"steps": [{"action": "add", "arguments": {"a": 2, "b": 3}}]}
            )
            assert result["outputs"] == [{"sum": 5}]
            assert result["agent_parent_pid"] == host.process.pid
            assert all(
                host.supervisor.view(name)["phase"] == "Running" for name in host.supervisor.states
            )
            with pytest.raises(ValueError):
                await host.supervisor.restart_service("names")
            await host.observer.close(agent)
            await denied(
                host.observer.wait_any(
                    [
                        {
                            "kind": "supervision",
                            "reference": host.control.session["supervision_events"],
                        }
                    ]
                ),
                "ACCESS_DENIED",
            )
        assert_supervised_clean(host)
        stopped = [x["service_key"] for x in host.supervisor.history if x["event"] == "stopped"]
        assert stopped[-3:] == ["worker", "tools", "names"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "policy,code,restarts",
    [
        ("never", 17, False),
        ("on_failure", 0, False),
        ("on_failure", 17, True),
        ("always", 0, True),
    ],
)
def test_restart_policy_uses_kernel_exit_fact_and_explicit_binding(policy, code, restarts):
    async def scenario():
        async with open_supervised_chain(services(restart=policy)) as host:
            supervisor, client = host.supervisor, host.observer
            old = supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            await crash(client, handle, code)
            state = await supervisor.wait_for(
                "tools",
                lambda x: (
                    x["phase"] == "Running"
                    and x["kernel"]["domain_generation"] == old["domain_generation"] + 1
                    if restarts
                    else x["phase"] == "Exited"
                ),
            )
            assert (await host.control.status(old["domain_id"]))["exit_code"] == code
            await denied(client.invoke(handle, "echo", {}), "STALE_REFERENCE")
            if restarts:
                fresh = await client.open("/scheme/ability/main")
                identity = await client.call(fresh, "identity", {})
                assert identity["pid"] == state["kernel"]["pid"] != old["pid"]
                assert identity["parent_pid"] == host.process.pid
                assert state["restart_count"] == 1
                await client.close(fresh)
            else:
                assert state["restart_count"] == 0 and state["next_retry"] is None
            await client.close(handle)
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_explicit_stop_during_always_backoff_cancels_resurrection():
    async def scenario():
        async with open_supervised_chain(services(restart="always")) as host:
            supervisor, client = host.supervisor, host.observer
            handle = await client.open("/scheme/ability/main")
            await crash(client, handle)
            backoff = await supervisor.wait_for("tools", lambda x: x["phase"] == "Backoff")
            stopped = await supervisor.stop_service("tools")
            assert stopped["desired"] == "stopped" and stopped["next_retry"] is None
            await asyncio.sleep(
                max(0, backoff["next_retry"] - asyncio.get_running_loop().time()) + 0.03
            )
            await supervisor.reconcile()
            matching = [
                x
                for x in (await host.control.process_snapshot())["domains"]
                if x["service_key"] == "tools"
            ]
            assert len(matching) == 1 and matching[0]["reaped"]
            assert supervisor.view("tools")["restart_count"] == 0
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_stop_during_registration_fences_new_instance_and_does_not_publish_binding():
    async def scenario():
        async with open_supervised_chain(services(restart="always")) as host:
            supervisor = host.supervisor
            await supervisor.stop_service("tools")
            original = host.control.register
            entered, released = asyncio.Event(), asyncio.Event()

            async def gated(*args, **kwargs):
                entered.set()
                await released.wait()
                return await original(*args, **kwargs)

            host.control.register = gated
            starting = asyncio.create_task(supervisor.start_service("tools"))
            await entered.wait()
            stopping = asyncio.create_task(supervisor.stop_service("tools"))

            async def desired_stopped():
                return supervisor.view("tools")["desired"] == "stopped"

            await until(desired_stopped)
            released.set()
            await asyncio.gather(starting, stopping)
            host.control.register = original
            state = supervisor.view("tools")
            assert state["phase"] == "Stopped" and state["kernel"]["reaped"]
            assert "tools" not in supervisor.registrations
            await denied(host.observer.open("/scheme/ability/main"), "STALE_REFERENCE")
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_crash_loop_uses_100_200_400_backoff_then_enters_failed():
    async def scenario():
        async with open_supervised_chain(services(fixture="exit")) as host:
            supervisor = host.supervisor
            state = await supervisor.wait_for("tools", lambda x: x["phase"] == "Failed")
            assert (
                state["restart_count"] == 3
                and state["desired"] == "running"
                and state["next_retry"] is None
            )
            backoffs = [
                x
                for x in supervisor.history
                if x["service_key"] == "tools" and x["event"] == "backoff"
            ]
            assert [x["delay_ms"] for x in backoffs] == [100, 200, 400]
            starts = [
                x
                for x in supervisor.history
                if x["service_key"] == "tools" and x["event"] == "starting"
            ][1:]
            assert all(start["time"] >= item["next_retry"] for start, item in zip(starts, backoffs))
            actual = [
                x
                for x in (await host.control.process_snapshot())["domains"]
                if x["service_key"] == "tools"
            ]
            assert len(actual) == 4 and all(x["reaped"] and x["exit_code"] == 17 for x in actual)
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_call_deadline_keep_policy_preserves_domain_and_immutable_result():
    async def scenario():
        async with open_supervised_chain(services(timeout_action="keep")) as host:
            supervisor, client = host.supervisor, host.observer
            old = supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle, "delay", {"gate": "policy-keep", "ignore_cancel": True}, timeout_ms=50
            )
            await until(lambda: active(host, "policy-keep"))
            result = await client.wait(invocation)
            assert result.settlement_state == "TIMED_OUT" and result.effect_evidence == "UNKNOWN"
            await supervisor.wait_for(
                "tools",
                lambda _: any(
                    x["event"] == "policy" and x["decision"] == "keep" for x in supervisor.history
                ),
            )
            assert not (await host.control.status(old["domain_id"]))["reaped"]
            await release(host, "policy-keep")
            assert await client.call(handle, "echo", "alive") == "alive"
            assert await client.wait(invocation) == result
            await client.close(invocation)
        assert_supervised_clean(host)
        assert any(x["decision"] == "keep" for x in host.final_report["policy_decisions"])

    asyncio.run(scenario())


def test_spin_ignoring_term_is_killed_by_kernel_and_restarted_by_policy():
    async def scenario():
        async with open_supervised_chain(
            services(fixture="ignore_term", timeout_action="kill")
        ) as host:
            supervisor, client = host.supervisor, host.observer
            old = supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(handle, "fault", {"mode": "spin"}, timeout_ms=50)
            result = await client.wait(invocation)
            assert result.policy_action_required and result.effect_evidence == "UNKNOWN"
            fresh = await supervisor.wait_for(
                "tools",
                lambda x: (
                    x["phase"] == "Running"
                    and x["kernel"]["domain_generation"] == old["domain_generation"] + 1
                ),
            )
            ended = await host.control.status(old["domain_id"])
            assert ended["reaped"] and ended["forced"] and ended["exit_code"] == -9
            assert fresh["restart_count"] == 1
            await denied(client.invoke(handle, "identity", {}), "STALE_REFERENCE")
            new = await client.open("/scheme/ability/main")
            assert (await client.call(new, "identity", {}))["parent_pid"] == host.process.pid
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_pending_old_timeout_is_acknowledged_stale_without_killing_new_generation():
    async def scenario():
        async with open_supervised_chain(services(timeout_action="kill"), monitor=False) as host:
            supervisor, client = host.supervisor, host.observer
            handle = await client.open("/scheme/ability/main")
            old = supervisor.view("tools")["kernel"]
            invocation = await client.invoke(
                handle, "delay", {"gate": "stale", "ignore_cancel": True}, timeout_ms=50
            )
            assert (await client.wait(invocation)).policy_action_required
            await client.close(invocation)
            replacement = await supervisor.restart_service("tools")
            assert replacement["kernel"]["domain_generation"] == old["domain_generation"] + 1
            await supervisor.reconcile()
            assert not (await host.control.status(replacement["kernel"]["domain_id"]))["reaped"]
            fresh = await client.open("/scheme/ability/main")
            assert await client.call(fresh, "echo", "new") == "new"
        assert_supervised_clean(host)
        request = next(
            x for x in host.final_report["policy_decisions"] if x["invocation_id"] == invocation
        )
        assert request["decision"] == "stale" and request["domain_id"] == old["domain_id"]

    asyncio.run(scenario())


def test_notification_gap_rebuilds_all_pending_policy_facts_after_observation_close():
    async def scenario():
        async with open_supervised_chain(monitor=False) as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            for _ in range(33):
                invocation = await client.invoke(
                    handle, "delay", {"milliseconds": 1000}, timeout_ms=30
                )
                result = await client.wait(invocation)
                assert result.policy_action_required
                await client.close(invocation)
            assert len((await host.control.process_snapshot())["policy_requests"]) == 33
            assert (await host.control.wait_supervision())["type"] == "FACTS_GAP"
            await host.supervisor.reconcile()
            assert not (await host.control.process_snapshot())["policy_requests"]
            assert not host.supervisor.view("tools")["kernel"]["reaped"]
        assert_supervised_clean(host)
        assert len(host.final_report["policy_decisions"]) == 33

    asyncio.run(scenario())


def test_registration_failure_reaps_created_instance_and_keeps_error_observable():
    async def scenario():
        async with open_supervised_chain(services(restart="never"), monitor=False) as host:
            await host.supervisor.stop_service("tools")
            original = host.control.register

            async def rejected(*args, **kwargs):
                raise RemoteError(ErrorRecord("CONTRACT_MISMATCH", "测试核定失败"))

            host.control.register = rejected
            result = await host.supervisor.start_service("tools")
            host.control.register = original
            assert result["phase"] == "Failed" and "CONTRACT_MISMATCH" in result["error"]
            assert result["kernel"]["reaped"] and "tools" not in host.supervisor.registrations
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_drain_finishes_accepted_call_and_rejects_new_work_without_exit_claim():
    async def scenario():
        async with open_supervised_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle, "delay", {"milliseconds": 100, "value": "finished"}
            )
            await until(lambda: active(host, "timed"))
            domain = host.supervisor.view("tools")["kernel"]["domain_id"]
            result = await host.control.drain(domain, timeout_ms=300)
            assert result["drained"] and not result["reaped"] and result["state"] == "draining"
            assert (await client.wait(invocation)).value == "finished"
            await denied(client.invoke(handle, "echo", {}), "PROVIDER_UNAVAILABLE")
            assert not (await host.control.wait_domain(domain, timeout_ms=0))["reaped"]
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_stop_with_unfinished_call_uses_finite_drain_then_kernel_reap():
    async def scenario():
        async with open_supervised_chain() as host:
            handle = await host.observer.open("/scheme/ability/main")
            invocation = await host.observer.invoke(
                handle, "delay", {"gate": "stop-inflight", "ignore_cancel": True}
            )
            await until(lambda: active(host, "stop-inflight"))
            stopped = await host.supervisor.stop_service("tools")
            assert stopped["kernel"]["reaped"]
            result = await host.observer.wait(invocation)
            assert result.settlement_state == "FAILED" and result.effect_evidence == "UNKNOWN"
            assert any(
                x["event"] == "drain" and x["remaining_calls"] > 0 for x in host.supervisor.history
            )
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_manifest_rejects_unimplemented_policy_and_namespace_migration():
    with pytest.raises(ValueError):
        ServiceSpec("tools", "ability", restart="sometimes")
    with pytest.raises(ValueError):
        ServiceSpec("names", "namespace", restart="always")


def test_restart_budget_window_expires_and_backoff_returns_to_first_step():
    async def scenario():
        async with open_supervised_chain(services(window_seconds=1)) as host:
            client, supervisor = host.observer, host.supervisor
            for generation in (2, 3):
                handle = await client.open("/scheme/ability/main")
                await crash(client, handle)
                await supervisor.wait_for(
                    "tools",
                    lambda x: (
                        x["phase"] == "Running" and x["kernel"]["domain_generation"] == generation
                    ),
                )
                await client.close(handle)
                if generation == 2:
                    last = supervisor.states["tools"].attempts[-1]
                    await asyncio.sleep(max(0, last + 1.03 - asyncio.get_running_loop().time()))
            delays = [
                x["delay_ms"]
                for x in supervisor.history
                if x["event"] == "backoff" and x["service_key"] == "tools"
            ]
            assert delays == [100, 100] and supervisor.view("tools")["restart_count"] == 2
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_lost_registration_result_replays_committed_bundle_before_cleanup():
    async def scenario():
        async with open_supervised_chain(services(restart="never"), monitor=False) as host:
            await host.supervisor.stop_service("tools")
            before = await host.control.stats()
            original, lost = host.control.register, False

            async def once(*args, **kwargs):
                nonlocal lost
                result = await original(*args, **kwargs)
                if not lost:
                    lost = True
                    raise TimeoutError("模拟已提交注册的回执丢失")
                return result

            host.control.register = once
            result = await host.supervisor.start_service("tools")
            host.control.register = original
            assert result["phase"] == "Failed" and result["kernel"]["reaped"]
            after = await host.control.stats()
            assert before["handles"] == after["handles"] and before["objects"] == after["objects"]
        assert_supervised_clean(host)

    asyncio.run(scenario())
