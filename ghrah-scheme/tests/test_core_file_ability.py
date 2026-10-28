# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""把未修改的 Core 执行器与文件 Ability 接到 Scheme，验证真实文件效果。"""

import asyncio
from pathlib import Path

from ghrah.abilities.builtin.fs_permissions import AccessApprovalHook, FSPermissionChecker
from ghrah.abilities.builtin.read_file import ReadFileAbility
from ghrah.abilities.builtin.write_file import WriteFileAbility
from ghrah.abilities.context import AbilityExecutionContext
from ghrah.abilities.executor import LocalAbilityExecutor
from ghrah.abilities.hooks import Hook, HookPoint, HookResult
from ghrah.abilities.invocation import AbilityInvocation
from ghrah.types.results import ActionResult
from ghrah_scheme_support import ALICE, BOB, Policy
from pydantic import Field

from ghrah.scheme import (
    ActionSpec,
    CancellationState,
    EffectState,
    InvocationState,
    ProviderCall,
    SchemeModel,
    SchemeRuntime,
    SchemeSpec,
)


class ReadInput(SchemeModel):
    file: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}\.txt$")


class WriteInput(ReadInput):
    content: str = Field(max_length=8192)


class AbilityOutput(SchemeModel):
    outcome: str
    data: dict


NOTES = SchemeSpec(
    kind="ability",
    contract="ghrah.file-notes",
    version="1",
    actions=(
        ActionSpec("read", ReadInput, AbilityOutput),
        ActionSpec("write", WriteInput, AbilityOutput, mutating=True),
    ),
)


class Gate(Hook):
    """在真实执行器的后置 hook 挂起，保留已完成写入的现场。"""

    hook_point = HookPoint.POST_EXECUTE

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.identities: list[tuple[str, str]] = []

    async def should_trigger(self, context: AbilityExecutionContext) -> bool:
        return True

    async def execute(
        self, context: AbilityExecutionContext, result: ActionResult | None
    ) -> HookResult:
        self.identities.append((context.agent_name, context.invocation.tool_call_id))
        self.entered.set()
        await self.release.wait()
        return HookResult.continue_()


class Notes:
    """仅测试的领域适配器；文件权限仍由 Core checker 核定。"""

    def __init__(self, root: Path, gate: Gate | None = None) -> None:
        self.root = root
        root.mkdir()
        checker = FSPermissionChecker(allowed_paths=[str(root)], require_approval=False)
        self.abilities = {
            "read": ReadFileAbility(permission_checker=checker),
            "write": WriteFileAbility(permission_checker=checker),
        }
        self.hooks = [AccessApprovalHook(checker), *([gate] if gate else [])]

    async def execute(self, call: ProviderCall) -> SchemeModel:
        assert isinstance(call.arguments, ReadInput)
        ability = self.abilities[call.action]
        arguments = {"file_path": str(self.root / call.arguments.file)}
        if isinstance(call.arguments, WriteInput):
            arguments["content"] = call.arguments.content
        context = AbilityExecutionContext(
            invocation=AbilityInvocation(
                ability_name=ability.name, tool_args=arguments, tool_call_id=call.invocation_id
            ),
            agent_name=call.principal.agent_id,
        )
        executor = LocalAbilityExecutor(agent_name=call.principal.agent_id, hooks=self.hooks)
        result = await executor.execute_ability(ability, context)
        return AbilityOutput(outcome=result.outcome.value, data=result.data)


async def test_real_abilities_local_bindings_and_dedup(tmp_path: Path) -> None:
    runtime = SchemeRuntime(Policy())
    try:
        for principal, provider in [(ALICE, "alice"), (BOB, "bob")]:
            name = principal.display_name
            root = tmp_path / provider
            runtime.manager.publish(provider, NOTES, Notes(root))
            runtime.manager.bind(
                principal.scope,
                "ability",
                "notes",
                provider,
                contract="ghrah.file-notes",
                contract_version="1",
            )
            session = runtime.manager.create_session(principal)
            ref = await session.acquire(
                "/scheme/ability/notes", actions=frozenset({"read", "write"})
            )
            call = await session.submit(
                ref,
                action="write",
                arguments={"file": "memo.txt", "content": name},
                request_key="write-memo",
            )
            written = await session.observe(call, wait=1)
            assert written.state is InvocationState.COMPLETED
            assert written.result["outcome"] == "success"
            assert (root / "memo.txt").read_text() == name
            duplicate = await session.submit(
                ref,
                action="write",
                arguments={"file": "memo.txt", "content": name},
                request_key="write-memo",
            )
            assert (await session.observe(duplicate)).invocation_id == written.invocation_id
            read = await session.submit(ref, action="read", arguments={"file": "memo.txt"})
            result = await session.observe(read, wait=1)
            assert result.result["outcome"] == "success"
            assert name in str(result.result["data"])
        assert (tmp_path / "alice/memo.txt").read_text() != (tmp_path / "bob/memo.txt").read_text()
    finally:
        assert (await runtime.manager.close()).complete


async def test_real_checker_rejects_symlink_escape(tmp_path: Path) -> None:
    runtime = SchemeRuntime(Policy())
    try:
        notes = Notes(tmp_path / "alice")
        outside = tmp_path / "outside.txt"
        outside.write_text("untouched")
        (notes.root / "link.txt").symlink_to(outside)
        runtime.manager.publish("alice", NOTES, notes)
        runtime.manager.bind(
            ALICE.scope,
            "ability",
            "notes",
            "alice",
            contract="ghrah.file-notes",
            contract_version="1",
        )
        session = runtime.manager.create_session(ALICE)
        ref = await session.acquire("/scheme/ability/notes", actions=frozenset({"write"}))
        call = await session.submit(
            ref,
            action="write",
            arguments={"file": "link.txt", "content": "escaped"},
            request_key="escape",
        )
        result = await session.observe(call, wait=1)
        assert result.state is InvocationState.COMPLETED
        assert result.result["outcome"] == "failure"
        assert outside.read_text() == "untouched"
    finally:
        assert (await runtime.manager.close()).complete


async def test_cancel_after_real_write_preserves_effect_and_identity(tmp_path: Path) -> None:
    runtime = SchemeRuntime(Policy())
    gate = Gate()
    try:
        notes = Notes(tmp_path / "alice", gate)
        runtime.manager.publish("alice", NOTES, notes)
        runtime.manager.bind(
            ALICE.scope,
            "ability",
            "notes",
            "alice",
            contract="ghrah.file-notes",
            contract_version="1",
        )
        session = runtime.manager.create_session(ALICE)
        ref = await session.acquire("/scheme/ability/notes", actions=frozenset({"write"}))
        call = await session.submit(
            ref,
            action="write",
            arguments={"file": "memo.txt", "content": "committed"},
            request_key="cancelled-after-write",
        )
        await asyncio.wait_for(gate.entered.wait(), 1)
        assert (notes.root / "memo.txt").read_text() == "committed"
        await session.cancel(call)
        result = await session.observe(call, wait=1)
        assert result.cancellation is CancellationState.CONFIRMED
        assert result.effect is EffectState.UNKNOWN
        assert gate.identities == [("agent-alice", result.invocation_id)]
        assert (notes.root / "memo.txt").read_text() == "committed"
    finally:
        gate.release.set()
        assert (await runtime.manager.close()).complete
