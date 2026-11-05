# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest
from _mechanism.bootstrap import open_service_chain, open_supervised_chain
from test_references import active, denied, probe, until
from test_service_chain import assert_clean
from test_supervision import assert_supervised_clean, services

from ghrah_kernel.client import RemoteError
from ghrah_kernel.kernel.runtime import Kernel


def check_report(report):
    assert report["clean"] and report["logical_invalidated"] and report["calls_settled"]
    assert report["children_reaped"] and not report["unfinished"]
    for key in (
        "domains_live",
        "handles",
        "objects",
        "invocations",
        "inflight",
        "event_records",
        "channels",
        "pipe_fds",
        "io_waiters",
        "event_waiters",
        "supervision_waiters",
    ):
        assert report["stats"][key] == 0, (key, report)


async def admission_closed(client):
    try:
        await client.open("/scheme/ability/main")
    except RemoteError as exc:
        return exc.code == "SHUTTING_DOWN"
    return False


def test_drain_completes_dispatched_call_rejects_new_work_then_reports_before_kernel_exit():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle, "delay", {"milliseconds": 300, "value": "finished"}
            )
            await until(lambda: active(host, "timed"))
            waiting = asyncio.create_task(client.wait(invocation))
            stopping = asyncio.create_task(host.control.shutdown(drain_timeout_ms=800))
            await until(lambda: admission_closed(client))
            await denied(client.invoke(handle, "echo", "new"), "SHUTTING_DOWN")
            result = await waiting
            assert result.settlement_state == "COMPLETED" and result.value == "finished"
            report = await stopping
            check_report(report)
            assert report["mode"] == "drain" and report["drained"]
            assert any(
                x["invocation_id"] == invocation and x["settlement_state"] == "COMPLETED"
                for x in report["calls"]
            )
            await asyncio.wait_for(host.process.wait(), 2)
        assert_clean(host)
        assert host.final_report["shutdown"] == report

    asyncio.run(scenario())


@pytest.mark.parametrize("mode,budget", [("now", 0), ("drain", 20)])
def test_shutdown_settles_unknown_effect_and_never_dispatches_queued_call(mode, budget):
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            first = await client.invoke(handle, "counter", {"gate": "held", "ignore_cancel": True})
            await until(lambda: active(host, "held"))
            assert (await probe(host))["count"] == 1
            second = await client.invoke(handle, "counter", {})
            report = await host.control.shutdown(mode=mode, drain_timeout_ms=budget)
            check_report(report)
            facts = {x["invocation_id"]: x for x in report["calls"]}
            assert facts[first]["settlement_state"] == "SHUTDOWN"
            assert (
                facts[first]["dispatch_fact"] == "DISPATCHED"
                and facts[first]["effect_evidence"] == "UNKNOWN"
            )
            assert facts[second]["settlement_state"] == "SHUTDOWN"
            assert (
                facts[second]["dispatch_fact"]
                == facts[second]["effect_evidence"]
                == "NOT_DISPATCHED"
            )
            assert not report["drained"]
        assert_clean(host)

    asyncio.run(scenario())


def test_shutdown_wakes_read_event_timer_and_full_pipe_write_waiters():
    async def scenario():
        async with open_service_chain() as host:
            client = host.observer
            handle = await client.open("/scheme/ability/main")
            sub = await client.subscribe(handle)
            await client.wait_item(sub)
            timer = await client.create_timer(3000)
            download = await client.channel(handle, direction="download")
            upload = await client.channel(handle, direction="upload")
            await client.write(upload["handle_id"], b"x" * upload["effective_quota"])
            tasks = [
                asyncio.create_task(client.read(download["handle_id"])),
                asyncio.create_task(client.write(upload["handle_id"], b"y")),
                asyncio.create_task(client.wait_item(sub)),
                asyncio.create_task(client.wait_item(timer, kind="timer")),
            ]

            async def blocked():
                stats = await client.stats()
                return stats["io_waiters"] == 2 and stats["event_record_waiters"] == 2

            await until(blocked)
            report = await host.control.shutdown(mode="now")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(
                isinstance(x, RemoteError) and x.code == "CHANNEL_CLOSED" for x in results[:2]
            )
            assert all(x["event"]["terminal"] for x in results[2:])
            check_report(report)
        assert_clean(host)

    asyncio.run(scenario())


def test_shutdown_requires_live_management_cap_and_valid_parameters():
    async def scenario():
        async with open_service_chain() as host:
            await denied(host.observer.shutdown(), "ACCESS_DENIED")
            await denied(
                host.control._request("shutdown", admin_cap="wrong", mode="now"), "ACCESS_DENIED"
            )
            for parameters in (
                {"mode": "other"},
                {"mode": "drain", "drain_timeout_ms": -1},
                {"mode": "now", "drain_timeout_ms": True},
                {"mode": "now", "domain_order": ["unknown"]},
                {"mode": "now", "domain_order": ["x", "x"]},
            ):
                await denied(
                    host.control._request(
                        "shutdown", admin_cap=host.control.session["proc_handle"], **parameters
                    ),
                    "NOT_FOUND"
                    if parameters.get("domain_order") == ["unknown"]
                    else "INVALID_ARGUMENT",
                )
            assert (await host.control.stats())["domains_live"] == 3
            await host.control.close(host.control.session["proc_handle"])
            await denied(host.control.shutdown(mode="now"), "INVALID_HANDLE")
            # 此用例有意关闭管理能力；让外层走断连回收而非再次调用 shutdown。
            await host.control.aclose()
            await host.process.wait()
        assert_clean(host)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["drain", "now"])
def test_supervisor_shutdown_fences_always_restart_and_reaps_ignore_term(mode):
    async def scenario():
        async with open_supervised_chain(services(restart="always", fixture="ignore_term")) as host:
            old = host.supervisor.view("tools")["kernel"]
            report = await host.supervisor.shutdown(mode=mode)
            check_report(report)
            assert [x["service_key"] for x in report["domains"]] == ["worker", "tools", "names"]
            tools = next(x for x in report["domains"] if x["domain_id"] == old["domain_id"])
            assert tools["reaped"] and tools["forced"] and tools["exit_code"] == -9
            assert host.supervisor.view("tools")["restart_count"] == 0
            assert all(
                host.supervisor.view(x)["phase"] == "Stopped" for x in host.supervisor.states
            )
            assert await host.supervisor.shutdown(mode=mode) == report
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_concurrent_identical_shutdown_joins_one_operation_conflicting_mode_rejected():
    async def scenario():
        async with open_service_chain() as host:
            handle = await host.observer.open("/scheme/ability/main")
            await host.observer.invoke(handle, "delay", {"milliseconds": 300})
            await until(lambda: active(host, "timed"))
            first = asyncio.create_task(host.control.shutdown(drain_timeout_ms=800))
            await until(lambda: admission_closed(host.observer))
            second = asyncio.create_task(host.control.shutdown(drain_timeout_ms=800))
            await denied(host.control.shutdown(mode="now"), "SHUTDOWN_CONFLICT")
            reports = await asyncio.gather(first, second)
            assert reports[0] == reports[1]
            check_report(reports[0])
        assert_clean(host)

    asyncio.run(scenario())


def test_reap_failure_is_reported_without_claiming_clean_shutdown():
    async def scenario():
        from _mechanism.contracts import mechanism_catalog

        kernel = Kernel(launch_catalog=mechanism_catalog())
        session = kernel._new_session("bootstrap", None, "bootstrap", False)
        domain = await kernel.processes.spawn("unreaped", "ability", "once")
        original = kernel.processes.kill

        async def failed(*args, **kwargs):
            raise RuntimeError("受控回收失败")

        kernel.processes.kill = failed
        try:
            report = await kernel.shutdown.request(
                session, {"admin_cap": session.hello["proc_handle"], "mode": "now"}
            )
            assert not report["clean"] and not report["children_reaped"]
            assert report["unfinished"][0]["domain_id"] == domain["domain_id"]
        finally:
            kernel.processes.kill = original
            await kernel.aclose()
        assert kernel.stats()["domains_live"] == 0

    asyncio.run(scenario())


def test_abandoned_shutdown_reply_does_not_cancel_reaping():
    async def scenario():
        async with open_service_chain() as host:
            handle = await host.observer.open("/scheme/ability/main")
            await host.observer.invoke(handle, "delay", {"milliseconds": 200})
            await until(lambda: active(host, "timed"))
            shutdown = asyncio.create_task(host.control.shutdown())
            await until(lambda: admission_closed(host.observer))
            shutdown.cancel()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            await asyncio.wait_for(host.process.wait(), 2)
        assert_clean(host)
        assert host.final_report["shutdown"]["clean"]

    asyncio.run(scenario())
