# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""验证异构契约以及项目、Agent、运行身份之间的隔离。"""

from dataclasses import replace

import pytest
from ghrah_scheme_support import ALICE, BOB, SPEC, Harness, MemoryProvider

from ghrah.scheme import (
    ActionSpec,
    AgentScope,
    ErrorCode,
    InvocationState,
    Principal,
    ProviderCall,
    SchemeError,
    SchemeModel,
    SchemeSpec,
)

PATH = "/scheme/ability/notes"


class RenderInput(SchemeModel):
    template: str


class RenderOutput(SchemeModel):
    rendered: str


REPORTS = SchemeSpec(
    kind="ability",
    contract="acme.reports",
    version="1",
    actions=(ActionSpec("render", RenderInput, RenderOutput),),
)


class Reports:
    async def execute(self, call: ProviderCall) -> SchemeModel:
        assert isinstance(call.arguments, RenderInput)
        return RenderOutput(rendered=call.arguments.template)


async def test_heterogeneous_contracts_share_kind_and_version(scheme_harness: Harness) -> None:
    h = scheme_harness
    h.runtime.manager.publish("reports", REPORTS, Reports())
    h.policy.grants.add((ALICE.project_id, ALICE.agent_id, "reports"))
    h.runtime.manager.bind(
        ALICE.scope,
        "ability",
        "reports",
        "reports",
        contract=REPORTS.contract,
        contract_version=REPORTS.version,
        contract_digest=REPORTS.digest,
    )
    notes = await h.alice.acquire(PATH, actions=frozenset({"read"}), contract=SPEC.contract)
    reports = await h.alice.acquire(
        "/scheme/ability/reports",
        actions=frozenset({"render"}),
        contract=REPORTS.contract,
        contract_version="1",
        contract_digest=REPORTS.digest,
    )
    first = await h.alice.submit(notes, action="read", arguments={"value": "notes"})
    second = await h.alice.submit(reports, action="render", arguments={"template": "daily"})
    a, b = await h.alice.observe(first, wait=1), await h.alice.observe(second, wait=1)
    assert a.result["value"] == "notes" and b.result == {"rendered": "daily"}
    assert a.target.kind == b.target.kind == "ability"
    assert a.target.contract == SPEC.contract and b.target.contract == REPORTS.contract
    assert a.target.contract_digest == SPEC.digest and b.target.contract_digest == REPORTS.digest
    assert a.target.scope == b.target.scope == ALICE.scope


@pytest.mark.parametrize(
    "wrong",
    [
        {"kind": "view"},
        {"contract": "acme.other"},
        {"contract_version": "2"},
        {"contract_digest": "sha256:" + "0" * 64},
    ],
)
async def test_wrong_contract_binding_cannot_replace_valid_binding(
    scheme_harness: Harness, wrong: dict
) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    expected = dict(
        kind="ability", contract=SPEC.contract, contract_version="1", contract_digest=SPEC.digest
    )
    expected.update(wrong)
    with pytest.raises(SchemeError, match=ErrorCode.CONTRACT_MISMATCH):
        h.runtime.manager.bind(ALICE.scope, alias="notes", provider_id="bob", **expected)
    call = await h.alice.submit(ref, action="read", arguments={"value": "unchanged"})
    assert (await h.alice.observe(call, wait=1)).target.provider_id == "alice"


@pytest.mark.parametrize(
    "wrong",
    [{"contract": "acme.other"}, {"contract_version": "2"}, {"contract_digest": "wrong"}],
)
async def test_acquire_rejects_unexpected_contract(scheme_harness: Harness, wrong: dict) -> None:
    with pytest.raises(SchemeError, match=ErrorCode.CONTRACT_MISMATCH):
        await scheme_harness.alice.acquire(PATH, actions=frozenset({"read"}), **wrong)


async def test_restart_under_different_contract_does_not_retarget_binding(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    old = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    assert (await h.runtime.manager.withdraw("alice")).complete
    other = replace(SPEC, contract="acme.other")
    h.runtime.manager.publish("alice", other, MemoryProvider())
    with pytest.raises(SchemeError, match=ErrorCode.STALE_PROVIDER):
        await h.alice.submit(old, action="read", arguments={"value": "x"})
    with pytest.raises(SchemeError, match=ErrorCode.STALE_BINDING):
        await h.alice.acquire(PATH, actions=frozenset({"read"}))
    h.runtime.manager.bind(
        ALICE.scope, "ability", "notes", "alice", contract=other.contract, contract_version="1"
    )
    new = await h.alice.acquire(PATH, actions=frozenset({"read"}), contract=other.contract)
    call = await h.alice.submit(new, action="read", arguments={"value": "new"})
    assert (await h.alice.observe(call, wait=1)).target.contract == other.contract


async def test_rebinding_other_agent_in_shared_namespace_preserves_reference(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    assert ALICE.project_id == BOB.project_id and ALICE.namespace == BOB.namespace
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    h.runtime.manager.bind(
        BOB.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    h.runtime.manager.unbind(BOB.scope, "ability", "notes")
    call = await h.alice.submit(ref, action="read", arguments={"value": "untouched"})
    assert (await h.alice.observe(call, wait=1)).target.provider_id == "alice"
    with pytest.raises(SchemeError, match=ErrorCode.NOT_FOUND):
        await h.bob.acquire(PATH, actions=frozenset({"read"}))


@pytest.mark.parametrize("dimension", ["agent_id", "project_id", "run_id"])
async def test_identity_dimensions_isolate_bindings_and_dedup(
    scheme_harness: Harness, dimension: str
) -> None:
    h = scheme_harness
    other = replace(ALICE, **{dimension: "other-" + dimension})
    # 两个实际主体可以具有相同显示名和组织 namespace。
    session = h.runtime.manager.create_session(other)
    with pytest.raises(SchemeError, match=ErrorCode.NOT_FOUND):
        await session.acquire(PATH, actions=frozenset({"write"}))
    h.policy.grants.add((other.project_id, other.agent_id, "alice"))
    h.runtime.manager.bind(
        other.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    own = await session.acquire(PATH, actions=frozenset({"write"}))
    first = await h.alice.submit(ref, action="write", arguments={"value": "x"}, request_key="same")
    a = await h.alice.observe(first, wait=1)
    second = await session.submit(own, action="write", arguments={"value": "x"}, request_key="same")
    b = await session.observe(second, wait=1)
    assert a.invocation_id != b.invocation_id and h.a.effects == ["x", "x"]
    assert b.target.scope == other.scope
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_REFERENCE):
        await session.submit(
            ref, action="write", arguments={"value": "forged"}, request_key="forge"
        )
    assert h.a.calls[-1].principal.scope == other.scope


async def test_display_metadata_changes_preserve_authorization_binding_and_retry(
    scheme_harness: Harness,
) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"write"}))
    first = await h.alice.submit(ref, action="write", arguments={"value": "once"}, request_key="k")
    original = await h.alice.observe(first, wait=1)
    await h.alice.close()
    renamed = replace(ALICE, display_name="Renamed", namespace="new-organization-label")
    session = h.runtime.manager.create_session(renamed)
    own = await session.acquire(PATH, actions=frozenset({"write"}))
    retry = await session.submit(own, action="write", arguments={"value": "once"}, request_key="k")
    result = await session.observe(retry, wait=1)
    assert result.invocation_id == original.invocation_id and h.a.effects == ["once"]
    assert h.policy.authorizations[-1].principal.agent_id == ALICE.agent_id
    assert h.policy.authorizations[-1].principal.display_name == "Renamed"
    with pytest.raises(SchemeError, match=ErrorCode.REQUEST_CONFLICT):
        await session.submit(own, action="write", arguments={"value": "changed"}, request_key="k")


async def test_same_display_name_does_not_share_authorization(scheme_harness: Harness) -> None:
    h = scheme_harness
    other = replace(ALICE, agent_id="another-agent")
    session = h.runtime.manager.create_session(other)
    h.runtime.manager.bind(
        other.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    with pytest.raises(SchemeError, match=ErrorCode.ACCESS_DENIED):
        await session.acquire(PATH, actions=frozenset({"read"}))
    assert h.policy.authorizations[-1].principal.agent_id == "another-agent"
    assert h.policy.authorizations[-1].principal.display_name == ALICE.display_name


async def test_closed_run_reference_cannot_revive_in_new_run(scheme_harness: Harness) -> None:
    h = scheme_harness
    ref = await h.alice.acquire(PATH, actions=frozenset({"read"}))
    await h.alice.close()
    next_run = replace(ALICE, run_id="run-next")
    session = h.runtime.manager.create_session(next_run)
    h.runtime.manager.bind(
        next_run.scope, "ability", "notes", "alice", contract=SPEC.contract, contract_version="1"
    )
    with pytest.raises(SchemeError, match=ErrorCode.INVALID_REFERENCE):
        await session.submit(ref, action="read", arguments={"value": "old"})
    new = await session.acquire(PATH, actions=frozenset({"read"}))
    call = await session.submit(new, action="read", arguments={"value": "new"})
    assert (await session.observe(call, wait=1)).state is InvocationState.COMPLETED


@pytest.mark.parametrize("field", ["project_id", "agent_id", "run_id"])
@pytest.mark.parametrize("value", ["", " ", " leading", None])
def test_stable_identity_requires_explicit_valid_ids(field: str, value: object) -> None:
    data = dict(project_id="project", agent_id="agent", run_id="run")
    data[field] = value
    with pytest.raises(ValueError):
        Principal(**data)
    with pytest.raises(ValueError):
        AgentScope(**data)


@pytest.mark.parametrize("contract", ["", "Acme.notes", "acme..notes", "../notes", "acme.notes "])
def test_contract_identity_must_be_canonical(contract: str) -> None:
    with pytest.raises(ValueError):
        replace(SPEC, contract=contract)


def test_contract_digest_pins_identity_content_and_normalizes_action_order() -> None:
    assert replace(SPEC, actions=tuple(reversed(SPEC.actions))).digest == SPEC.digest
    for changed in (
        replace(SPEC, contract="acme.other"),
        replace(SPEC, version="2"),
        replace(SPEC, kind="other"),
        replace(SPEC, actions=(replace(SPEC.actions[0], mutating=True), SPEC.actions[1])),
        replace(SPEC, actions=(replace(SPEC.actions[0], description="changed"), SPEC.actions[1])),
    ):
        assert changed.digest != SPEC.digest


async def test_same_contract_version_cannot_be_republished_under_different_kind(
    scheme_harness: Harness,
) -> None:
    with pytest.raises(SchemeError, match=ErrorCode.CONTRACT_MISMATCH):
        scheme_harness.runtime.manager.publish(
            "wrong-kind", replace(SPEC, kind="other"), MemoryProvider()
        )
