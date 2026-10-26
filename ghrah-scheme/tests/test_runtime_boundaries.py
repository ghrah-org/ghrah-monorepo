# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""验证原型的容量、失败、嵌套和异步核定边界。"""

import asyncio
from dataclasses import replace

import pytest
from ghrah_scheme_support import (
    SPEC,
    Arguments,
    Harness,
    MemoryProvider,
    Output,
    Policy,
    make_harness,
)

from ghrah.scheme import (
    ActionSpec,
    AdmissionState,
    CancellationState,
    EffectState,
    ErrorCode,
    InvocationState,
    Principal,
    ProviderCall,
    RuntimeLimits,
    SchemeError,
    SchemeModel,
    SchemeRuntime,
    SchemeSpec,
)

PATH = "/scheme/ability/notes"


@pytest.mark.parametrize("bad", [{1: "x"}, {"value": "x", "tags": ("a",)}, {"x": float("nan")}])
async def test_non_json_input_is_rejected(scheme_harness: Harness, bad: dict) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_INPUT):
        await h.alice.submit(ref, action="read", arguments=bad)
    assert not h.a.calls


@pytest.mark.parametrize("mode", ["schema", "size", "exception"])
async def test_invalid_result_and_provider_exception_keep_effect_unknown(mode: str) -> None:
    h = make_harness(SchemeRuntime(Policy(), limits=RuntimeLimits(max_result_bytes=80)))

    async def broken(call: ProviderCall) -> SchemeModel:
        if mode == "exception":
            raise RuntimeError("private provider detail")
        if mode == "schema":
            return Arguments(value="wrong output")
        return Output(value="x" * 100, principal="Alice")

    h.a.execute = broken
    try:
        ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
        call = await h.alice.submit(ref, action="read", arguments={"value": "x"})
        result = await h.alice.observe(call, wait=1)
        assert result.state is InvocationState.FAILED
        assert result.effect is EffectState.UNKNOWN
        assert result.result is None
        assert result.error is (
            ErrorCode.PROVIDER_FAILED if mode == "exception" else ErrorCode.INVALID_RESULT
        )
        assert "private provider detail" not in repr(result)
    finally:
        assert (await h.runtime.manager.close()).complete


async def test_released_call_ref_does_not_remove_running_owner(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.a.before = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(ref, action="write", arguments={"value": "owned"}, request_key="x")
    await asyncio.wait_for(h.a.entered.wait(), 1)
    await h.alice.release(call)
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_REFERENCE):
        await h.alice.observe(call)
    assert h.runtime.manager.diagnostics()["inflight"] == 1
    h.a.before.set()
    assert (await h.runtime.manager.withdraw("alice")).complete
    assert h.a.effects == ["owned"]


async def test_release_during_admission_records_acceptance_without_dispatch(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.policy.admit_gate = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    call = await h.alice.submit(ref, action="read", arguments={"value": "x"})
    await asyncio.wait_for(h.policy.admit_entered.wait(), 1)
    await h.alice.release(ref)
    h.policy.admit_gate.set()
    result = await h.alice.observe(call, wait=1)
    assert result.admission is AdmissionState.ACCEPTED
    assert result.error is ErrorCode.STALE_REFERENCE
    assert result.effect is EffectState.NOT_DISPATCHED
    assert not h.a.calls


async def test_dedup_expiration_and_capacity_never_repeat_effects() -> None:
    policy = Policy()
    h = make_harness(
        SchemeRuntime(policy, limits=RuntimeLimits(max_retained_calls=1, max_request_keys=2)),
        policy,
    )
    try:
        ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
        for key in ("1", "2"):
            call = await h.alice.submit(
                ref, action="write", arguments={"value": key}, request_key=key
            )
            assert (await h.alice.observe(call, wait=1)).state is InvocationState.COMPLETED
        with pytest.raises(SchemeError, match=ErrorCode.REQUEST_EXPIRED):
            await h.alice.submit(ref, action="write", arguments={"value": "1"}, request_key="1")
        with pytest.raises(SchemeError, match=ErrorCode.REQUEST_KEY_LIMIT):
            await h.alice.submit(ref, action="write", arguments={"value": "3"}, request_key="3")
        assert h.a.effects == ["1", "2"]
    finally:
        assert (await h.runtime.manager.close()).complete


async def test_dedup_reconnect_same_identity_can_observe_original(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    original = await h.alice.submit(ref, action="write", arguments={"value": "x"}, request_key="k")
    result = await h.alice.observe(original, wait=1)
    await h.alice.close()
    reconnected = h.runtime.manager.create_session(Principal("Alice", "a"))
    new = await reconnected.acquire(PATH, actions=frozenset({"write"}))
    retry = await reconnected.submit(new, action="write", arguments={"value": "x"}, request_key="k")
    assert (await reconnected.observe(retry)).invocation_id == result.invocation_id
    assert h.a.effects == ["x"]


async def test_noncooperative_authority_remains_owned_after_timeout() -> None:
    policy = Policy()
    policy.authorize_gate = asyncio.Event()
    policy.stubborn = True
    runtime = SchemeRuntime(
        policy, limits=RuntimeLimits(default_timeout=0.02, max_authorizations=1)
    )
    h = make_harness(runtime, policy)
    try:
        with pytest.raises(SchemeError, match=ErrorCode.AUTHORITY_UNAVAILABLE):
            await h.alice.acquire(PATH, actions=frozenset({"read"}))
        await asyncio.wait_for(policy.cancel_seen.wait(), 1)
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            await h.bob.acquire(PATH, actions=frozenset({"read"}))
        report = await runtime.manager.close(timeout=0)
        assert not report.complete and report.pending_controls
        policy.authorize_gate.set()
        await asyncio.sleep(0)
        assert (await runtime.manager.close(timeout=0.02)).complete
    finally:
        policy.authorize_gate.set()
        await runtime.manager.close(timeout=0.02)


async def test_cancel_request_followed_by_valid_completion_is_not_operation_error(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.a.after = asyncio.Event()
    h.a.stubborn = True
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(ref, action="write", arguments={"value": "x"}, request_key="k")
    await asyncio.wait_for(h.a.effect_written.wait(), 1)
    await h.alice.cancel(call)
    await asyncio.wait_for(h.a.cancel_seen.wait(), 1)
    h.a.after.set()
    result = await h.alice.observe(call, wait=1)
    assert result.state is InvocationState.COMPLETED
    assert result.cancellation is CancellationState.REQUESTED
    assert result.cancel_reason is ErrorCode.CANCELLED
    assert result.error is None and result.effect is EffectState.REPORTED


async def test_metadata_capacity_and_closed_session_reclamation() -> None:
    runtime = SchemeRuntime(
        Policy(),
        limits=RuntimeLimits(max_sessions=1, max_providers=1, max_bindings=1, max_contracts=1),
    )
    try:
        runtime.manager.publish("alice", SPEC, MemoryProvider())
        runtime.manager.bind("a", "notes", "alice", contract_version="1")
        first = runtime.manager.create_session(Principal("Alice", "a"))
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            runtime.manager.create_session(Principal("Alice", "a"))
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            runtime.manager.publish("extra", SPEC, MemoryProvider())
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            runtime.manager.bind("a", "other", "alice", contract_version="1")
        await first.close()
        second = runtime.manager.create_session(Principal("Alice", "a"))
        assert runtime.manager.diagnostics()["sessions"] == 1
        runtime.manager.unbind("a", "ability", "notes")
        runtime.manager.bind("a", "notes", "alice", contract_version="1")
        await second.acquire(PATH, actions=frozenset({"read"}))
        await runtime.manager.withdraw("alice")
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            runtime.manager.publish("alice", replace(SPEC, version="2"), MemoryProvider())
    finally:
        assert (await runtime.manager.close()).complete


async def test_same_contract_version_cannot_change_action_schema(scheme_harness: Harness) -> None:
    h = scheme_harness
    changed = SchemeSpec("ability", "1", (ActionSpec("read", Arguments, Arguments),))
    with pytest.raises(SchemeError, match=ErrorCode.CONTRACT_MISMATCH):
        h.runtime.manager.publish("different", changed, MemoryProvider())


async def test_reference_quotas_and_input_bytes() -> None:
    policy = Policy()
    h = make_harness(
        SchemeRuntime(
            policy,
            limits=RuntimeLimits(max_resource_refs=1, max_invocation_refs=1, max_input_bytes=64),
        ),
        policy,
    )
    try:
        ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
        with pytest.raises(SchemeError, match=ErrorCode.REFERENCE_LIMIT):
            await h.alice.acquire(PATH, actions=frozenset({"read"}))
        with pytest.raises(SchemeError, match=ErrorCode.INVALID_INPUT):
            await h.alice.submit(ref, action="read", arguments={"value": "x" * 100})
        call = await h.alice.submit(ref, action="read", arguments={"value": "x"})
        await h.alice.observe(call, wait=1)
        with pytest.raises(SchemeError, match=ErrorCode.REFERENCE_LIMIT):
            await h.alice.submit(ref, action="read", arguments={"value": "y"})
        await h.alice.release(call)
        call = await h.alice.submit(ref, action="read", arguments={"value": "y"})
        assert (await h.alice.observe(call, wait=1)).state is InvocationState.COMPLETED
    finally:
        assert (await h.runtime.manager.close()).complete


async def test_cross_provider_nested_call_keeps_injected_identity(scheme_harness: Harness) -> None:
    h = scheme_harness
    a = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    b = await h.bob.acquire(PATH, actions=frozenset({"read"}))

    async def outer(call: ProviderCall) -> SchemeModel:
        child = await h.bob.submit(b, action="read", arguments={"value": "child"})
        assert (await h.bob.observe(child, wait=1)).result["principal"] == "Bob"
        return Output(value="parent", principal=call.principal.name)

    h.a.execute = outer
    call = await h.alice.submit(a, action="read", arguments={"value": "parent"})
    assert (await h.alice.observe(call, wait=1)).result["principal"] == "Alice"


async def test_same_provider_reentry_including_dedup_is_rejected(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))

    async def outer(call: ProviderCall) -> SchemeModel:
        for key in (None, "parent"):
            with pytest.raises(SchemeError, match=ErrorCode.REENTRANT_CALL):
                await h.alice.submit(ref, action="read", arguments={"value": "x"}, request_key=key)
        return Output(value="finished", principal="Alice")

    h.a.execute = outer
    call = await h.alice.submit(ref, action="read", arguments={"value": "x"}, request_key="parent")
    assert (await h.alice.observe(call, wait=1)).state is InvocationState.COMPLETED


async def test_parallel_cross_provider_wait_cycle_is_rejected(scheme_harness: Harness) -> None:
    h = scheme_harness
    a = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    b = await h.bob.acquire(PATH, actions=frozenset({"read"}))
    entered_a, entered_b = asyncio.Event(), asyncio.Event()
    rejected = []

    async def outer_a(call: ProviderCall) -> SchemeModel:
        entered_a.set()
        await entered_b.wait()
        with pytest.raises(SchemeError) as caught:
            await h.bob.submit(b, action="read", arguments={"value": "nested"})
        rejected.append(caught.value.code)
        return Output(value="a", principal="Alice")

    async def outer_b(call: ProviderCall) -> SchemeModel:
        entered_b.set()
        await entered_a.wait()
        with pytest.raises(SchemeError) as caught:
            await h.alice.submit(a, action="read", arguments={"value": "nested"})
        rejected.append(caught.value.code)
        return Output(value="b", principal="Bob")

    h.a.execute, h.b.execute = outer_a, outer_b
    first = await h.alice.submit(a, action="read", arguments={"value": "outer"})
    second = await h.bob.submit(b, action="read", arguments={"value": "outer"})
    results = await asyncio.gather(h.alice.observe(first, wait=1), h.bob.observe(second, wait=1))
    assert all(result.state is InvocationState.COMPLETED for result in results)
    assert rejected == [ErrorCode.NESTED_BUSY, ErrorCode.NESTED_BUSY]


def test_runtime_rejects_different_event_loop() -> None:
    async def create() -> SchemeRuntime:
        runtime = SchemeRuntime()
        await runtime.manager.close()
        return runtime

    runtime = asyncio.run(create())

    async def other_loop() -> None:
        with pytest.raises(SchemeError, match=ErrorCode.WRONG_LOOP):
            runtime.manager.diagnostics()

    asyncio.run(other_loop())


async def test_cancelled_parent_cannot_start_more_nested_work(scheme_harness: Harness) -> None:
    h = scheme_harness
    a = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    b = await h.bob.acquire(PATH, actions=frozenset({"read"}))
    entered = asyncio.Event()

    async def outer(call: ProviderCall) -> SchemeModel:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            with pytest.raises(SchemeError, match=ErrorCode.CANCELLED):
                await h.bob.submit(b, action="read", arguments={"value": "too late"})
        return Output(value="stopped nesting", principal="Alice")

    h.a.execute = outer
    call = await h.alice.submit(a, action="read", arguments={"value": "parent"})
    await asyncio.wait_for(entered.wait(), 1)
    await h.alice.cancel(call)
    result = await h.alice.observe(call, wait=1)
    assert result.state is InvocationState.COMPLETED and not h.b.calls


async def test_parent_cancellation_propagates_to_nested_call(scheme_harness: Harness) -> None:
    h = scheme_harness
    a = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    b = await h.bob.acquire(PATH, actions=frozenset({"read"}))
    h.b.before = asyncio.Event()
    children = []

    async def outer(call: ProviderCall) -> SchemeModel:
        child = await h.bob.submit(b, action="read", arguments={"value": "child"})
        children.append(child)
        await h.bob.observe(child, wait=1)
        return Output(value="parent", principal="Alice")

    h.a.execute = outer
    call = await h.alice.submit(a, action="read", arguments={"value": "parent"})
    await asyncio.wait_for(h.b.entered.wait(), 1)
    await h.alice.cancel(call)
    parent, child = await asyncio.gather(
        h.alice.observe(call, wait=1), h.bob.observe(children[0], wait=1)
    )
    assert parent.cancellation is CancellationState.CONFIRMED
    assert child.cancellation is CancellationState.CONFIRMED
    assert not parent.pending and not child.pending
