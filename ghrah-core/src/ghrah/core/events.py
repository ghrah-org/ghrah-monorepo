# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""ghrah-core 事件定义。

与 ghrah-protocol 的 EventType 对齐，定义 Core 侧产生的事件类型。
Core 产生的事件通过注入的 EventPublisher 推送到宿主（Subject），
Subject 再将相关事件转发给 Observer。

事件流方向：
    Core → Subject（确认/记录）→ Observer（渲染/审批）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "CoreEventType",
    "CoreEvent",
    "HITLRequestEvent",
    "ActionChainUpdatedEvent",
    "AgentErrorEvent",
    "AgentResponseEvent",
    "ContextUsageUpdatedEvent",
    "SessionCreatedEvent",
    "SessionActivatedEvent",
    "SessionArchivedEvent",
    "SessionDeletedEvent",
    "BranchCreatedEvent",
    "BranchActivatedEvent",
    "BranchArchivedEvent",
    "BranchDeletedEvent",
]


class CoreEventType(str, Enum):  # noqa: UP042
    """Core 产生的事件类型。

    与 ghrah-protocol 的 EventType 对齐：
    - HITL_REQUEST ↔ EventType.HITL_REQUEST
    - HITL_RESOLVED ↔ EventType.HITL_RESOLVED
    - ACTION_CHAIN_UPDATED ↔ EventType.ACTION_CHAIN_UPDATED
    - AGENT_ERROR ↔ EventType.AGENT_ERROR
    - AGENT_RESPONSE ↔ EventType.AGENT_RESPONSE
    - SESSION_CREATED ↔ EventType.SESSION_CREATED
    - SESSION_ACTIVATED ↔ EventType.SESSION_ACTIVATED
    - SESSION_ARCHIVED ↔ EventType.SESSION_ARCHIVED
    - SESSION_DELETED ↔ EventType.SESSION_DELETED
    """

    HITL_REQUEST = "hitl_request"
    HITL_RESOLVED = "hitl_resolved"
    ACTION_CHAIN_UPDATED = "action_chain_updated"
    AGENT_ERROR = "agent_error"
    AGENT_RESPONSE = "agent_response"
    CONTEXT_USAGE_UPDATED = "context_usage_updated"
    SESSION_CREATED = "session_created"
    SESSION_ACTIVATED = "session_activated"
    SESSION_ARCHIVED = "session_archived"
    SESSION_DELETED = "session_deleted"
    BRANCH_CREATED = "branch_created"
    BRANCH_ACTIVATED = "branch_activated"
    BRANCH_ARCHIVED = "branch_archived"
    BRANCH_DELETED = "branch_deleted"


@dataclass
class CoreEvent:
    """Core 事件基类。

    Attributes:
        event_type: 事件类型
        agent_name: 产生事件的 Agent 名称
        data: 事件附加数据
    """

    event_type: CoreEventType
    agent_name: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class HITLRequestEvent(CoreEvent):
    """HITL 请求事件。

    Agent 需要人类批准时产生此事件，经 Core Server 推送给 Subject。
    Subject 的 HITLNotary 记录后广播给 Observer 渲染审批界面。

    Attributes:
        ability_name: 请求 HITL 的 Ability 名称
        tool_call: 工具调用参数
        context: 附加上下文（含 tool_call_id）
    """

    event_type: CoreEventType = field(default=CoreEventType.HITL_REQUEST, init=False)
    ability_name: str = ""
    tool_call: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    promise_id: str = ""
    """审批响应定位键：Observer 凭此回发 hitl_response"""
    reason: str = ""
    """触发审批的原因（Hook 拦截说明，如 "Command requires approval: pnpm ..."）"""


@dataclass
class HITLResolvedEvent(CoreEvent):
    """HITL 审批终结事件。

    审批等待终结（approved / rejected / timeout）时发布，Observer 据此清除
    审批卡片——超时或另端已处理时前端不再悬挂。

    Attributes:
        promise_id: 对应请求的 promise 标识
        ability_name: 请求 HITL 的 Ability 名称
        tool_call_id: 工具调用 ID
        status: 终态（"approved" / "rejected" / "timeout"）
    """

    event_type: CoreEventType = field(default=CoreEventType.HITL_RESOLVED, init=False)
    promise_id: str = ""
    ability_name: str = ""
    tool_call_id: str = ""
    status: str = ""


@dataclass
class ActionChainUpdatedEvent(CoreEvent):
    """ActionChain 变更事件。

    ContextManager commit/rollback 时产生此事件。
    Subject 的 ActionChainLedger 持久化后广播给 Observer 渲染 Timeline。

    Attributes:
        node: 变更的节点数据
        branch: 本次 commit 后的权威 Branch 快照（含 head_node_id）
    """

    event_type: CoreEventType = field(default=CoreEventType.ACTION_CHAIN_UPDATED, init=False)
    node: dict[str, Any] = field(default_factory=dict)
    branch: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentErrorEvent(CoreEvent):
    """Agent 错误事件。

    Agent 驱动循环发生错误时产生此事件。

    Attributes:
        error: 错误信息
    """

    event_type: CoreEventType = field(default=CoreEventType.AGENT_ERROR, init=False)
    error: str = ""


@dataclass
class AgentResponseEvent(CoreEvent):
    """Agent 响应事件。

    Agent 完成任务产生最终回复时产生此事件。

    Attributes:
        content: 响应内容（纯文本，作为向后兼容 fallback）
        content_blocks: 结构化内容块列表，保留 reasoning/text 等类型区分。
            每个元素为 block_to_dict() 序列化后的字典。
            为 None 时表示仅有纯文本 content，前端应降级使用 content 字段。
        message_type: 消息类型（result/error/delegate）
        metadata: 附加元数据
    """

    event_type: CoreEventType = field(default=CoreEventType.AGENT_RESPONSE, init=False)
    content: str = ""
    content_blocks: list[dict[str, Any]] | None = None
    message_type: str = "result"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextUsageUpdatedEvent(CoreEvent):
    """上下文占用状态更新事件。

    Agent 每次 LLM 调用前后产生（双相位）：
    - pre_call（仅配置 window 的 agent）：发送前占用锚点口径；
    - post_call（所有 agent）：响应后真实 input_tokens 口径。

    真实 usage 唯一权威：post_call 的 occupied_tokens 即本次调用的
    真实 input_tokens（basis="real"）；pre_call 取占用锚点（可能为
    None，如首次调用/head 变更后，basis="anchor"）。

    Attributes:
        phase: 发布相位 "pre_call" | "post_call"
        occupied_tokens: 占用 token 数（口径随 phase）
        basis: occupied_tokens 计量口径 "anchor" | "real"
        budget_tokens: 运营者声明预算（无 WindowManager 时为 0）
        budget_source: 预算来源 "declared" | "agentconf" | "model_table" | "default" |
            "vendor"（无 WindowManager 时为 None）
        compact_threshold: 压缩阈值比率（未配置时为 None）
        real_input_tokens: 本次调用真实 input_tokens（pre_call 为 None）
        real_output_tokens: 本次调用真实 output_tokens（pre_call 为 None）
        real_cache_read_tokens: 本次调用缓存命中读 token 数（pre_call 或
            厂商未上报时为 None）
        real_cache_write_tokens: 本次调用缓存写入 token 数（pre_call 或
            厂商未上报时为 None）
        compaction: 本轮压缩决策记录（未配置窗口时为 None）
        iteration: 当前迭代号
    """

    event_type: CoreEventType = field(default=CoreEventType.CONTEXT_USAGE_UPDATED, init=False)
    phase: str = "pre_call"
    occupied_tokens: int | None = None
    basis: str = "anchor"
    budget_tokens: int = 0
    budget_source: str | None = None
    compact_threshold: float | None = None
    real_input_tokens: int | None = None
    real_output_tokens: int | None = None
    real_cache_read_tokens: int | None = None
    real_cache_write_tokens: int | None = None
    compaction: dict[str, Any] | None = None
    iteration: int | None = None


@dataclass
class SessionCreatedEvent(CoreEvent):
    """独立 Root Session 创建事件。"""

    event_type: CoreEventType = field(default=CoreEventType.SESSION_CREATED, init=False)
    session: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionActivatedEvent(CoreEvent):
    """独立 Root Session 激活事件。"""

    event_type: CoreEventType = field(default=CoreEventType.SESSION_ACTIVATED, init=False)
    session: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionArchivedEvent(CoreEvent):
    """Session 归档事件。

    Attributes:
        session_id: 被归档的 Session ID
    """

    event_type: CoreEventType = field(default=CoreEventType.SESSION_ARCHIVED, init=False)
    session_id: str = ""


@dataclass
class SessionDeletedEvent(CoreEvent):
    """Session 删除事件。

    Attributes:
        session_id: 被删除的 Session ID
    """

    event_type: CoreEventType = field(default=CoreEventType.SESSION_DELETED, init=False)
    session_id: str = ""


@dataclass
class BranchCreatedEvent(CoreEvent):
    """Branch 创建事件。"""

    event_type: CoreEventType = field(default=CoreEventType.BRANCH_CREATED, init=False)
    branch: dict[str, Any] = field(default_factory=dict)


@dataclass
class BranchActivatedEvent(CoreEvent):
    """Branch 激活事件。"""

    event_type: CoreEventType = field(default=CoreEventType.BRANCH_ACTIVATED, init=False)
    branch: dict[str, Any] = field(default_factory=dict)


@dataclass
class BranchArchivedEvent(CoreEvent):
    """Branch 归档事件。"""

    event_type: CoreEventType = field(default=CoreEventType.BRANCH_ARCHIVED, init=False)
    session_id: str = ""
    branch_id: str = ""


@dataclass
class BranchDeletedEvent(CoreEvent):
    """Branch 删除事件。"""

    event_type: CoreEventType = field(default=CoreEventType.BRANCH_DELETED, init=False)
    session_id: str = ""
    branch_id: str = ""
