# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""只有运行时持有的可变记录，不交给消费者。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ghrah.scheme.contracts import ActionSpec, Provider, ProviderLifecycle, SchemeModel, SchemeSpec
from ghrah.scheme.errors import ErrorCode
from ghrah.scheme.models import (
    AdmissionState,
    CancellationState,
    EffectState,
    InvocationState,
    JsonObject,
    OwnerRef,
    OwnerState,
    ProviderState,
    Requester,
    ResourceRef,
    Target,
)


@dataclass(frozen=True)
class Binding:
    provider_id: str
    contract: str
    version: str
    digest: str
    revision: int


@dataclass
class SessionState:
    principal: Requester
    active: bool = True
    observers: int = 0


@dataclass
class Registration:
    spec: SchemeSpec
    provider: Provider
    incarnation: str
    concurrency: int
    pending_limit: int
    semaphore: asyncio.Semaphore
    state: ProviderState = ProviderState.READY
    inflight: int = 0
    owner: ResourceOwner | None = None


@dataclass(frozen=True)
class ResourceEntry:
    reference: ResourceRef
    target: Target
    actions: frozenset[str]


@dataclass
class Invocation:
    invocation_id: str
    holder: str
    principal: Requester
    entry: ResourceEntry
    registration: Registration
    action: ActionSpec
    model: SchemeModel
    arguments: JsonObject
    parent_id: str | None
    state: InvocationState = InvocationState.QUEUED
    admission: AdmissionState = AdmissionState.NOT_REQUESTED
    effect: EffectState = EffectState.NOT_DISPATCHED
    cancellation: CancellationState = CancellationState.NONE
    cancel_reason: ErrorCode | None = None
    error: ErrorCode | None = None
    basis: str | None = None
    admission_record: str | None = None
    result: JsonObject | None = None
    version: int = 0
    settled: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    timer: asyncio.TimerHandle | None = None

    def touch(self) -> None:
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()


@dataclass
class ResourceOwner:
    reference: OwnerRef
    lifecycle: ProviderLifecycle
    state: OwnerState = OwnerState.PREPARING
    error: ErrorCode | None = None
    exports: list[Registration] = field(default_factory=list)
    stopping: bool = False
    prepare_task: asyncio.Task[None] | None = None
    close_task: asyncio.Task[None] | None = None
    timer: asyncio.TimerHandle | None = None
