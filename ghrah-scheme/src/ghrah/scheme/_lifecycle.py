# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
# SPDX-License-Identifier: Apache-2.0
"""唯一资源 owner 的运行记录；协作回调不持锁，残余任务始终保留。"""

import asyncio
from uuid import uuid4

from ghrah.scheme._state import Registration, ResourceOwner
from ghrah.scheme.contracts import ProviderLifecycle
from ghrah.scheme.errors import ErrorCode, SchemeError
from ghrah.scheme.models import OwnerRef, OwnerSnapshot, OwnerState, ProviderState


class Lifecycles:
    def __init__(self, boot: str, maximum: int, timeout: float) -> None:
        self.boot = boot
        self.maximum = maximum
        self.timeout = timeout
        self.owners: dict[str, ResourceOwner] = {}

    def create(self, lifecycle: ProviderLifecycle) -> OwnerRef:
        if len(self.owners) >= self.maximum:
            raise SchemeError(ErrorCode.BUSY)
        if any(item.lifecycle is lifecycle for item in self.owners.values()):
            raise SchemeError(ErrorCode.PROVIDER_EXISTS, "同一资源 owner 不能重复登记")
        reference = OwnerRef(self.boot, uuid4().hex)
        owner = ResourceOwner(reference, lifecycle)
        self.owners[reference.token] = owner
        task = asyncio.create_task(self._prepare(owner), name=f"ghrah.prepare:{reference.token}")
        owner.prepare_task = task
        owner.timer = asyncio.get_running_loop().call_later(
            self.timeout, self._prepare_timeout, owner
        )
        task.add_done_callback(lambda completed: self._prepared(owner, completed))
        return reference

    def _prepared(self, owner: ResourceOwner, task: asyncio.Task[None]) -> None:
        if owner.timer is not None:
            owner.timer.cancel()
        if task.cancelled():
            owner.error = owner.error or ErrorCode.CANCELLED
            if not owner.stopping:
                owner.state = OwnerState.FAILED
                self._apply(owner, ProviderState.FAILED)
        else:
            task.exception()
        self.advance(owner)

    def get(self, reference: OwnerRef) -> ResourceOwner:
        if not isinstance(reference, OwnerRef) or reference.boot != self.boot:
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        owner = self.owners.get(reference.token)
        if owner is None or owner.reference != reference:
            raise SchemeError(ErrorCode.INVALID_REFERENCE)
        return owner

    def attach(self, owner: ResourceOwner, registration: Registration) -> None:
        if owner.stopping or owner.state not in {OwnerState.PREPARING, OwnerState.READY}:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        registration.owner = owner
        registration.state = (
            ProviderState.READY if owner.state is OwnerState.READY else ProviderState.PREPARING
        )
        owner.exports.append(registration)

    def _prepare_timeout(self, owner: ResourceOwner) -> None:
        task = owner.prepare_task
        if task is not None and not task.done():
            owner.state = OwnerState.FAILED
            owner.error = ErrorCode.DEADLINE_EXCEEDED
            self._apply(owner, ProviderState.FAILED)
            self._cancel_once(task)

    async def _prepare(self, owner: ResourceOwner) -> None:
        try:
            await owner.lifecycle.prepare()
        except asyncio.CancelledError:
            owner.error = owner.error or ErrorCode.CANCELLED
            if not owner.stopping:
                owner.state = OwnerState.FAILED
                self._apply(owner, ProviderState.FAILED)
        except Exception:
            owner.error = ErrorCode.PROVIDER_FAILED
            if not owner.stopping:
                owner.state = OwnerState.FAILED
                self._apply(owner, ProviderState.FAILED)
        else:
            if not owner.stopping and owner.state is OwnerState.PREPARING and owner.error is None:
                owner.state = OwnerState.READY
                self._apply(owner, ProviderState.READY)
        finally:
            if owner.timer is not None:
                owner.timer.cancel()

    @staticmethod
    def _apply(owner: ResourceOwner, state: ProviderState) -> None:
        for registration in owner.exports:
            if registration.state not in {ProviderState.DRAINING, ProviderState.WITHDRAWN}:
                registration.state = state

    @staticmethod
    def _cancel_once(task: asyncio.Task[None]) -> None:
        if not task.done() and not task.cancelling():
            task.cancel()

    def finish_export(self, registration: Registration) -> None:
        if registration.state is not ProviderState.DRAINING or registration.inflight:
            return
        owner = registration.owner
        if owner is None:
            registration.state = ProviderState.WITHDRAWN
            return
        others_active = any(
            item is not registration
            and item.state not in {ProviderState.DRAINING, ProviderState.WITHDRAWN}
            for item in owner.exports
        )
        if others_active:
            registration.state = ProviderState.WITHDRAWN
        self.advance(owner)

    def advance(self, owner: ResourceOwner) -> None:
        if owner.exports and all(
            item.state in {ProviderState.DRAINING, ProviderState.WITHDRAWN}
            for item in owner.exports
        ):
            owner.stopping = True
        if not owner.stopping or owner.close_task is not None:
            return
        if owner.prepare_task is not None and not owner.prepare_task.done():
            return
        if any(item.inflight for item in owner.exports):
            owner.state = OwnerState.DRAINING
            return
        owner.state = OwnerState.CLOSING
        owner.close_task = asyncio.create_task(
            self._close(owner), name=f"ghrah.close:{owner.reference.token}"
        )
        owner.close_task.add_done_callback(lambda completed: self._closed(owner, completed))

    @staticmethod
    def _closed(owner: ResourceOwner, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            owner.state = OwnerState.CLOSE_FAILED
            owner.error = ErrorCode.CANCELLED
        else:
            task.exception()

    async def _close(self, owner: ResourceOwner) -> None:
        try:
            await owner.lifecycle.aclose()
        except asyncio.CancelledError:
            owner.state = OwnerState.CLOSE_FAILED
            owner.error = ErrorCode.CANCELLED
        except Exception:
            owner.state = OwnerState.CLOSE_FAILED
            owner.error = ErrorCode.PROVIDER_FAILED
        else:
            owner.state = OwnerState.CLOSED
            owner.error = None
            for registration in owner.exports:
                registration.state = ProviderState.WITHDRAWN

    def stop_all(self) -> None:
        for owner in self.owners.values():
            if owner.state is OwnerState.CLOSED:
                continue
            owner.stopping = True
            for registration in owner.exports:
                registration.state = ProviderState.DRAINING
            self.advance(owner)

    def residuals(self, owners: list[ResourceOwner]) -> tuple[OwnerSnapshot, ...]:
        return tuple(
            OwnerSnapshot(
                item.reference,
                item.state,
                item.error,
                any(
                    task is not None and not task.done()
                    for task in (item.prepare_task, item.close_task)
                )
                or any(export.inflight for export in item.exports),
            )
            for item in owners
            if item.stopping and item.state is not OwnerState.CLOSED
        )

    def cancel_pending(self, owners: list[ResourceOwner]) -> None:
        for owner in owners:
            if not owner.stopping:
                continue
            for task in (owner.prepare_task, owner.close_task):
                if task is not None:
                    self._cancel_once(task)

    def health(self, registration: Registration, state: ProviderState) -> None:
        if state not in {ProviderState.READY, ProviderState.UNAVAILABLE, ProviderState.FAILED}:
            raise SchemeError(ErrorCode.INVALID_ARGUMENT)
        if registration.state in {
            ProviderState.DRAINING,
            ProviderState.WITHDRAWN,
            ProviderState.PREPARING,
        }:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        owner = registration.owner
        if registration.state is ProviderState.FAILED and state is not ProviderState.FAILED:
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        if owner is not None:
            if owner.stopping or owner.state in {
                OwnerState.CLOSING,
                OwnerState.CLOSED,
                OwnerState.CLOSE_FAILED,
            }:
                raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
            owner.state = {
                ProviderState.READY: OwnerState.READY,
                ProviderState.UNAVAILABLE: OwnerState.UNAVAILABLE,
                ProviderState.FAILED: OwnerState.FAILED,
            }[state]
            self._apply(owner, state)
        else:
            registration.state = state

    def retry(self, owner: ResourceOwner) -> None:
        if (
            owner.state is not OwnerState.CLOSE_FAILED
            or owner.close_task is None
            or not owner.close_task.done()
        ):
            raise SchemeError(ErrorCode.PROVIDER_UNAVAILABLE)
        owner.close_task = None
        owner.error = None
        self.advance(owner)
