import asyncio
from types import SimpleNamespace

import pytest
from _mechanism.bootstrap import open_supervised_chain
from test_references import denied, until
from test_service_chain import assert_clean
from test_supervision import assert_supervised_clean, services

from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.tables import KernelFault


async def drop_endpoint(host, handle):
    invocation = await host.observer.invoke(handle, "fault", {"mode": "drop_endpoint"})
    result = await host.observer.wait(invocation)
    assert result.settlement_state == "FAILED" and result.effect_evidence == "UNKNOWN"
    await host.observer.close(invocation)


def test_slow_invoke_without_deadline_completes_beyond_previous_internal_budget():
    async def scenario():
        async with open_supervised_chain() as host:
            client = host.observer
            before = host.supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle, "delay", {"milliseconds": 2200, "value": "slow but healthy"}
            )
            result = await client.wait(invocation, timeout_ms=3000)
            assert result.settlement_state == "COMPLETED" and result.value == "slow but healthy"
            assert await client.call(handle, "echo", "still available") == "still available"
            actual = await host.control.status(before["domain_id"])
            assert actual["state"] == "ready" and not actual["reaped"]
            assert host.supervisor.view("tools")["restart_count"] == 0
            assert not any(x["event"] == "endpoint_recovery" for x in host.supervisor.history)
            await client.close(invocation)
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_deadline_keep_does_not_poison_endpoint_when_reply_arrives_after_two_seconds():
    async def scenario():
        async with open_supervised_chain(services(timeout_action="keep")) as host:
            client = host.observer
            before = host.supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            invocation = await client.invoke(
                handle,
                "delay",
                {"milliseconds": 2200, "ignore_cancel": True, "value": "late"},
                timeout_ms=50,
            )
            frozen = await client.wait(invocation)
            assert frozen.settlement_state == "TIMED_OUT" and frozen.effect_evidence == "UNKNOWN"
            async with asyncio.timeout(4):
                while (await client.stats())["late_replies"] == 0:
                    await asyncio.sleep(0.005)
            assert await client.wait(invocation) == frozen
            actual = await host.control.status(before["domain_id"])
            assert actual["state"] == "ready" and not actual["reaped"]
            assert host.supervisor.view("tools")["restart_count"] == 0
            assert await client.call(handle, "echo", "after late reply") == "after late reply"
            await client.close(invocation)
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_control_rpc_wait_timeout_is_not_endpoint_failure():
    async def scenario():
        kernel = Kernel()

        async def timeout(*args, **kwargs):
            raise TimeoutError("模拟控制回复等待到期")

        domain = SimpleNamespace(
            peer=SimpleNamespace(exchange=timeout),
            state="ready",
            domain_id="test",
            expected_stop=False,
        )
        with pytest.raises(KernelFault) as failed:
            await kernel._rpc(domain, {"op": "describe"})
        assert failed.value.code == "RPC_TIMEOUT"
        assert domain.state == "ready" and kernel.fact_revision == 0
        await kernel.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", ["never", "on_failure", "always"])
def test_real_broken_endpoint_with_live_process_is_reaped_then_policy_runs(policy):
    async def scenario():
        async with open_supervised_chain(
            services(restart=policy, fixture="ignore_term"), monitor=False
        ) as host:
            client, supervisor = host.observer, host.supervisor
            before = supervisor.view("tools")["kernel"]
            handle = await client.open("/scheme/ability/main")
            await drop_endpoint(host, handle)
            failed = await host.control.status(before["domain_id"])
            assert failed["state"] == "failed" and not failed["reaped"]
            await supervisor.reconcile()
            actual = await host.control.status(before["domain_id"])
            assert actual["reaped"] and actual["forced"] and actual["exit_code"] == -9
            state = supervisor.view("tools")
            assert state["phase"] != "Unavailable"
            if policy == "never":
                assert state["phase"] == "Failed" and "ENDPOINT_FAILED" in state["error"]
                assert state["restart_count"] == 0 and state["next_retry"] is None
            else:
                assert state["phase"] == "Backoff"
                await asyncio.sleep(max(0, state["next_retry"] - asyncio.get_running_loop().time()))
                await supervisor.reconcile()
                fresh = supervisor.view("tools")
                assert fresh["phase"] == "Running" and fresh["kernel"]["domain_generation"] == 2
                new = await client.open("/scheme/ability/main")
                assert await client.call(new, "echo", "recovered") == "recovered"
            await denied(client.invoke(handle, "echo", {}), "STALE_REFERENCE")
            assert any(
                x["event"] == "endpoint_recovery" and x["domain_id"] == before["domain_id"]
                for x in supervisor.history
            )
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_stop_during_endpoint_wait_prevents_restart_and_leaves_no_live_instance():
    async def scenario():
        async with open_supervised_chain(monitor=False) as host:
            client, supervisor = host.observer, host.supervisor
            handle = await client.open("/scheme/ability/main")
            await drop_endpoint(host, handle)
            original = host.control.wait_domain
            entered, released = asyncio.Event(), asyncio.Event()

            async def gated(*args, **kwargs):
                result = await original(*args, **kwargs)
                entered.set()
                await released.wait()
                return result

            host.control.wait_domain = gated
            recovering = asyncio.create_task(supervisor.reconcile())
            await entered.wait()
            stopping = asyncio.create_task(supervisor.stop_service("tools"))

            async def stopped_intent():
                return supervisor.view("tools")["desired"] == "stopped"

            await until(stopped_intent)
            released.set()
            await asyncio.gather(recovering, stopping)
            host.control.wait_domain = original
            state = supervisor.view("tools")
            assert (
                state["phase"] == "Stopped"
                and state["kernel"]["reaped"]
                and state["restart_count"] == 0
            )
            instances = [
                x
                for x in (await host.control.process_snapshot())["domains"]
                if x["service_key"] == "tools"
            ]
            assert len(instances) == 1 and instances[0]["reaped"]
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_repeated_endpoint_loss_uses_existing_restart_budget():
    async def scenario():
        async with open_supervised_chain() as host:
            supervisor, client = host.supervisor, host.observer
            for generation in range(1, 5):
                handle = await client.open("/scheme/ability/main")
                await drop_endpoint(host, handle)
                if generation < 4:
                    await supervisor.wait_for(
                        "tools",
                        lambda x: (
                            x["phase"] == "Running"
                            and x["kernel"]["domain_generation"] == generation + 1
                        ),
                    )
                else:
                    result = await supervisor.wait_for("tools", lambda x: x["phase"] == "Failed")
                    assert result["restart_count"] == 3 and result["kernel"]["reaped"]
                await client.close(handle)
            delays = [
                x["delay_ms"]
                for x in supervisor.history
                if x["event"] == "backoff" and x["service_key"] == "tools"
            ]
            assert delays == [100, 200, 400]
        assert_supervised_clean(host)

    asyncio.run(scenario())


def test_recovery_action_failure_is_visible_as_global_failure():
    async def scenario():
        with pytest.raises(RuntimeError, match="模拟回收失败"):
            async with open_supervised_chain() as host:
                original = host.control.kill

                async def failed(*args, **kwargs):
                    raise RuntimeError("模拟回收失败")

                host.control.kill = failed
                try:
                    handle = await host.observer.open("/scheme/ability/main")
                    await drop_endpoint(host, handle)
                    with pytest.raises(RuntimeError, match="模拟回收失败"):
                        await host.supervisor.wait_for("tools", lambda _: False)
                    assert host.supervisor.view("tools")["phase"] == "Failed"
                    assert host.supervisor.failure
                finally:
                    host.control.kill = original
        assert_clean(host)
        assert not host.final_report["unhandled_policy"]

    asyncio.run(scenario())
