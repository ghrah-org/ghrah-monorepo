# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""单 agent 的 FIFO 执行入口，保留请求返回值和取消语义。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")
_CURRENT: ContextVar[AgentRunner | None] = ContextVar("ghrah_agent_runner", default=None)


def detached_runner_context() -> Context:
    """为后台投递清除同步等待关系，保留其他上下文变量。"""
    context = copy_context()
    context.run(_CURRENT.set, None)
    return context


@dataclass
class _Work(Generic[_T]):
    operation: Callable[[], Awaitable[_T]]
    future: asyncio.Future[_T]
    context: Context


class AgentRunner:
    """串行执行接收与空闲压缩操作，拒绝 agent 间同步循环等待。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self._pending: deque[_Work[Any]] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._operation: asyncio.Task[Any] | None = None
        self._current: _Work[Any] | None = None
        self._waiting: dict[AgentRunner, int] = {}
        self._closed = False

    @property
    def busy(self) -> bool:
        """返回是否存在运行中或等待中的执行。"""
        return self._worker is not None

    async def run(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        """排队执行，返回该操作自己的结果；取消只影响该操作。"""
        if self._closed:
            raise RuntimeError(f"Agent runner '{self._name}' is closed")
        parent = _CURRENT.get()
        if parent is not None and not parent.busy:
            parent = None
        if parent is not None:
            if self._reaches(parent, set()):
                raise RuntimeError(
                    f"Synchronous agent wait cycle: '{parent._name}' -> '{self._name}'"
                )
            parent._waiting[self] = parent._waiting.get(self, 0) + 1
        work = _Work(operation, asyncio.get_running_loop().create_future(), copy_context())
        self._pending.append(work)
        if self._worker is None:
            self._worker = asyncio.create_task(self._drain(), context=detached_runner_context())
        try:
            return await work.future
        except asyncio.CancelledError:
            if self._current is work and self._operation is not None:
                task = self._operation
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            if parent is not None:
                count = parent._waiting[self] - 1
                if count:
                    parent._waiting[self] = count
                else:
                    del parent._waiting[self]

    async def close(self) -> None:
        """停止当前执行和待执行请求，关闭后拒绝新请求。"""
        if _CURRENT.get() is self:
            raise RuntimeError("Cannot stop an agent runner from its own execution")
        self._closed = True
        for work in self._pending:
            work.future.cancel()
        self._pending.clear()
        if self._worker is not None:
            task = self._worker
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _reaches(self, target: AgentRunner, seen: set[AgentRunner]) -> bool:
        if self is target:
            return True
        if self in seen:
            return False
        seen.add(self)
        return any(runner._reaches(target, seen) for runner in self._waiting)

    async def _invoke(self, work: _Work[_T]) -> _T:
        token = _CURRENT.set(self)
        try:
            return await work.operation()
        finally:
            _CURRENT.reset(token)

    async def _drain(self) -> None:
        try:
            while self._pending:
                work = self._pending.popleft()
                if work.future.cancelled():
                    continue
                self._current = work
                self._operation = asyncio.create_task(self._invoke(work), context=work.context)
                try:
                    result = await self._operation
                except asyncio.CancelledError:
                    work.future.cancel()
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                except Exception as exc:
                    if not work.future.done():
                        work.future.set_exception(exc)
                else:
                    if not work.future.done():
                        work.future.set_result(result)
                finally:
                    self._current = None
                    self._operation = None
        finally:
            self._worker = None
