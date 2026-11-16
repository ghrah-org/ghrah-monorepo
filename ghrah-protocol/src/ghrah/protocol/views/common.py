# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""跨模块共用视图：分页、长动作、空回执、id 信封。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ghrah.protocol.problem import Problem

__all__ = [
    "AckView",
    "AgentIdView",
    "BranchIdView",
    "DeliveryIdView",
    "MessageIdListView",
    "MessageIdView",
    "NodeIdView",
    "Operation",
    "OperationState",
    "OperationView",
    "Page",
    "SessionIdView",
]


class Page[T](BaseModel):
    """列表页。

    Attributes:
        items: 本页条目
        next_cursor: 下一页游标；``None`` 表示没有下一页
        has_more: 是否还有更多
    """

    items: list[T] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class OperationState(StrEnum):
    """长动作状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Operation(BaseModel):
    """长动作句柄。

    返回它的能力表示「结果不在本次调用内产生」，消费方可以选择等或不等。

    Attributes:
        operation_id: 句柄
        state: 当前状态
        result: 成功后的结果（形状由产生它的能力定义）
        error: 失败原因
        created_at: 创建时间（RFC3339）
        updated_at: 最近更新时间（RFC3339）
    """

    operation_id: str
    state: OperationState = OperationState.PENDING
    result: dict[str, Any] | None = None
    error: Problem | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def terminal(self) -> bool:
        """是否已到终态。"""
        return self.state in (OperationState.SUCCEEDED, OperationState.FAILED)


class OperationView(Operation):
    """``operations.get`` 的出参（与 :class:`Operation` 同形，独立命名以便演进）。"""


class AckView(BaseModel):
    """空出参的占位。

    ``invoke`` 要求出参是 DTO，但对应能力在 Port 层返回 ``None``；
    门面用 ``AckView`` 接收后丢弃。没有它，那些能力无法登记进清单。
    """

    ok: bool = True


class AgentIdView(BaseModel):
    """``agents.create`` 的出参信封：Port 层拆壳后返回裸 id。"""

    agent_id: str


class SessionIdView(BaseModel):
    """``sessions.create`` 的出参信封。"""

    session_id: str


class BranchIdView(BaseModel):
    """``branches.create`` 的出参信封。"""

    branch_id: str


class MessageIdView(BaseModel):
    """``messages.append`` 的出参信封。"""

    message_id: str


class DeliveryIdView(BaseModel):
    """``messages.delegate`` 的出参信封。"""

    delivery_id: str


class NodeIdView(BaseModel):
    """``sessions.head`` 的出参信封。"""

    node_id: str


class MessageIdListView(BaseModel):
    """``messages.broadcast`` 的出参信封。"""

    message_ids: list[str] = Field(default_factory=list)
