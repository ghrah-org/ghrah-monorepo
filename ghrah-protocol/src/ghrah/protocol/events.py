# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""事件契约：只带资源引用，不带 per-type payload。

事件是**通知**，不是权威。消费方收到后应自回端口按 id 读权威数据；
``resource_etag`` 让它跳过陈旧事件。

这条约束的直接后果：消费方**必须能只靠读资源重建状态**，
所以事件里没有、也不应该有业务字段。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "EventEnvelope",
    "EventTypeV1",
    "ResourceKind",
    "ResourceRef",
]

type ResourceKind = Literal[
    "agent",
    "node",
    "session",
    "branch",
    "delivery",
    "ability",
    "hitl",
    "system",
]


class EventTypeV1(StrEnum):
    """事件类型（10 种 / 3 个动词）。

    「启动 / 停止 / 出错」不再各自成事件——都是 ``agent.updated``，
    具体状态由读 ``GET /api/v1/agents/{id}`` 得到。
    同理 HITL 请求走 ``agent.updated`` + ``resource.kind == "hitl"``。
    """

    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_DELETED = "agent.deleted"
    DELIVERY_UPDATED = "delivery.updated"
    ABILITY_UPDATED = "ability.updated"
    CONTEXT_UPDATED = "context.updated"
    SYSTEM_UPDATED = "system.updated"


class ResourceRef(BaseModel):
    """事件指向的资源引用。

    Attributes:
        kind: 资源种类（区分同一 URL 空间下的不同资源）
        id: 稳定标识（客户端视为不透明串）
        url: 读取该资源的路径（不含 query）
    """

    model_config = ConfigDict(extra="forbid")

    kind: ResourceKind
    id: str
    url: str


class EventEnvelope(BaseModel):
    """事件信封。

    Attributes:
        event_id: 事件标识
        seq: 同 scope 内单调递增序号（消费方存 last_seq 做断档检测）
        type: 事件类型
        scope_ref: 作用域（当前实现等于 ``project_id``）
        occurred_at: 发生时间（RFC3339 UTC）
        resource: 受影响资源引用
        resource_etag: 资源在事件发生时的 ETag，用于跳过陈旧事件
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    seq: int
    type: EventTypeV1
    scope_ref: str
    occurred_at: str
    resource: ResourceRef
    resource_etag: str = ""
