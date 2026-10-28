# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""准备、共享资源、故障代次、收尾失败和非合作任务的 owner 验证。"""

import asyncio

import pytest
from ghrah_scheme_support import ALICE, SPEC, MemoryProvider, Policy

from ghrah.scheme import (
    ErrorCode,
    OwnerState,
    ProviderState,
    RuntimeLimits,
    SchemeError,
    SchemeRuntime,
)


class Lifecycle:
    def __init__(self) -> None:
        self.prepares = 0
        self.closes = 0
        self.prepare_gate: asyncio.Event | None = None
        self.close_gate: asyncio.Event | None = None
        self.prepare_entered = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.stubborn_prepare = False
        self.stubborn_close = False
        self.fail_prepare = False
        self.fail_close = False

    async def prepare(self) -> None:
        self.prepares += 1
        self.prepare_entered.set()
        if self.prepare_gate is not None:
            try:
                await self.prepare_gate.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                if not self.stubborn_prepare:
                    raise
                await self.prepare_gate.wait()
        if self.fail_prepare:
            raise RuntimeError("private prepare failure")

    async def aclose(self) -> None:
        self.closes += 1
        self.close_entered.set()
        if self.close_gate is not None:
            try:
                await self.close_gate.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                if not self.stubborn_close:
                    raise
                await self.close_gate.wait()
        if self.fail_close:
            raise RuntimeError("private close failure")


def mount(runtime, lifecycle):
    owner = runtime.manager.create_owner(lifecycle)
    incarnation = runtime.manager.publish("alice", SPEC, MemoryProvider(), owner=owner)
    runtime.manager.bind(
        ALICE.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    return owner, incarnation, runtime.manager.create_session(ALICE)


async def test_preparing_is_not_ready_and_owned_close_runs_once() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.prepare_gate = asyncio.Event()
    owner, _, session = mount(runtime, life)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        assert runtime.manager.diagnostics()["providers"]["alice"] == "preparing"
        with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_UNAVAILABLE):
            await session.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
        life.prepare_gate.set()
        await asyncio.sleep(0)
        await session.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
        assert (await runtime.manager.withdraw("alice")).complete
        assert life.prepares == life.closes == 1
        assert (await runtime.manager.close()).complete
        assert life.closes == 1
        assert runtime.manager.diagnostics()["owners"][owner.token] == "closed"
    finally:
        life.prepare_gate.set()
        await runtime.manager.close()


async def test_shared_owner_waits_for_last_export() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    owner, _, _ = mount(runtime, life)
    runtime.manager.publish("second", SPEC, MemoryProvider(), owner=owner)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        assert (await runtime.manager.withdraw("alice")).complete
        assert life.closes == 0
        assert runtime.manager.diagnostics()["providers"]["second"] == "ready"
        assert (await runtime.manager.withdraw("second")).complete
        assert life.prepares == life.closes == 1
    finally:
        await runtime.manager.close()


async def test_prepare_failure_still_releases_partial_resources() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.fail_prepare = True
    _, _, session = mount(runtime, life)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        assert runtime.manager.diagnostics()["providers"]["alice"] == "failed"
        with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_UNAVAILABLE):
            await session.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
        assert (await runtime.manager.withdraw("alice")).complete
        assert life.closes == 1
    finally:
        await runtime.manager.close()


async def test_failed_close_is_residual_and_only_explicit_retry_releases() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.fail_close = True
    owner, _, _ = mount(runtime, life)
    await asyncio.wait_for(life.prepare_entered.wait(), 1)
    report = await runtime.manager.withdraw("alice")
    assert not report.complete
    assert report.pending_owners[0].state is OwnerState.CLOSE_FAILED
    assert report.pending_owners[0].error is ErrorCode.PROVIDER_FAILED
    assert "private" not in repr(report)
    with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_EXISTS):
        runtime.manager.publish("alice", SPEC, MemoryProvider())
    assert not (await runtime.manager.close()).complete
    assert life.closes == 1
    life.fail_close = False
    assert (await runtime.manager.retry_close(owner)).complete
    assert life.closes == 2
    assert (await runtime.manager.close()).complete


async def test_noncooperative_close_remains_owned_and_cannot_be_retried_concurrently() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.close_gate = asyncio.Event()
    life.stubborn_close = True
    owner, _, _ = mount(runtime, life)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        report = await runtime.manager.withdraw("alice", timeout=0.01)
        assert not report.complete and report.pending_owners[0].pending
        await asyncio.wait_for(life.cancel_seen.wait(), 1)
        with pytest.raises(SchemeError):
            await runtime.manager.retry_close(owner)
        assert not (await runtime.manager.close(timeout=0)).complete
        assert life.closes == 1
        life.close_gate.set()
        assert (await runtime.manager.close()).complete
    finally:
        life.close_gate.set()
        await runtime.manager.close()


async def test_close_during_noncooperative_prepare_never_publishes_ready() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.prepare_gate = asyncio.Event()
    life.stubborn_prepare = True
    _, _, _ = mount(runtime, life)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        report = await runtime.manager.withdraw("alice", timeout=0.01)
        assert report.pending_owners and life.closes == 0
        await asyncio.wait_for(life.cancel_seen.wait(), 1)
        life.prepare_gate.set()
        assert (await runtime.manager.close()).complete
        assert runtime.manager.diagnostics()["providers"]["alice"] == "withdrawn"
        assert life.prepares == life.closes == 1
    finally:
        life.prepare_gate.set()
        await runtime.manager.close()


async def test_owner_health_gates_shared_exports_and_ignores_old_incarnation() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    owner, incarnation, session = mount(runtime, life)
    runtime.manager.publish("second", SPEC, MemoryProvider(), owner=owner)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        runtime.manager.report_health("alice", incarnation, ProviderState.UNAVAILABLE)
        assert runtime.manager.diagnostics()["providers"]["second"] == "unavailable"
        runtime.manager.report_health("alice", incarnation, ProviderState.READY)
        await session.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
        runtime.manager.report_health("alice", incarnation, ProviderState.FAILED)
        with pytest.raises(SchemeError):
            runtime.manager.report_health("alice", incarnation, ProviderState.READY)
        assert (await runtime.manager.withdraw("alice")).complete
        assert (await runtime.manager.withdraw("second")).complete
        runtime.manager.publish("alice", SPEC, MemoryProvider())
        with pytest.raises(SchemeError, match=ErrorCode.STALE_PROVIDER):
            runtime.manager.report_health("alice", incarnation, ProviderState.FAILED)
        assert runtime.manager.diagnostics()["providers"]["alice"] == "ready"
    finally:
        await runtime.manager.close()


async def test_owner_capacity_duplicate_identity_and_preparation_deadline() -> None:
    runtime = SchemeRuntime(
        Policy(), limits=RuntimeLimits(max_provider_owners=1, default_timeout=0.02)
    )
    life = Lifecycle()
    life.prepare_gate = asyncio.Event()
    _, _, _ = mount(runtime, life)
    try:
        with pytest.raises(SchemeError):
            runtime.manager.create_owner(life)
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            runtime.manager.create_owner(Lifecycle())
        await asyncio.sleep(0.03)
        assert runtime.manager.diagnostics()["providers"]["alice"] == "failed"
        assert (await runtime.manager.withdraw("alice", timeout=0.02)).complete
    finally:
        life.prepare_gate.set()
        await runtime.manager.close(timeout=0.02)


async def test_cancel_close_before_first_schedule_is_retryable() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    owner, _, _ = mount(runtime, life)
    await asyncio.wait_for(life.prepare_entered.wait(), 1)
    assert not (await runtime.manager.withdraw("alice", timeout=0)).complete
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert (await runtime.manager.retry_close(owner)).complete
    assert (await runtime.manager.close()).complete


async def test_last_export_closes_only_after_running_call_settles() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    provider = MemoryProvider()
    provider.before = asyncio.Event()
    owner = runtime.manager.create_owner(life)
    runtime.manager.publish("alice", SPEC, provider, owner=owner)
    runtime.manager.bind(
        ALICE.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    session = runtime.manager.create_session(ALICE)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        ref = await session.acquire("/scheme/ability/notes", actions=frozenset({"write"}))
        await session.submit(ref, action="write", arguments={"value": "effect"}, request_key="k")
        await asyncio.wait_for(provider.entered.wait(), 1)
        stopping = asyncio.create_task(runtime.manager.withdraw("alice", timeout=0.2))
        await asyncio.sleep(0)
        assert life.closes == 0
        provider.before.set()
        assert (await stopping).complete
        assert provider.effects == ["effect"] and life.closes == 1
    finally:
        provider.before.set()
        await runtime.manager.close()


async def test_noncooperative_call_keeps_resource_owner_until_actual_finish() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    provider = MemoryProvider()
    provider.after = asyncio.Event()
    provider.stubborn = True
    owner = runtime.manager.create_owner(life)
    runtime.manager.publish("alice", SPEC, provider, owner=owner)
    runtime.manager.bind(
        ALICE.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    session = runtime.manager.create_session(ALICE)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        ref = await session.acquire("/scheme/ability/notes", actions=frozenset({"write"}))
        await session.submit(ref, action="write", arguments={"value": "effect"}, request_key="k")
        await asyncio.wait_for(provider.effect_written.wait(), 1)
        report = await runtime.manager.withdraw("alice", timeout=0.01)
        assert report.pending and report.pending_owners and life.closes == 0
        assert report.pending_owners[0].state is OwnerState.DRAINING
        await asyncio.wait_for(provider.cancel_seen.wait(), 1)
        with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_EXISTS):
            runtime.manager.publish("alice", SPEC, MemoryProvider())
        provider.after.set()
        assert (await runtime.manager.close()).complete
        assert life.closes == 1 and provider.effects == ["effect"]
    finally:
        provider.after.set()
        await runtime.manager.close()


async def test_late_prepare_success_after_deadline_never_revives_ready() -> None:
    runtime = SchemeRuntime(Policy(), limits=RuntimeLimits(default_timeout=0.02))
    life = Lifecycle()
    life.prepare_gate = asyncio.Event()
    life.stubborn_prepare = True
    owner, incarnation, _ = mount(runtime, life)
    try:
        with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_UNAVAILABLE):
            runtime.manager.report_health("alice", incarnation, ProviderState.READY)
        await asyncio.wait_for(life.cancel_seen.wait(), 1)
        life.prepare_gate.set()
        await asyncio.sleep(0)
        assert runtime.manager.diagnostics()["providers"]["alice"] == "failed"
        assert (await runtime.manager.withdraw("alice", timeout=0.02)).complete
        assert life.closes == 1
        assert runtime.manager.diagnostics()["owners"][owner.token] == "closed"
    finally:
        life.prepare_gate.set()
        await runtime.manager.close(timeout=0.02)


async def test_cancelling_close_waiter_does_not_lose_owner_task() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.close_gate = asyncio.Event()
    owner, _, _ = mount(runtime, life)
    try:
        await asyncio.wait_for(life.prepare_entered.wait(), 1)
        waiter = asyncio.create_task(runtime.manager.withdraw("alice"))
        await asyncio.wait_for(life.close_entered.wait(), 1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert runtime.manager.diagnostics()["owners"][owner.token] == "closing"
        life.close_gate.set()
        assert (await runtime.manager.close()).complete
        assert life.closes == 1
    finally:
        life.close_gate.set()
        await runtime.manager.close()


async def test_same_lifecycle_object_cannot_have_two_owners() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    runtime.manager.create_owner(life)
    with pytest.raises(SchemeError) as caught:
        runtime.manager.create_owner(life)
    assert caught.value.code is ErrorCode.PROVIDER_EXISTS
    assert (await runtime.manager.close()).complete
    assert life.closes == 1


async def test_borrowed_provider_is_not_implicitly_closed() -> None:
    class Borrowed(Lifecycle):
        async def execute(self, call):
            raise RuntimeError("unused")

    runtime = SchemeRuntime(Policy())
    provider = Borrowed()
    runtime.manager.publish("alice", SPEC, provider)
    assert (await runtime.manager.close()).complete
    assert provider.prepares == provider.closes == 0


async def test_preparation_observation_timeout_does_not_cancel_owner() -> None:
    runtime = SchemeRuntime(Policy())
    life = Lifecycle()
    life.prepare_gate = asyncio.Event()
    owner, _, _ = mount(runtime, life)
    try:
        status = await runtime.manager.wait_prepared(owner, timeout=0.001)
        assert status.state is OwnerState.PREPARING and status.pending
        assert not life.cancel_seen.is_set()
        life.prepare_gate.set()
        assert (await runtime.manager.wait_prepared(owner)).state is OwnerState.READY
        assert (await runtime.manager.withdraw("alice")).complete
        assert runtime.manager.owner_status(owner).state is OwnerState.CLOSED
    finally:
        life.prepare_gate.set()
        await runtime.manager.close()
