# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""只供验证使用的策略和可控制提供者，不进入产品包。"""

import asyncio
from dataclasses import dataclass

from ghrah.scheme import (
    AccessRequest,
    AccessStage,
    ActionSpec,
    Admission,
    Authorization,
    Principal,
    ProviderCall,
    SchemeModel,
    SchemeRuntime,
    SchemeSpec,
    Session,
)


class Arguments(SchemeModel):
    value: str
    tags: list[str] = []


class Output(SchemeModel):
    value: str
    principal: str


SPEC = SchemeSpec(
    "ability",
    "1",
    (
        ActionSpec("read", Arguments, Output),
        ActionSpec("write", Arguments, Output, mutating=True),
    ),
)


class Policy:
    """实验许可表只留在测试，实际产品通过领域 Authority 接入。"""

    def __init__(self) -> None:
        self.grants = {("Alice", "alice"), ("Bob", "bob")}
        self.revision = 1
        self.allow_results = True
        self.admissions: list[AccessRequest] = []
        self.authorizations: list[AccessRequest] = []
        self.admit_entered = asyncio.Event()
        self.admit_gate: asyncio.Event | None = None
        self.authorize_entered = asyncio.Event()
        self.authorize_gate: asyncio.Event | None = None
        self.stubborn = False
        self.cancel_seen = asyncio.Event()

    def revoke(self, principal: str, provider: str) -> None:
        self.grants.discard((principal, provider))
        self.revision += 1

    async def authorize(self, request: AccessRequest) -> Authorization:
        self.authorizations.append(request)
        self.authorize_entered.set()
        if self.authorize_gate is not None:
            try:
                await self.authorize_gate.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                if not self.stubborn:
                    raise
                await self.authorize_gate.wait()
        allowed = (request.principal.name, request.target.provider_id) in self.grants
        if request.stage is AccessStage.RESULT:
            allowed = allowed and self.allow_results
        return Authorization(allowed, f"revision:{self.revision}" if allowed else "")

    async def admit(self, request: AccessRequest) -> Admission:
        self.admissions.append(request)
        self.admit_entered.set()
        if self.admit_gate is not None:
            await self.admit_gate.wait()
        allowed = (request.principal.name, request.target.provider_id) in self.grants
        return Admission(
            allowed,
            f"revision:{self.revision}" if allowed else "",
            f"record:{request.invocation_id}" if allowed else None,
        )


class MemoryProvider:
    """在产生效果之前或之后阻塞，区分协作取消与非合作场景。"""

    def __init__(self) -> None:
        self.calls: list[ProviderCall] = []
        self.effects: list[str] = []
        self.entered = asyncio.Event()
        self.effect_written = asyncio.Event()
        self.before: asyncio.Event | None = None
        self.after: asyncio.Event | None = None
        self.stubborn = False
        self.cancel_seen = asyncio.Event()

    async def execute(self, call: ProviderCall) -> SchemeModel:
        self.calls.append(call)
        self.entered.set()
        if self.before is not None:
            await self.before.wait()
        assert isinstance(call.arguments, Arguments)
        if call.action == "write":
            self.effects.append(call.arguments.value)
        self.effect_written.set()
        if self.after is not None:
            try:
                await self.after.wait()
            except asyncio.CancelledError:
                self.cancel_seen.set()
                if not self.stubborn:
                    raise
                await self.after.wait()
        return Output(value=call.arguments.value, principal=call.principal.name)


@dataclass
class Harness:
    runtime: SchemeRuntime
    policy: Policy
    alice: Session
    bob: Session
    a: MemoryProvider
    b: MemoryProvider


def make_harness(runtime: SchemeRuntime | None = None, policy: Policy | None = None) -> Harness:
    """受信装配两个具有同名局部绑定的消费会话。"""
    policy = policy or Policy()
    runtime = runtime or SchemeRuntime(policy)
    a, b = MemoryProvider(), MemoryProvider()
    runtime.manager.publish("alice", SPEC, a)
    runtime.manager.publish("bob", SPEC, b)
    runtime.manager.bind("a", "notes", "alice", contract_version="1")
    runtime.manager.bind("b", "notes", "bob", contract_version="1")
    return Harness(
        runtime,
        policy,
        runtime.manager.create_session(Principal("Alice", "a")),
        runtime.manager.create_session(Principal("Bob", "b")),
        a,
        b,
    )
