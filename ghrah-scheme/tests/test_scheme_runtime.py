# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""吸收真实实验的引用、撤销、换代、过载与关闭场景。"""

import asyncio
from dataclasses import replace

import pytest
from ghrah_scheme_support import SPEC, Harness, MemoryProvider, Policy

from ghrah.scheme import (
    AdmissionState,
    CancelDisposition,
    CancellationState,
    EffectState,
    ErrorCode,
    InvocationState,
    Principal,
    RuntimeLimits,
    SchemeError,
    SchemeRuntime,
)

PATH = "/scheme/ability/notes"


async def test_local_bindings_and_typed_input(scheme_harness: Harness) -> None:
    h = scheme_harness
    for session, name, provider in [(h.alice, "Alice", h.a), (h.bob, "Bob", h.b)]:
        ref = await session.acquire(PATH, actions=frozenset({"write"}))
        call = await session.submit(
            ref, action="write", arguments={"value": name}, request_key="one"
        )
        result = await session.observe(call, wait=1)
        assert result.state is InvocationState.COMPLETED
        assert result.admission is AdmissionState.ACCEPTED
        assert result.effect is EffectState.REPORTED
        assert result.result == {"value": name, "principal": name}
        assert provider.effects == [name]
        assert provider.calls[0].principal.name == name


async def test_bind_does_not_authorize_and_default_is_deny(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.runtime.manager.bind("b", "notes", "alice", contract_version="1")
    with pytest.raises(SchemeError, match=ErrorCode.ACCESS_DENIED):
        await h.bob.acquire(PATH, actions=frozenset({"read"}))
    runtime = SchemeRuntime()
    runtime.manager.publish("p", SPEC, MemoryProvider())
    runtime.manager.bind("n", "notes", "p", contract_version="1")
    session = runtime.manager.create_session(Principal("a", "n"))
    with pytest.raises(SchemeError, match=ErrorCode.ACCESS_DENIED):
        await session.acquire(PATH, actions=frozenset({"read"}))
    assert (await runtime.manager.close()).complete


@pytest.mark.parametrize(
    "path",
    [
        "ability:notes",
        "/scheme/ability",
        "/scheme/ability//notes",
        "/scheme/ability/../notes",
        "/scheme/ability/notes/",
        "/scheme/ability/notes/%2e",
        "/scheme/ability/notes/a?b",
        "/scheme/ability/notes/a\\b",
    ],
)
async def test_invalid_paths_are_rejected(scheme_harness: Harness, path: str) -> None:
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_PATH):
        await scheme_harness.alice.acquire(path, actions=frozenset({"read"}))


async def test_cross_session_and_forged_references(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    for invalid in (replace(ref, boot="other"), replace(ref, token="forged")):
        with pytest.raises(SchemeError):
            await h.alice.submit(invalid, action="read", arguments={"value": "x"})
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_REFERENCE):
        await h.bob.submit(ref, action="read", arguments={"value": "x"})
    call = await h.alice.submit(ref, action="read", arguments={"value": "ok"})
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_REFERENCE):
        await h.bob.cancel(call)
    assert (await h.alice.observe(call, wait=1)).result is not None


async def test_slot_reuse_and_late_release_cannot_release_new_ref(scheme_harness: Harness) -> None:
    session = scheme_harness.alice
    old = await session.acquire(PATH, actions=frozenset({"read"}))
    await session.release(old)
    new = await session.acquire(PATH, actions=frozenset({"read"}))
    assert old.slot == new.slot and old.generation != new.generation
    with pytest.raises(SchemeError, match=ErrorCode.STALE_REFERENCE):
        await session.release(old)
    result = await session.submit(new, action="read", arguments={"value": "new"})
    assert (await session.observe(result, wait=1)).result["value"] == "new"


async def test_revoke_after_acquire_blocks_admission(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    h.policy.revoke("Alice", "alice")
    call = await h.alice.submit(ref, action="write", arguments={"value": "no"}, request_key="r")
    state = await h.alice.observe(call, wait=1)
    assert state.state is InvocationState.REJECTED
    assert state.admission is AdmissionState.REJECTED
    assert state.effect is EffectState.NOT_DISPATCHED
    assert not h.a.effects
    await h.alice.release(ref)


async def test_queued_revoke_preserves_already_admitted_call(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.a.before = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    first = await h.alice.submit(ref, action="write", arguments={"value": "first"}, request_key="1")
    await asyncio.wait_for(h.a.entered.wait(), 1)
    second = await h.alice.submit(
        ref, action="write", arguments={"value": "second"}, request_key="2"
    )
    h.policy.revoke("Alice", "alice")
    h.a.before.set()
    first_state = await h.alice.observe(first, wait=1)
    second_state = await h.alice.observe(second, wait=1)
    assert first_state.state is InvocationState.COMPLETED
    assert first_state.result_redacted
    assert second_state.error is ErrorCode.ACCESS_DENIED
    assert second_state.effect is EffectState.NOT_DISPATCHED
    assert h.a.effects == ["first"]


async def test_rebind_and_unbind_invalidate_old_reference(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    h.runtime.manager.bind("a", "notes", "bob", contract_version="1")
    with pytest.raises(SchemeError, match=ErrorCode.STALE_BINDING):
        await h.alice.submit(ref, action="read", arguments={"value": "x"})
    await h.alice.release(ref)
    h.runtime.manager.unbind("a", "ability", "notes")
    with pytest.raises(SchemeError, match=ErrorCode.NOT_FOUND):
        await h.alice.acquire(PATH, actions=frozenset({"read"}))


async def test_restart_never_retargets_old_reference(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    assert (await h.runtime.manager.withdraw("alice")).complete
    replacement = MemoryProvider()
    h.runtime.manager.publish("alice", SPEC, replacement)
    with pytest.raises(SchemeError, match=ErrorCode.STALE_PROVIDER):
        await h.alice.submit(ref, action="read", arguments={"value": "x"})
    new = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    call = await h.alice.submit(new, action="read", arguments={"value": "new"})
    assert (await h.alice.observe(call, wait=1)).state is InvocationState.COMPLETED
    assert len(replacement.calls) == 1


async def test_read_only_reference_cannot_invoke_write(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    with pytest.raises(SchemeError, match=ErrorCode.ACCESS_DENIED):
        await h.alice.submit(ref, action="write", arguments={"value": "x"}, request_key="w")


@pytest.mark.parametrize(
    "arguments",
    [{"value": True}, {"value": "x", "principal": "Admin"}, {"value": "x", "agent_id": "Bob"}],
)
async def test_bad_inputs_do_not_reach_provider(scheme_harness: Harness, arguments: dict) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_INPUT):
        await h.alice.submit(ref, action="read", arguments=arguments)
    assert not h.a.calls


async def test_release_resource_after_admission_does_not_cancel_call(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.a.before = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(ref, action="write", arguments={"value": "kept"}, request_key="k")
    await asyncio.wait_for(h.a.entered.wait(), 1)
    await h.alice.release(ref)
    h.a.before.set()
    assert (await h.alice.observe(call, wait=1)).state is InvocationState.COMPLETED
    assert h.a.effects == ["kept"]


async def test_release_queued_reference_blocks_unadmitted_call(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.a.before = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    first = await h.alice.submit(ref, action="read", arguments={"value": "first"})
    await asyncio.wait_for(h.a.entered.wait(), 1)
    second = await h.alice.submit(ref, action="read", arguments={"value": "second"})
    await h.alice.release(ref)
    h.a.before.set()
    assert (await h.alice.observe(first, wait=1)).state is InvocationState.COMPLETED
    state = await h.alice.observe(second, wait=1)
    assert state.error is ErrorCode.STALE_REFERENCE and state.effect is EffectState.NOT_DISPATCHED


async def test_observe_timeout_does_not_cancel_and_snapshot_isolated(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.a.before = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    arguments = {"value": "original", "tags": ["a"]}
    call = await h.alice.submit(ref, action="read", arguments=arguments)
    arguments["value"] = "changed"
    arguments["tags"].append("b")
    await asyncio.wait_for(h.a.entered.wait(), 1)
    state = await h.alice.observe(call, wait=0.001)
    assert state.pending and state.cancellation is CancellationState.NONE
    h.a.before.set()
    state = await h.alice.observe(call, wait=1)
    assert state.result["value"] == "original"
    assert h.a.calls[0].arguments.tags == ["a"]
    state.result["value"] = "tampered"
    assert (await h.alice.observe(call)).result["value"] == "original"


async def test_cancel_before_first_dispatch_is_confirmed_without_effect(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(ref, action="write", arguments={"value": "no"}, request_key="x")
    assert await h.alice.cancel(call) is CancelDisposition.REQUESTED
    state = await h.alice.observe(call, wait=1)
    assert state.state is InvocationState.CANCELLED
    assert state.effect is EffectState.NOT_DISPATCHED
    assert not h.a.effects


async def test_cancel_after_effect_does_not_report_rollback(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.a.after = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(
        ref, action="write", arguments={"value": "written"}, request_key="w"
    )
    await asyncio.wait_for(h.a.effect_written.wait(), 1)
    await h.alice.cancel(call)
    state = await h.alice.observe(call, wait=1)
    assert state.cancellation is CancellationState.CONFIRMED
    assert state.effect is EffectState.UNKNOWN
    assert h.a.effects == ["written"]


async def test_noncooperative_task_retains_owner_and_blocks_replacement(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.a.after, h.a.stubborn = asyncio.Event(), True
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(
        ref, action="write", arguments={"value": "written"}, request_key="w"
    )
    await asyncio.wait_for(h.a.effect_written.wait(), 1)
    report = await h.runtime.manager.withdraw("alice", timeout=0.01, cancel=True)
    assert not report.complete and report.pending[0].effect is EffectState.UNKNOWN
    assert (await h.alice.observe(call)).pending
    with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_EXISTS):
        h.runtime.manager.publish("alice", SPEC, MemoryProvider())
    h.a.after.set()
    state = await h.alice.observe(call, wait=1)
    assert state.state is InvocationState.COMPLETED
    assert state.cancellation is CancellationState.REQUESTED


async def test_shared_provider_survives_one_session_close(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.policy.grants.add(("Bob", "alice"))
    h.runtime.manager.bind("b", "notes", "alice", contract_version="1")
    ref = await h.bob.acquire(PATH, actions=frozenset({"read"}))
    assert (await h.alice.close()).complete
    call = await h.bob.submit(ref, action="read", arguments={"value": "Bob"})
    assert (await h.bob.observe(call, wait=1)).state is InvocationState.COMPLETED
    assert h.runtime.manager.diagnostics()["providers"]["alice"] == "ready"


async def test_completed_results_require_current_permission(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    call = await h.alice.submit(ref, action="read", arguments={"value": "secret"})
    assert (await h.alice.observe(call, wait=1)).result is not None
    h.policy.revoke("Alice", "alice")
    state = await h.alice.observe(call)
    assert state.result is None and state.result_redacted and not state.pending
    assert await h.alice.cancel(call) is CancelDisposition.ALREADY_FINISHED
    await h.alice.release(call)


async def test_dedup_returns_original_call_and_rejects_different_input(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    first = await h.alice.submit(
        ref, action="write", arguments={"value": "once"}, request_key="same"
    )
    retry = await h.alice.submit(
        ref, action="write", arguments={"value": "once"}, request_key="same"
    )
    assert (await h.alice.observe(first, wait=1)).invocation_id == (
        await h.alice.observe(retry)
    ).invocation_id
    assert h.a.effects == ["once"]
    with pytest.raises(SchemeError, match=ErrorCode.REQUEST_CONFLICT):
        await h.alice.submit(ref, action="write", arguments={"value": "twice"}, request_key="same")


async def test_dedup_does_not_retarget_after_rebind(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    call = await h.alice.submit(
        ref, action="write", arguments={"value": "once"}, request_key="same"
    )
    await h.alice.observe(call, wait=1)
    h.runtime.manager.bind("a", "notes", "bob", contract_version="1")
    h.policy.grants.add(("Alice", "bob"))
    new = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    with pytest.raises(SchemeError, match=ErrorCode.REQUEST_CONFLICT):
        await h.alice.submit(new, action="write", arguments={"value": "once"}, request_key="same")
    assert not h.b.effects


async def test_mutation_requires_explicit_request_key(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    with pytest.raises(SchemeError, match=ErrorCode.REQUEST_KEY_REQUIRED):
        await h.alice.submit(ref, action="write", arguments={"value": "x"})


async def test_overload_and_deadline_keep_control_responsive() -> None:
    policy = Policy()
    runtime = SchemeRuntime(policy, limits=RuntimeLimits(max_inflight=2))
    provider = MemoryProvider()
    provider.before = asyncio.Event()
    runtime.manager.publish("alice", SPEC, provider, pending_limit=1)
    runtime.manager.bind("a", "notes", "alice", contract_version="1")
    session = runtime.manager.create_session(Principal("Alice", "a"))
    ref = await session.acquire(PATH, actions=frozenset({"read"}))
    try:
        first = await session.submit(ref, action="read", arguments={"value": "a"}, timeout=0.02)
        await asyncio.wait_for(provider.entered.wait(), 1)
        second = await session.submit(ref, action="read", arguments={"value": "b"})
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            await session.submit(ref, action="read", arguments={"value": "c"})
        assert (await session.observe(first, wait=1)).error is ErrorCode.DEADLINE_EXCEEDED
        await session.cancel(second)
        assert not (await session.observe(second, wait=1)).pending
    finally:
        provider.before.set()
        assert (await runtime.manager.close()).complete


async def test_async_authorization_rechecks_binding(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.policy.authorize_gate = asyncio.Event()
    pending = asyncio.create_task(h.alice.acquire(PATH, actions=frozenset({"read"})))
    await asyncio.wait_for(h.policy.authorize_entered.wait(), 1)
    h.runtime.manager.bind("a", "notes", "bob", contract_version="1")
    h.policy.authorize_gate.set()
    with pytest.raises(SchemeError, match=ErrorCode.STALE_BINDING):
        await pending


async def test_admission_is_distinct_from_dispatch(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.policy.admit_gate = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    call = await h.alice.submit(ref, action="read", arguments={"value": "x"})
    await asyncio.wait_for(h.policy.admit_entered.wait(), 1)
    state = await h.alice.observe(call)
    assert state.admission is AdmissionState.PENDING and state.effect is EffectState.NOT_DISPATCHED
    h.runtime.manager.bind("a", "notes", "bob", contract_version="1")
    h.policy.admit_gate.set()
    state = await h.alice.observe(call, wait=1)
    assert state.admission is AdmissionState.ACCEPTED
    assert state.error is ErrorCode.STALE_BINDING
    assert state.effect is EffectState.NOT_DISPATCHED
    assert not h.a.calls


async def test_cancel_admission_wait_does_not_claim_owner_did_not_commit(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    h.policy.admit_gate = asyncio.Event()
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    call = await h.alice.submit(ref, action="read", arguments={"value": "x"})
    await asyncio.wait_for(h.policy.admit_entered.wait(), 1)
    await h.alice.cancel(call)
    state = await h.alice.observe(call, wait=1)
    assert state.admission is AdmissionState.UNKNOWN
    assert state.effect is EffectState.NOT_DISPATCHED
