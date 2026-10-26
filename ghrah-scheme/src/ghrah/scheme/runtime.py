# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""ghrah 进程内的 Scheme 控制原语；所有状态归一个 asyncio loop。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import asdict
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from ghrah.scheme._state import Binding, Invocation, Registration, ResourceEntry, SessionState
from ghrah.scheme.authority import Authority, DenyAuthority
from ghrah.scheme.contracts import Provider, ProviderCall, SchemeModel, SchemeSpec
from ghrah.scheme.errors import ErrorCode, SchemeError
from ghrah.scheme.models import (
    AccessRequest,
    AccessStage,
    AdmissionState,
    AgentScope,
    Authorization,
    CancelDisposition,
    CancellationState,
    DrainReport,
    EffectState,
    InvocationRef,
    InvocationSnapshot,
    InvocationState,
    JsonObject,
    JsonValue,
    Principal,
    ProviderState,
    ReleaseReport,
    ResourceRef,
    RuntimeLimits,
    Target,
)

_chain: ContextVar[tuple[tuple[str, str, str], ...]] = ContextVar("ghrah_scheme_chain", default=())


def _budget(value: float, maximum: float, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemeError(ErrorCode.INVALID_ARGUMENT)
    if not math.isfinite(value) or value < 0 or (not zero and value == 0) or value > maximum:
        raise SchemeError(ErrorCode.INVALID_ARGUMENT)
    return value


def _json_object(value: object, limit: int, code: ErrorCode) -> JsonObject:
    def validate(item: object) -> None:
        if type(item) in (str, int, float, bool, type(None)):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise SchemeError(code)

    try:
        validate(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        if len(encoded.encode()) > limit:
            raise SchemeError(code)
        result: object = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise SchemeError(code) from error
    if not isinstance(result, dict) or not all(isinstance(key, str) for key in result):
        raise SchemeError(code)
    return cast(JsonObject, result)


def _path(path: str) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(path, str) or len(path.encode()) > 1024:
        raise SchemeError(ErrorCode.INVALID_PATH)
    if any(char in path for char in ("\\", "%", "?", "#", "\x00")):
        raise SchemeError(ErrorCode.INVALID_PATH)
    parts = path.split("/")
    if len(parts) < 4 or parts[:2] != ["", "scheme"]:
        raise SchemeError(ErrorCode.INVALID_PATH)
    if any(part in {"", ".", ".."} for part in parts[1:]):
        raise SchemeError(ErrorCode.INVALID_PATH)
    if not all(re.fullmatch(r"[a-z][a-z0-9_-]*", part) for part in parts[2:4]):
        raise SchemeError(ErrorCode.INVALID_PATH)
    return parts[2], parts[3], tuple(parts[4:])


class SchemeRuntime:
    """显式创建的自用运行时；只把 Session 注入普通消费者。"""

    def __init__(
        self, authority: Authority | None = None, *, limits: RuntimeLimits | None = None
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._boot = uuid4().hex
        self._authority = authority if authority is not None else DenyAuthority()
        self._limits = limits or RuntimeLimits()
        self._closing = False
        self._sessions: dict[str, SessionState] = {}
        self._providers: dict[str, Registration] = {}
        self._contracts: dict[tuple[str, str], str] = {}
        self._bindings: dict[tuple[AgentScope, str, str], Binding] = {}
        self._binding_sequence = 0
        self._resources: dict[tuple[str, int], ResourceEntry] = {}
        self._generations: dict[tuple[str, int], int] = {}
        self._invocations: OrderedDict[str, Invocation] = OrderedDict()
        self._call_refs: dict[str, tuple[str, str]] = {}
        self._keys: dict[tuple[AgentScope, str], tuple[str, str]] = {}
        self._controls: dict[str, asyncio.Task[Authorization]] = {}
        self._inflight = 0
        self._manager = RuntimeManager(self)

    @property
    def manager(self) -> RuntimeManager:
        """仅由受信装配者持有的管理端口。"""
        return self._manager

    def _guard(self, *, writable: bool = False) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError as error:
            raise SchemeError(ErrorCode.WRONG_LOOP) from error
        if current is not self._loop:
            raise SchemeError(ErrorCode.WRONG_LOOP)
        if writable and self._closing:
            raise SchemeError(ErrorCode.RUNTIME_CLOSED)

    def _session(self, holder: str, *, active: bool = True) -> SessionState:
        self._guard(writable=active)
        session = self._sessions.get(holder)
        if session is None or (active and not session.active):
            raise SchemeError(ErrorCode.SESSION_CLOSED)
        return session

    async def _authorize(self, request: AccessRequest) -> Authorization:
        if len(self._controls) >= self._limits.max_authorizations:
            raise SchemeError(ErrorCode.BUSY)
        control_id = uuid4().hex
        task = asyncio.create_task(self._authority.authorize(request))
        self._controls[control_id] = task

        def done(completed: asyncio.Task[Authorization]) -> None:
            self._controls.pop(control_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)
        try:
            finished, _ = await asyncio.wait({task}, timeout=self._limits.default_timeout)
            if not finished:
                task.cancel()
                raise SchemeError(ErrorCode.AUTHORITY_UNAVAILABLE)
            return task.result()
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            raise
        except SchemeError:
            raise
        except Exception as error:
            raise SchemeError(ErrorCode.AUTHORITY_UNAVAILABLE) from error

    def _target(self, holder: str, path: str) -> Target:
        principal = self._session(holder).principal
        scheme, alias, resource = _path(path)
        binding = self._bindings.get((principal.scope, scheme, alias))
        if binding is None:
            raise SchemeError(ErrorCode.NOT_FOUND)
        registration = self._providers[binding.provider_id]
        if registration.state is not ProviderState.READY:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        return Target(
            scope=principal.scope,
            kind=scheme,
            alias=alias,
            resource=resource,
            provider_id=binding.provider_id,
            incarnation=registration.incarnation,
            contract=binding.contract,
            contract_version=binding.version,
            contract_digest=binding.digest,
            binding_revision=binding.revision,
        )

    def _validate_target(self, holder: str, target: Target) -> Registration:
        session = self._session(holder)
        registration = self._providers.get(target.provider_id)
        if registration is None or registration.incarnation != target.incarnation:
            raise SchemeError(ErrorCode.STALE_PROVIDER)
        key = session.principal.scope, target.kind, target.alias
        binding = self._bindings.get(key)
        if (
            target.scope != session.principal.scope
            or binding is None
            or binding.revision != target.binding_revision
            or binding.provider_id != target.provider_id
            or binding.version != target.contract_version
            or binding.contract != target.contract
            or binding.digest != target.contract_digest
            or registration.spec.kind != target.kind
            or registration.spec.contract != target.contract
            or registration.spec.version != target.contract_version
            or registration.spec.digest != target.contract_digest
        ):
            raise SchemeError(ErrorCode.STALE_BINDING)
        if registration.state is not ProviderState.READY:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        return registration

    def _resource(
        self, holder: str, reference: ResourceRef, *, usable: bool = True
    ) -> ResourceEntry:
        self._session(holder, active=usable)
        if (
            not isinstance(reference, ResourceRef)
            or reference.boot != self._boot
            or reference.holder != holder
        ):
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        entry = self._resources.get((holder, reference.slot))
        if entry is None or entry.reference != reference:
            raise SchemeError(ErrorCode.STALE_REFERENCE)
        if usable:
            self._validate_target(holder, entry.target)
        return entry

    async def _acquire(
        self,
        holder: str,
        path: str,
        actions: frozenset[str],
        contract: str | None,
        version: str | None,
        digest: str | None,
    ) -> ResourceRef:
        target = self._target(holder, path)
        registration = self._validate_target(holder, target)
        if not isinstance(actions, frozenset) or not actions:
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        if (
            (contract is not None and contract != target.contract)
            or (version is not None and version != target.contract_version)
            or (digest is not None and digest != target.contract_digest)
        ):
            raise SchemeError(ErrorCode.CONTRACT_MISMATCH)
        for action in actions:
            registration.spec.action(action)
        principal = self._session(holder).principal
        decision = await self._authorize(
            AccessRequest(principal, target, actions, AccessStage.ACQUIRE)
        )
        self._validate_target(holder, target)
        if not decision.allowed:
            raise SchemeError(ErrorCode.ACCESS_DENIED)
        slot = next(
            (
                i
                for i in range(self._limits.max_resource_refs)
                if (holder, i) not in self._resources
            ),
            None,
        )
        if slot is None:
            raise SchemeError(ErrorCode.REFERENCE_LIMIT)
        key = holder, slot
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        reference = ResourceRef(self._boot, holder, slot, generation, uuid4().hex)
        self._resources[key] = ResourceEntry(reference, target, actions)
        return reference

    def _new_call_ref(self, holder: str, invocation_id: str) -> InvocationRef:
        if (
            sum(owner == holder for owner, _ in self._call_refs.values())
            >= self._limits.max_invocation_refs
        ):
            raise SchemeError(ErrorCode.REFERENCE_LIMIT)
        reference = InvocationRef(self._boot, holder, uuid4().hex)
        self._call_refs[reference.token] = holder, invocation_id
        return reference

    def _lookup_call(self, holder: str, reference: InvocationRef) -> Invocation:
        self._session(holder, active=False)
        if (
            not isinstance(reference, InvocationRef)
            or reference.boot != self._boot
            or reference.holder != holder
        ):
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        item = self._call_refs.get(reference.token)
        if item is None or item[0] != holder:
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        record = self._invocations.get(item[1])
        if record is None:
            raise SchemeError(ErrorCode.REQUEST_EXPIRED)
        return record

    async def _submit(
        self,
        holder: str,
        reference: ResourceRef,
        action: str,
        arguments: JsonObject | SchemeModel,
        request_key: str | None,
        timeout: float | None,
    ) -> InvocationRef:
        entry = self._resource(holder, reference)
        registration = self._providers[entry.target.provider_id]
        spec = registration.spec.action(action)
        if action not in entry.actions:
            raise SchemeError(ErrorCode.ACCESS_DENIED)
        if spec.mutating and request_key is None:
            raise SchemeError(ErrorCode.REQUEST_KEY_REQUIRED)
        if request_key is not None and (
            not isinstance(request_key, str) or not request_key or len(request_key) > 256
        ):
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        budget = (
            self._limits.default_timeout
            if timeout is None
            else _budget(timeout, self._limits.default_timeout)
        )
        raw = arguments.model_dump(mode="json") if isinstance(arguments, SchemeModel) else arguments
        data = _json_object(raw, self._limits.max_input_bytes, ErrorCode.INVALID_INPUT)
        try:
            model = spec.input_model.model_validate(data, strict=True)
            data = _json_object(
                model.model_dump(mode="json"), self._limits.max_input_bytes, ErrorCode.INVALID_INPUT
            )
        except (ValidationError, ValueError) as error:
            raise SchemeError(ErrorCode.INVALID_INPUT) from error
        principal = self._session(holder).principal
        fingerprint = hashlib.sha256(
            json.dumps(
                {"target": asdict(entry.target), "action": action, "arguments": data},
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        key = (principal.scope, request_key) if request_key is not None else None
        lineage = tuple(item for item in _chain.get() if item[0] == self._boot)
        if any(
            (parent := self._invocations.get(item[2])) is None
            or parent.settled
            or parent.cancellation is not CancellationState.NONE
            for item in lineage
        ):
            raise SchemeError(ErrorCode.CANCELLED)
        if key is not None and key in self._keys:
            previous_fingerprint, invocation_id = self._keys[key]
            if previous_fingerprint != fingerprint:
                raise SchemeError(ErrorCode.REQUEST_CONFLICT)
            if invocation_id not in self._invocations:
                raise SchemeError(ErrorCode.REQUEST_EXPIRED)
            if any(item[2] == invocation_id for item in lineage):
                raise SchemeError(ErrorCode.REENTRANT_CALL)
            if lineage and not self._invocations[invocation_id].settled:
                raise SchemeError(ErrorCode.NESTED_BUSY)
            return self._new_call_ref(holder, invocation_id)
        if any(item[1] == entry.target.incarnation for item in lineage):
            raise SchemeError(ErrorCode.REENTRANT_CALL)
        if lineage and registration.inflight >= registration.concurrency:
            raise SchemeError(ErrorCode.NESTED_BUSY)
        if (
            self._inflight >= self._limits.max_inflight
            or registration.inflight >= registration.concurrency + registration.pending_limit
        ):
            raise SchemeError(ErrorCode.BUSY)
        if key is not None and len(self._keys) >= self._limits.max_request_keys:
            raise SchemeError(ErrorCode.REQUEST_KEY_LIMIT)
        invocation_id = uuid4().hex
        call_ref = self._new_call_ref(holder, invocation_id)
        record = Invocation(
            invocation_id,
            holder,
            principal,
            entry,
            registration,
            spec,
            model,
            data,
            lineage[-1][2] if lineage else None,
        )
        self._invocations[invocation_id] = record
        if key is not None:
            self._keys[key] = fingerprint, invocation_id
        self._inflight += 1
        registration.inflight += 1
        record.task = asyncio.create_task(
            self._execute(record), name=f"ghrah.scheme:{invocation_id}"
        )
        record.task.add_done_callback(lambda _: self._settle(record))
        record.timer = self._loop.call_later(
            budget, self._request_cancel, record, ErrorCode.DEADLINE_EXCEEDED
        )
        return call_ref

    async def _execute(self, record: Invocation) -> None:
        registration = record.registration
        try:
            # 先等可运行机会，领域接纳等待期间不占用提供者执行槽。
            async with registration.semaphore:
                self._resource(record.holder, record.entry.reference)
            record.state = InvocationState.ADMITTING
            record.admission = AdmissionState.PENDING
            record.touch()
            try:
                admission = await self._authority.admit(
                    AccessRequest(
                        record.principal,
                        record.entry.target,
                        frozenset({record.action.name}),
                        AccessStage.ADMIT,
                        copy.deepcopy(record.arguments),
                        record.invocation_id,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                record.admission = AdmissionState.UNKNOWN
                raise SchemeError(ErrorCode.AUTHORITY_UNAVAILABLE) from error
            record.basis = admission.basis or None
            record.admission_record = admission.record_id
            if not admission.allowed:
                record.admission = AdmissionState.REJECTED
                raise SchemeError(ErrorCode.ACCESS_DENIED)
            record.admission = AdmissionState.ACCEPTED
            self._resource(record.holder, record.entry.reference)
            record.state = InvocationState.ADMITTED
            record.touch()
            async with registration.semaphore:
                self._validate_target(record.holder, record.entry.target)
                if record.cancellation is not CancellationState.NONE:
                    raise asyncio.CancelledError
                record.state = InvocationState.RUNNING
                record.effect = EffectState.UNKNOWN
                record.touch()
                chain_token = _chain.set(
                    (*_chain.get(), (self._boot, registration.incarnation, record.invocation_id))
                )
                try:
                    result = await registration.provider.execute(
                        ProviderCall(
                            record.principal,
                            record.entry.target,
                            record.action.name,
                            record.model.model_copy(deep=True),
                            record.invocation_id,
                            admission.basis,
                            admission.record_id,
                        )
                    )
                finally:
                    _chain.reset(chain_token)
                try:
                    output = record.action.output_model.model_validate(
                        result.model_dump(mode="json"), strict=True
                    )
                    record.result = _json_object(
                        output.model_dump(mode="json"),
                        self._limits.max_result_bytes,
                        ErrorCode.INVALID_RESULT,
                    )
                except (ValidationError, ValueError, AttributeError, TypeError) as error:
                    raise SchemeError(ErrorCode.INVALID_RESULT) from error
                record.state = InvocationState.COMPLETED
                record.effect = EffectState.REPORTED
        except asyncio.CancelledError:
            if record.admission is AdmissionState.PENDING:
                record.admission = AdmissionState.UNKNOWN
            record.state = InvocationState.CANCELLED
            record.cancellation = CancellationState.CONFIRMED
            record.error = record.cancel_reason or ErrorCode.CANCELLED
        except SchemeError as error:
            record.state = (
                InvocationState.FAILED
                if record.effect is EffectState.UNKNOWN
                else InvocationState.REJECTED
            )
            record.error = error.code
        except Exception:
            record.state = InvocationState.FAILED
            record.error = ErrorCode.PROVIDER_FAILED
        finally:
            record.touch()

    def _settle(self, record: Invocation) -> None:
        task = record.task
        if record.settled or task is None or not task.done():
            return
        if task.cancelled():
            record.state = InvocationState.CANCELLED
            record.cancellation = CancellationState.CONFIRMED
            record.error = record.cancel_reason or ErrorCode.CANCELLED
        else:
            task.exception()
        record.settled = True
        record.registration.inflight -= 1
        self._inflight -= 1
        if record.timer is not None:
            record.timer.cancel()
        if record.registration.state is ProviderState.DRAINING and not record.registration.inflight:
            record.registration.state = ProviderState.WITHDRAWN
        record.touch()
        completed = [key for key, item in self._invocations.items() if item.settled]
        for key in completed[: -self._limits.max_retained_calls]:
            del self._invocations[key]

    def _request_cancel(self, record: Invocation, reason: ErrorCode) -> None:
        if record.settled or record.cancellation is not CancellationState.NONE:
            return
        record.cancellation = CancellationState.REQUESTED
        record.cancel_reason = reason
        record.touch()
        if record.task is not None:
            record.task.cancel()
        for child in tuple(self._invocations.values()):
            if child.parent_id == record.invocation_id:
                self._request_cancel(child, reason)

    def _snapshot(self, record: Invocation, *, visible: bool = False) -> InvocationSnapshot:
        return InvocationSnapshot(
            record.invocation_id,
            record.entry.target,
            record.action.name,
            record.state,
            record.admission,
            record.effect,
            record.cancellation,
            record.cancel_reason,
            record.error,
            record.basis,
            record.admission_record,
            copy.deepcopy(record.result) if visible else None,
            record.result is not None and not visible,
            record.version,
            not record.settled,
        )

    async def _observe(
        self, holder: str, reference: InvocationRef, wait: float, after_version: int | None
    ) -> InvocationSnapshot:
        record = self._lookup_call(holder, reference)
        duration = _budget(wait, self._limits.max_observe_wait, zero=True)
        if after_version is not None and (type(after_version) is not int or after_version < 0):
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        session = self._session(holder, active=False)
        if session.observers >= self._limits.max_observers:
            raise SchemeError(ErrorCode.BUSY)
        session.observers += 1
        try:
            deadline = self._loop.time() + duration
            while not record.settled and (after_version is None or record.version <= after_version):
                remaining = deadline - self._loop.time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(remaining):
                        await record.changed.wait()
                except TimeoutError:
                    break
            visible = False
            if record.result is not None and not self._closing:
                try:
                    decision = await self._authorize(
                        AccessRequest(
                            session.principal,
                            record.entry.target,
                            frozenset({record.action.name}),
                            AccessStage.RESULT,
                            None,
                            record.invocation_id,
                        )
                    )
                    visible = decision.allowed
                except SchemeError:
                    pass
            return self._snapshot(record, visible=visible)
        finally:
            session.observers -= 1

    async def _cancel(self, holder: str, reference: InvocationRef) -> CancelDisposition:
        record = self._lookup_call(holder, reference)
        if record.task is not None and record.task.done():
            self._settle(record)
        if record.settled:
            return CancelDisposition.ALREADY_FINISHED
        if not record.action.cancellable:
            return CancelDisposition.UNSUPPORTED
        self._request_cancel(record, ErrorCode.CANCELLED)
        return CancelDisposition.REQUESTED

    async def _release(self, holder: str, reference: ResourceRef | InvocationRef) -> ReleaseReport:
        if isinstance(reference, ResourceRef):
            self._resource(holder, reference, usable=False)
            del self._resources[holder, reference.slot]
        elif isinstance(reference, InvocationRef):
            self._session(holder, active=False)
            if reference.boot != self._boot or reference.holder != holder:
                raise SchemeError(ErrorCode.INVALID_REFERENCE)
            item = self._call_refs.get(reference.token)
            if item is None or item[0] != holder:
                raise SchemeError(ErrorCode.INVALID_REFERENCE)
            del self._call_refs[reference.token]
        else:
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        return ReleaseReport(True)

    async def _drain(
        self, records: list[Invocation], timeout: float, cancel: bool, *, controls: bool = False
    ) -> DrainReport:
        duration = _budget(timeout, self._limits.default_timeout, zero=True)
        tasks: set[asyncio.Future[Any]] = set()
        for record in records:
            if cancel:
                self._request_cancel(record, ErrorCode.CANCELLED)
            if record.task is not None and not record.task.done():
                tasks.add(record.task)
        if controls:
            for task in self._controls.values():
                if not task.done():
                    if not task.cancelling():
                        task.cancel()
                    tasks.add(task)
        if tasks:
            await asyncio.wait(tasks, timeout=duration)
        for record in records:
            self._settle(record)
            if not record.settled:
                self._request_cancel(record, ErrorCode.CANCELLED)
        return DrainReport(
            tuple(self._snapshot(item) for item in records if not item.settled),
            tuple(key for key, task in self._controls.items() if not task.done())
            if controls
            else (),
        )


class Session:
    """注入给消费者的会话；不提供发布、接线或切换身份的方法。"""

    def __init__(self, runtime: SchemeRuntime, holder: str) -> None:
        self._runtime = runtime
        self._holder = holder

    async def acquire(
        self,
        target: str,
        *,
        actions: frozenset[str],
        contract: str | None = None,
        contract_version: str | None = None,
        contract_digest: str | None = None,
    ) -> ResourceRef:
        """取得固定目标和动作上限的受限引用。"""
        return await self._runtime._acquire(
            self._holder, target, actions, contract, contract_version, contract_digest
        )

    async def submit(
        self,
        reference: ResourceRef,
        *,
        action: str,
        arguments: JsonObject | SchemeModel,
        request_key: str | None = None,
        timeout: float | None = None,
    ) -> InvocationRef:
        """有界提交；返回调用引用不代表已经通过最终业务接纳。"""
        return await self._runtime._submit(
            self._holder, reference, action, arguments, request_key, timeout
        )

    async def observe(
        self, reference: InvocationRef, *, wait: float = 0, after_version: int | None = None
    ) -> InvocationSnapshot:
        """查询或有限等待调用，超时不取消执行。"""
        return await self._runtime._observe(self._holder, reference, wait, after_version)

    async def cancel(self, reference: InvocationRef) -> CancelDisposition:
        """请求取消自己的调用，不保证回滚效果。"""
        return await self._runtime._cancel(self._holder, reference)

    async def release(self, reference: ResourceRef | InvocationRef) -> ReleaseReport:
        """释放自有引用，不撤销许可或取消已接纳调用。"""
        return await self._runtime._release(self._holder, reference)

    async def close(self, *, timeout: float = 1.0) -> DrainReport:
        """关闭本会话并取消自有调用，残余由运行 owner 持有。"""
        runtime = self._runtime
        state = runtime._session(self._holder, active=False)
        _budget(timeout, runtime._limits.default_timeout, zero=True)
        state.active = False
        records = [
            item
            for item in runtime._invocations.values()
            if item.holder == self._holder and not item.settled
        ]
        report = await runtime._drain(records, timeout, True)
        for key in list(runtime._resources):
            if key[0] == self._holder:
                del runtime._resources[key]
        for token, (holder, _) in list(runtime._call_refs.items()):
            if holder == self._holder:
                del runtime._call_refs[token]
        return report


class RuntimeManager:
    """由 Subject 等受信装配者持有的管理端口，不是认证服务。"""

    def __init__(self, runtime: SchemeRuntime) -> None:
        self._runtime = runtime

    def create_session(self, principal: Principal) -> Session:
        """接收已核定身份；不自行认证或授予许可。"""
        runtime = self._runtime
        runtime._guard(writable=True)
        for old, state in list(runtime._sessions.items()):
            if (
                not state.active
                and not state.observers
                and not any(
                    item.holder == old and not item.settled
                    for item in runtime._invocations.values()
                )
            ):
                del runtime._sessions[old]
                for key in list(runtime._generations):
                    if key[0] == old:
                        runtime._generations.pop(key)
                        runtime._resources.pop(key, None)
                for token, (owner, _) in list(runtime._call_refs.items()):
                    if owner == old:
                        del runtime._call_refs[token]
        if len(runtime._sessions) >= runtime._limits.max_sessions:
            raise SchemeError(ErrorCode.BUSY)
        holder = uuid4().hex
        runtime._sessions[holder] = SessionState(principal)
        return Session(runtime, holder)

    def publish(
        self,
        provider_id: str,
        spec: SchemeSpec,
        provider: Provider,
        *,
        concurrency: int = 1,
        pending_limit: int = 16,
    ) -> str:
        """发布已准备好的提供者；同名旧实例有在途时不能替换。"""
        runtime = self._runtime
        runtime._guard(writable=True)
        if (
            not provider_id
            or type(concurrency) is not int
            or concurrency <= 0
            or type(pending_limit) is not int
            or pending_limit < 0
        ):
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        previous = runtime._providers.get(provider_id)
        if previous is not None and previous.state is not ProviderState.WITHDRAWN:
            raise SchemeError(ErrorCode.PROVIDER_EXISTS)
        contract_key = spec.contract, spec.version
        if contract_key in runtime._contracts and runtime._contracts[contract_key] != spec.digest:
            raise SchemeError(ErrorCode.CONTRACT_MISMATCH)
        if (previous is None and len(runtime._providers) >= runtime._limits.max_providers) or (
            contract_key not in runtime._contracts
            and len(runtime._contracts) >= runtime._limits.max_contracts
        ):
            raise SchemeError(ErrorCode.BUSY)
        runtime._contracts[contract_key] = spec.digest
        incarnation = uuid4().hex
        runtime._providers[provider_id] = Registration(
            spec, provider, incarnation, concurrency, pending_limit, asyncio.Semaphore(concurrency)
        )
        return incarnation

    def bind(
        self,
        scope: AgentScope,
        kind: str,
        alias: str,
        provider_id: str,
        *,
        contract: str,
        contract_version: str,
        contract_digest: str | None = None,
    ) -> int:
        """安装确定 Agent 运行的绑定，核对提供者契约，不签发 grant。"""
        runtime = self._runtime
        runtime._guard(writable=True)
        if not isinstance(scope, AgentScope) or not re.fullmatch(r"[a-z][a-z0-9_-]*", alias):
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        registration = runtime._providers.get(provider_id)
        if registration is None:
            raise SchemeError(ErrorCode.NOT_FOUND)
        spec = registration.spec
        if (
            spec.kind != kind
            or spec.contract != contract
            or spec.version != contract_version
            or (contract_digest is not None and spec.digest != contract_digest)
        ):
            raise SchemeError(ErrorCode.CONTRACT_MISMATCH)
        key = scope, kind, alias
        if key not in runtime._bindings and len(runtime._bindings) >= runtime._limits.max_bindings:
            raise SchemeError(ErrorCode.BUSY)
        runtime._binding_sequence += 1
        revision = runtime._binding_sequence
        runtime._bindings[key] = Binding(
            provider_id, spec.contract, spec.version, spec.digest, revision
        )
        return revision

    def unbind(self, scope: AgentScope, kind: str, alias: str) -> None:
        """撤去确定 Agent 运行的局部绑定，不影响其他 Agent 或运行。"""
        runtime = self._runtime
        runtime._guard(writable=True)
        if not isinstance(scope, AgentScope):
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        runtime._bindings.pop((scope, kind, alias), None)

    async def withdraw(
        self, provider_id: str, *, timeout: float = 1.0, cancel: bool = False
    ) -> DrainReport:
        """停止新访问，有限收束；WITHDRAWN 仅表示运行导出退出。"""
        runtime = self._runtime
        runtime._guard()
        _budget(timeout, runtime._limits.default_timeout, zero=True)
        registration = runtime._providers.get(provider_id)
        if registration is None:
            raise SchemeError(ErrorCode.NOT_FOUND)
        registration.state = ProviderState.DRAINING
        records = [
            item
            for item in runtime._invocations.values()
            if item.registration is registration and not item.settled
        ]
        report = await runtime._drain(records, timeout, cancel)
        if not registration.inflight:
            registration.state = ProviderState.WITHDRAWN
        return report

    async def close(self, *, timeout: float = 1.0) -> DrainReport:
        """有界关闭整个实例；非合作调用及核定任务仍可见。"""
        runtime = self._runtime
        runtime._guard()
        _budget(timeout, runtime._limits.default_timeout, zero=True)
        runtime._closing = True
        for session in runtime._sessions.values():
            session.active = False
        for provider in runtime._providers.values():
            provider.state = (
                ProviderState.DRAINING if provider.inflight else ProviderState.WITHDRAWN
            )
        report = await runtime._drain(
            [item for item in runtime._invocations.values() if not item.settled],
            timeout,
            True,
            controls=True,
        )
        runtime._resources.clear()
        runtime._call_refs.clear()
        return report

    def diagnostics(self) -> JsonObject:
        """返回运行事实快照，不返回结果正文、handler 或可写状态。"""
        runtime = self._runtime
        runtime._guard()
        providers: dict[str, JsonValue] = {
            key: value.state.value for key, value in runtime._providers.items()
        }
        return {
            "boot": runtime._boot,
            "closing": runtime._closing,
            "inflight": runtime._inflight,
            "sessions": len(runtime._sessions),
            "bindings": len(runtime._bindings),
            "contracts": len(runtime._contracts),
            "resource_refs": len(runtime._resources),
            "invocation_refs": len(runtime._call_refs),
            "records": len(runtime._invocations),
            "request_keys": len(runtime._keys),
            "controls": len(runtime._controls),
            "providers": providers,
        }
