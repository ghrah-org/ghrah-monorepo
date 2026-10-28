# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""发现与 schema 披露分别核定，目录从唯一契约和绑定重建。"""

import asyncio

import pytest
from ghrah.protocol.scheme import ContractDescriptor
from ghrah_scheme_support import ALICE, BOB, SPEC, Harness, Policy, make_harness

from ghrah.scheme import (
    AccessRequest,
    AccessStage,
    Authorization,
    ErrorCode,
    ProviderState,
    RuntimeLimits,
    SchemeError,
    SchemeRuntime,
)


class DiscoveryPolicy(Policy):
    def __init__(self) -> None:
        super().__init__()
        self.visible = {"read", "write"}
        self.readable = {"read"}
        self.invokable = {"write"}
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def authorize(self, request: AccessRequest) -> Authorization:
        base = await super().authorize(request)
        if self.gate is not None and request.stage is AccessStage.DISCOVER:
            self.entered.set()
            await self.gate.wait()
        choices = {
            AccessStage.DISCOVER: self.visible,
            AccessStage.DESCRIBE: self.readable,
            AccessStage.INVOKE: self.invokable,
        }
        allowed = base.allowed and request.actions <= choices.get(request.stage, request.actions)
        return Authorization(allowed, "discovery-policy" if allowed else "")


@pytest.fixture
async def discovery_harness():
    policy = DiscoveryPolicy()
    h = make_harness(SchemeRuntime(policy), policy)
    yield h
    if policy.gate is not None:
        policy.gate.set()
    assert (await h.runtime.manager.close()).complete


async def test_summary_schema_and_invoke_are_separate(discovery_harness: Harness) -> None:
    h = discovery_harness
    view = await h.alice.describe("ability", "notes")
    assert view.contract_digest == SPEC.digest and view.available
    read, write = view.actions
    assert read.name == "read" and read.input_schema is not None and not read.invokable
    assert write.name == "write" and write.input_schema is None and write.invokable
    assert not h.policy.admissions and not h.a.calls
    assert view.actions[0].input_schema == SPEC.descriptor().actions[0].input_schema
    view.actions[0].input_schema["tampered"] = True
    assert "tampered" not in (await h.alice.describe("ability", "notes")).actions[0].input_schema
    assert SPEC.descriptor().fingerprint() == SPEC.digest


async def test_hidden_resource_and_missing_resource_have_same_outcome(
    discovery_harness: Harness,
) -> None:
    h = discovery_harness
    h.policy.visible.clear()
    assert (await h.alice.catalog()).entries == []
    for alias in ("notes", "missing"):
        with pytest.raises(SchemeError, match=ErrorCode.NOT_FOUND):
            await h.alice.describe("ability", alias)


async def test_revoke_changes_subsequent_projection(discovery_harness: Harness) -> None:
    h = discovery_harness
    assert len((await h.alice.catalog()).entries) == 1
    h.policy.revoke(ALICE.agent_id, "alice")
    assert (await h.alice.catalog()).entries == []
    assert len((await h.bob.catalog()).entries) == 1


async def test_alias_is_agent_scoped_and_catalog_has_no_hidden_count(
    discovery_harness: Harness,
) -> None:
    h = discovery_harness
    h.runtime.manager.bind(
        ALICE.scope, "ability", "hidden", "bob", contract=SPEC.contract, contract_version="1"
    )
    view = await h.alice.catalog("ability")
    assert [item.alias for item in view.entries] == ["notes"]
    assert set(view.model_dump()) == {"format", "entries"}
    assert (await h.bob.describe("ability", "notes")).alias == "notes"
    assert ALICE.scope != BOB.scope


async def test_rebind_while_discovering_discards_old_description(
    discovery_harness: Harness,
) -> None:
    h = discovery_harness
    h.policy.gate = asyncio.Event()
    task = asyncio.create_task(h.alice.describe("ability", "notes"))
    await asyncio.wait_for(h.policy.entered.wait(), 1)
    h.runtime.manager.bind(
        ALICE.scope, "ability", "notes", "bob", contract=SPEC.contract, contract_version="1"
    )
    h.policy.gate.set()
    with pytest.raises(SchemeError, match=ErrorCode.NOT_FOUND):
        await task


async def test_discovery_capacity_and_session_close() -> None:
    policy = DiscoveryPolicy()
    h = make_harness(SchemeRuntime(policy, limits=RuntimeLimits(max_discoveries=1)), policy)
    policy.gate = asyncio.Event()
    task = asyncio.create_task(h.alice.catalog())
    try:
        await asyncio.wait_for(policy.entered.wait(), 1)
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            await h.bob.catalog()
        await h.alice.close()
        policy.gate.set()
        with pytest.raises(SchemeError, match=ErrorCode.SESSION_CLOSED):
            await task
    finally:
        policy.gate.set()
        await h.runtime.manager.close()


async def test_unavailable_resource_visible_but_not_executable(discovery_harness: Harness) -> None:
    h = discovery_harness
    call = await h.alice.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
    incarnation = (await h.alice.describe("ability", "notes")).binding_revision
    assert incarnation > 0
    provider_incarnation = h.runtime._providers["alice"].incarnation
    h.runtime.manager.report_health("alice", provider_incarnation, ProviderState.UNAVAILABLE)
    view = await h.alice.describe("ability", "notes")
    assert not view.available and not any(action.invokable for action in view.actions)
    with pytest.raises(SchemeError, match=ErrorCode.PROVIDER_UNAVAILABLE):
        await h.alice.submit(call, action="read", arguments={"value": "x"})


def test_descriptor_is_single_source_and_export_is_isolated() -> None:
    exported = SPEC.descriptor()
    assert (
        ContractDescriptor.model_validate_json(exported.model_dump_json(by_alias=True)) == exported
    )
    exported.actions[0].input_schema["extra"] = "changed"
    assert SPEC.descriptor().fingerprint() == SPEC.digest
    assert "extra" not in SPEC.descriptor().actions[0].input_schema


async def test_catalog_has_total_deadline_and_authority_residual_owner() -> None:
    policy = Policy()
    h = make_harness(SchemeRuntime(policy, limits=RuntimeLimits(default_timeout=0.02)), policy)
    policy.authorize_gate = asyncio.Event()
    policy.stubborn = True
    try:
        with pytest.raises(SchemeError, match=ErrorCode.AUTHORITY_UNAVAILABLE):
            await h.alice.catalog()
        await asyncio.wait_for(policy.cancel_seen.wait(), 1)
        report = await h.runtime.manager.close(timeout=0)
        assert not report.complete and report.pending_controls
        policy.authorize_gate.set()
        assert (await h.runtime.manager.close(timeout=0.02)).complete
    finally:
        policy.authorize_gate.set()
        await h.runtime.manager.close(timeout=0.02)


async def test_discovery_output_budget() -> None:
    policy = Policy()
    h = make_harness(SchemeRuntime(policy, limits=RuntimeLimits(max_result_bytes=64)), policy)
    try:
        with pytest.raises(SchemeError, match=ErrorCode.INVALID_RESULT):
            await h.alice.catalog()
    finally:
        await h.runtime.manager.close()


@pytest.mark.parametrize(
    "kind,alias",
    [(None, "notes"), ("ability", None), ("ABILITY", "notes"), ("ability", "../notes")],
)
async def test_describe_arguments_are_explicit(discovery_harness: Harness, kind, alias) -> None:
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_ARGUMENT):
        await discovery_harness.alice.describe(kind, alias)


async def test_discovery_remains_available_with_full_business_capacity() -> None:
    policy = Policy()
    h = make_harness(SchemeRuntime(policy, limits=RuntimeLimits(max_inflight=1)), policy)
    h.a.before = asyncio.Event()
    try:
        ref = await h.alice.acquire("/scheme/ability/notes", actions=frozenset({"read"}))
        await h.alice.submit(ref, action="read", arguments={"value": "blocked"})
        await asyncio.wait_for(h.a.entered.wait(), 1)
        with pytest.raises(SchemeError, match=ErrorCode.BUSY):
            await h.alice.submit(ref, action="read", arguments={"value": "overflow"})
        assert [entry.alias for entry in (await h.alice.catalog()).entries] == ["notes"]
    finally:
        h.a.before.set()
        assert (await h.runtime.manager.close()).complete
