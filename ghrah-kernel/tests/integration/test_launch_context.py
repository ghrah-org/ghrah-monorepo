# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""受信启动、上下文与效果在真实跨进程调用中保持一致。"""

import asyncio
import time
from dataclasses import replace

import pytest
from _runtime_host import SVC, U1, identities, open_runtime

from ghrah_kernel.client import RemoteError
from ghrah_kernel.integration.assembly import host_configuration
from ghrah_kernel.kernel.runtime import Kernel
from ghrah_kernel.kernel.tables import KernelFault
from ghrah_kernel.launch import LaunchCatalog, LaunchRecord


def test_empty_catalog_and_request_command_overrides_do_not_spawn_processes():
    async def scenario():
        kernel = Kernel()
        try:
            with pytest.raises(KernelFault) as denied:
                await kernel.processes.spawn("any", "any", "once")
            assert denied.value.code == "ACCESS_DENIED" and not kernel.processes.domains
        finally:
            await kernel.aclose()
        async with open_runtime() as host:
            before = await host.control.process_snapshot()
            for key, kind, variant in (
                ("os", "service-probe", "normal"),
                ("one", "wrong", "normal"),
                ("one", "service-probe", "undeclared"),
            ):
                with pytest.raises(RemoteError) as denied:
                    await host.control.spawn(
                        "forged", kind, entry_key=key, fixture=variant, spawn_key="once"
                    )
                assert denied.value.code == "ACCESS_DENIED"
            with pytest.raises(RemoteError) as override:
                await host.control.call(
                    host.control.session["proc_handle"],
                    "spawn",
                    {
                        "service_key": "forged",
                        "kind": "service-probe",
                        "spawn_key": "once",
                        "entry_key": "one",
                        "module": "os",
                        "environment": {"EVIL": "yes"},
                    },
                )
            assert override.value.code == "INVALID_ARGUMENT"
            assert len((await host.control.process_snapshot())["domains"]) == len(before["domains"])

    asyncio.run(scenario())


def test_invoke_context_contains_trusted_session_exact_target_and_deadline():
    async def scenario():
        async with open_runtime() as host:
            client = host.observer
            handle = await client.open("/service")
            deadline = time.monotonic() + 5
            invocation = await client.invoke(
                handle, "context", {"principal": "forged"}, deadline=deadline
            )
            result = await client.wait(invocation)
            value = result.value
            assert (
                value["principal"] == U1.ref and value["session_id"] == client.session["session_id"]
            )
            assert value["scheme_id"] == host.registrations["one"]["schemes"]["probe"]
            assert value["provider_object_id"].startswith("opened:")
            assert value["invocation_id"] == invocation and value["deadline"] == deadline
            assert value["caller_domain_id"] is None
            assert value["launch_environment"] == "trusted-one"
            assert result.effect_evidence == "PROVIDER_REPORTED_NO_EFFECT"
            await client.close(invocation)

    asyncio.run(scenario())


def test_provider_reports_effect_and_cancellation_remains_unknown():
    async def scenario():
        async with open_runtime() as host:
            client = host.observer
            handle = await client.open("/service")
            for action, effect in (("effect", "PROVIDER_REPORTED_EFFECT"), ("fail", "UNKNOWN")):
                invocation = await client.invoke(handle, action, {})
                result = await client.wait(invocation)
                assert result.effect_evidence == effect
                if action == "fail":
                    assert result.error.code == "PROVIDER_ERROR"
                await client.close(invocation)
            oversized = await client.invoke(handle, "context", {"large_reply": True})
            result = await client.wait(oversized)
            assert result.error.code == "MESSAGE_TOO_LARGE"
            assert result.effect_evidence == "PROVIDER_REPORTED_NO_EFFECT"
            await client.close(oversized)
            invocation = await client.invoke(handle, "delay", {"seconds": 2}, timeout_ms=100)
            result = await client.wait(invocation)
            assert result.settlement_state == "TIMED_OUT"
            assert result.effect_evidence == "UNKNOWN" and result.dispatch_fact == "DISPATCHED"
            await client.close(invocation)
            assert (await client.call(handle, "context", {}))["principal"] == U1.ref

    asyncio.run(scenario())


def test_explicit_cancel_preserves_dispatch_uncertainty_and_service_usability():
    async def scenario():
        async with open_runtime() as host:
            client = host.observer
            handle = await client.open("/service")
            invocation = await client.invoke(handle, "delay", {"seconds": 5})
            await client.cancel(invocation)
            result = await client.wait(invocation)
            assert result.settlement_state == "CANCELLED"
            assert result.dispatch_fact == "DISPATCHED" and result.effect_evidence == "UNKNOWN"
            await client.close(invocation)
            assert (await client.call(handle, "context", {}))["principal"] == U1.ref
            await client.close(handle)

    asyncio.run(scenario())


def test_restart_preserves_subject_but_invalidates_old_instance_handles():
    async def scenario():
        async with open_runtime() as host:
            old_registration = host.registrations["one"]
            old = await host.observer.open("/service")
            before = await host.observer.call(old, "context", {})
            await host.control.kill(old_registration["domain_id"])
            new_registration = await host.start("one")
            await host.mount("s1", "/service", "one", "n1")
            with pytest.raises(RemoteError) as stale:
                await host.observer.call(old, "context", {})
            assert stale.value.code == "STALE_REFERENCE"
            new = await host.observer.open("/service")
            after = await host.observer.call(new, "context", {})
            assert before["service_principal"] == after["service_principal"] == SVC.ref
            assert before["service_domain"] != after["service_domain"]
            assert (
                new_registration["domain_generation"] == old_registration["domain_generation"] + 1
            )

    asyncio.run(scenario())


def test_launch_records_and_host_configuration_reject_ambiguous_or_injected_entries():
    record = LaunchRecord(
        "names", "namespace", "ghrah_kernel.integration.entry", identities().bind(SVC.ref, None)
    )
    with pytest.raises(ValueError, match="重复"):
        LaunchCatalog((record, record))
    with pytest.raises(ValueError, match="覆盖"):
        replace(record, arguments=("--provider-fd=999",))
    with pytest.raises(ValueError, match="模块"):
        replace(record, module="os;echo")
    with pytest.raises(ValueError, match="三个"):
        host_configuration({"launches": [], "callers": {}})
