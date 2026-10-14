# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""显式原子接收的迭代提交作用域，随 Runner 请求上下文传播。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ghrah.context.persistence.changes import ContextChanges
from ghrah.core.message import AgentMessage

if TYPE_CHECKING:
    from ghrah.context.manager import ContextManager

IterationCommitter = Callable[[ContextChanges], Awaitable[None]]
DeliveryFinisher = Callable[[AgentMessage], Awaitable[None]]
_SCOPE: ContextVar[
    tuple[ContextManager, IterationCommitter, Callable[[], None] | None, DeliveryFinisher | None]
    | None
] = ContextVar("ghrah_iteration_commit", default=None)


@contextmanager
def durable_iteration_scope(
    manager: ContextManager,
    committer: IterationCommitter,
    rollback: Callable[[], None] | None = None,
    finish: DeliveryFinisher | None = None,
) -> Iterator[None]:
    """为一个接收请求安装持久提交器，退出时恢复原作用域。"""
    token = _SCOPE.set((manager, committer, rollback, finish))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def get_iteration_committer(manager: ContextManager) -> IterationCommitter | None:
    """仅为匹配的上下文实例返回提交器，避免嵌套 agent 串用。"""
    scope = _SCOPE.get()
    return scope[1] if scope is not None and scope[0] is manager else None


def discard_iteration_effects(manager: ContextManager) -> None:
    """本轮回滚或取消时丢弃尚未提交的出站变更。"""
    scope = _SCOPE.get()
    if scope is not None and scope[0] is manager and scope[2] is not None:
        scope[2]()


def get_delivery_finisher(manager: ContextManager) -> DeliveryFinisher | None:
    """返回匹配上下文的最终结果持久收尾入口。"""
    scope = _SCOPE.get()
    return scope[3] if scope is not None and scope[0] is manager else None


def clear_durable_iteration_scope() -> None:
    """在新后台任务的复制上下文中清空迭代作用域，不影响创建方。"""
    _SCOPE.set(None)
