# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""Agent 作用域事件的归属契约与补齐逻辑（Subject 侧）。

Core 只报告「哪个 Agent 发生了什么」，不再携带 Project 归属——Project 是 Subject
的组织方式，Core 不必知道。Subject 在把 Core 事件转发给 Observer 之前，用自身的
权威映射（ProjectStore 的 project → agents）补回 ``project_id``，从而保持 Observer
wire 形状不变（Observer 继续按 Project 分桶）。

事件类型清单原先在 ``ghrah.protocol.routing``（Core 与 Subject 共用），M1 迁移后
归 Subject：只有转发侧需要"哪些事件必须带归属"这条规则。
"""

from __future__ import annotations

from typing import Any

from ghrah.protocol.enums import EventType

#: 必须携带非空 ``project_id`` + ``agent_id`` 的事件类型。
#: 转发侧（Subject EventBus）补齐失败时丢弃并计数，不猜测归属。
AGENT_SCOPED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.AGENT_SPAWNED.value,
        EventType.AGENT_TERMINATED.value,
        EventType.AGENT_RESPONSE.value,
        EventType.AGENT_ERROR.value,
        EventType.ACTION_CHAIN_UPDATED.value,
        EventType.ABILITY_RESULT.value,
        EventType.ABILITY_TREE_CHANGED.value,
        EventType.HITL_REQUEST.value,
        EventType.HITL_RESOLVED.value,
        EventType.TURN_OPENED.value,
        EventType.TURN_WAITING_HITL.value,
        EventType.TURN_CLOSED.value,
        EventType.CONTEXT_USAGE_UPDATED.value,
        EventType.SESSION_CREATED.value,
        EventType.SESSION_ACTIVATED.value,
        EventType.SESSION_ARCHIVED.value,
        EventType.SESSION_DELETED.value,
        EventType.SESSION_LIST_RESULT.value,
        EventType.BRANCH_CREATED.value,
        EventType.BRANCH_ACTIVATED.value,
        EventType.BRANCH_ARCHIVED.value,
        EventType.BRANCH_DELETED.value,
        EventType.BRANCH_LIST_RESULT.value,
    }
)


def fill_attribution(payload: dict[str, Any], project_id: str) -> None:
    """就地补齐事件归属：顶层 ``project_id`` 与嵌套 ``session.project_id``。

    只在字段为空时写入——Core 侧残留的空串（协议 payload 默认 ``""``）视为缺失；
    已有非空值的 payload 原样保留（Subject 自己的域事件不经过这里）。

    Args:
        payload: 事件载荷（原地修改）
        project_id: 由 Subject 权威映射解析出的 Project 标识
    """
    if not payload.get("project_id"):
        payload["project_id"] = project_id
    session = payload.get("session")
    if isinstance(session, dict) and not session.get("project_id"):
        session["project_id"] = project_id
