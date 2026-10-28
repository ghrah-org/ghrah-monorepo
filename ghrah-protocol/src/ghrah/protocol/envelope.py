# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""消息信封、type → Payload 注册表与反序列化入口。"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from ghrah.protocol.enums import ClientType, CommandType, EventType, SystemType
from ghrah.protocol.payloads import (
    AbilityResultPayload,
    ActionChainUpdatedPayload,
    AgentCompactContextPayload,
    AgentErrorPayload,
    AgentResetPayload,
    AgentResponsePayload,
    AgentSpawnedPayload,
    AgentTerminatedPayload,
    BranchActivatePayload,
    BranchArchivePayload,
    BranchCreatePayload,
    BranchDeletePayload,
    BranchEventPayload,
    BranchLifecyclePayload,
    BranchListPayload,
    BranchListResultPayload,
    BroadcastMessagePayload,
    ClusterStatusPayload,
    ContextUsageUpdatedPayload,
    CreateWorkspacePayload,
    DelegatePayload,
    DestroyWorkspacePayload,
    ExecuteAbilityPayload,
    GetAgentInfoPayload,
    GetChainHistoryPayload,
    HealthCheckPayload,
    HealthStatusPayload,
    HITLRequestPayload,
    HITLResolvedPayload,
    HITLResponsePayload,
    InitClusterPayload,
    ListAgentsPayload,
    ListClustersPayload,
    ManifestAbilityEventPayload,
    ManifestAgentEventPayload,
    PluginCrashedPayload,
    PluginLifecyclePayload,
    PluginNegotiatedPayload,
    PluginNegotiatePayload,
    ProjectAddAgentPayload,
    ProjectAgentEventPayload,
    ProjectCreatePayload,
    ProjectDeletePayload,
    ProjectEventPayload,
    ProjectIdPayload,
    ProjectLifecyclePayload,
    ProjectLinkTaskPayload,
    ProjectListPayload,
    ProjectRemoveAgentPayload,
    ProjectSetRecoveryPayload,
    ProjectUnlinkTaskPayload,
    ProjectUpdatePayload,
    ReconcileNowPayload,
    ReconcileReportPayload,
    ReconcileStatusPayload,
    RegisterAbilityPayload,
    RoomCreatePayload,
    RoomDeletedEventPayload,
    RoomDeletePayload,
    RoomEventPayload,
    RoomGetLogPayload,
    RoomIdPayload,
    RoomJoinPayload,
    RoomLeavePayload,
    RoomLifecyclePayload,
    RoomListPayload,
    RoomLogEventPayload,
    RoomMemberEventPayload,
    RoomSendPayload,
    RoomUpdatePayload,
    SendMessagePayload,
    SessionActivatedPayload,
    SessionActivatePayload,
    SessionArchivedPayload,
    SessionArchivePayload,
    SessionCreatedPayload,
    SessionCreatePayload,
    SessionDeletedPayload,
    SessionDeletePayload,
    SessionListPayload,
    SessionListResultPayload,
    ShutdownClusterPayload,
    SpawnAgentPayload,
    SubscribePayload,
    TaskAssignPayload,
    TaskBlockPayload,
    TaskCancelPayload,
    TaskClaimEventPayload,
    TaskClaimListPayload,
    TaskCompletePayload,
    TaskCreatePayload,
    TaskDeletePayload,
    TaskDumpPayload,
    TaskEventPayload,
    TaskFailPayload,
    TaskIdPayload,
    TaskListPayload,
    TaskSubmitCompletionPayload,
    TaskUpdatePayload,
    TaskVerifyPayload,
    TerminateAgentPayload,
    UnregisterAbilityPayload,
    UnsubscribePayload,
    WorkspaceCreatedPayload,
    WorkspaceDestroyedPayload,
    WorkspaceDiffPayload,
    WorkspaceGetPayload,
    WorkspaceListPayload,
    WorkspaceRegisterPayload,
    WorkspaceRollbackPayload,
    WorkspaceRolledBackPayload,
    WorkspaceSnapshotCreatedPayload,
    WorkspaceSnapshotPayload,
    WorkspaceStatusPayload,
)
from ghrah.protocol.payloads.entity import (
    AgentEntityCreatePayload,
    AgentEntityListPayload,
    AgentEntityPayload,
)

T = TypeVar("T", bound=BaseModel)


# ─── 信封模型 ───


class Envelope(BaseModel):
    """WebSocket 消息信封（非泛型，宽类型）。

    payload 在 wire 层保持 Any（dict | BaseModel 实例 | None）。
    类型安全通过 PAYLOAD_MAP + envelope_from_dict（入口收窄）
    + expect_payload（handler 边界断言）实现，不由 Pydantic 泛型承担。

    type 保持 str（不收紧为 MessageType union），以接受未知 type
    字符串、保持前向兼容；由 known_type() / as_command_type() 判断。

    Attributes:
        type: 消息类型字符串（命令/事件/系统）
        payload: 消息载荷（dict / BaseModel 实例 / None）
        request_id: 请求ID，用于关联命令和响应
        timestamp: 消息时间戳（Unix时间戳）
        client_type: 发送方客户端类型（subject/observer），用于连接管理
        seq_id: 单调递增序号（广播时填充）
    """

    type: str
    payload: Any = Field(default_factory=dict)
    request_id: str | None = None
    timestamp: float | None = None
    client_type: ClientType | None = None
    seq_id: int | None = None

    def model_dump_with_timestamp(self) -> dict[str, Any]:
        """序列化时自动填充时间戳。"""
        data = self.model_dump()
        if data.get("timestamp") is None:
            data["timestamp"] = time.time()
        return data

    # ── 类型辅助（不改变 wire，仅消费侧便利）──

    def known_type(self) -> bool:
        """type 是否在 PAYLOAD_MAP 中（已知命令/事件）。"""
        return self.type in PAYLOAD_MAP

    def as_command_type(self) -> CommandType | None:
        """尝试解析为 CommandType（未知返回 None，不抛）。"""
        try:
            return CommandType(self.type)
        except ValueError:
            return None

    def as_event_type(self) -> EventType | None:
        """尝试解析为 EventType（未知返回 None，不抛）。"""
        try:
            return EventType(self.type)
        except ValueError:
            return None

    def as_system_type(self) -> SystemType | None:
        """尝试解析为 SystemType（未知返回 None，不抛）。"""
        try:
            return SystemType(self.type)
        except ValueError:
            return None  # ─── type → Payload 注册表 ───


#
# 仅登记 schema 正确且当前有消费方的 payload。
# - persist_*（13 个）不登记：PersistSavePayload 等 schema 与实际 wire shape 不符，
#   留待 Subject 插件化阶段补齐真实 payload。此处 payload 保持裸 dict 透传。
# - command_result / error / ping / pong 不登记：welcome 包手写 payload 与
#   CommandResultPayload schema 不符。此处 payload 保持裸 dict。
# - plugin_* / task 归因切面例外：契约先行（类型期即登记，消费方后续接线），
#   schema 与 wire 已定且经双侧一致性测试覆盖，非"schema 与 wire 不符"的欠账。


COMMAND_PAYLOAD_MAP: dict[CommandType, type[BaseModel]] = {
    CommandType.SPAWN_AGENT: SpawnAgentPayload,
    CommandType.TERMINATE_AGENT: TerminateAgentPayload,
    CommandType.SEND_MESSAGE: SendMessagePayload,
    CommandType.BROADCAST_MESSAGE: BroadcastMessagePayload,
    CommandType.EXECUTE_ABILITY: ExecuteAbilityPayload,
    CommandType.REGISTER_ABILITY: RegisterAbilityPayload,
    CommandType.UNREGISTER_ABILITY: UnregisterAbilityPayload,
    CommandType.LIST_AGENTS: ListAgentsPayload,
    CommandType.HEALTH_CHECK: HealthCheckPayload,
    CommandType.DELEGATE: DelegatePayload,
    CommandType.SUBSCRIBE: SubscribePayload,
    CommandType.UNSUBSCRIBE: UnsubscribePayload,
    CommandType.GET_AGENT_INFO: GetAgentInfoPayload,
    CommandType.AGENT_COMPACT_CONTEXT: AgentCompactContextPayload,
    CommandType.AGENT_RESET: AgentResetPayload,
    CommandType.INIT_CLUSTER: InitClusterPayload,
    CommandType.SHUTDOWN_CLUSTER: ShutdownClusterPayload,
    CommandType.CLUSTER_STATUS: ClusterStatusPayload,
    CommandType.LIST_CLUSTERS: ListClustersPayload,
    CommandType.HITL_RESPONSE: HITLResponsePayload,
    # persist_*: payload 为裸 dict（Stage 2 补齐真实 payload 模型）
    # Agent-scoped workspace 命令；资源级 register/get/list 仍以 workspace_id 路由。
    CommandType.CREATE_WORKSPACE: CreateWorkspacePayload,
    CommandType.DESTROY_WORKSPACE: DestroyWorkspacePayload,
    CommandType.WORKSPACE_SNAPSHOT: WorkspaceSnapshotPayload,
    CommandType.WORKSPACE_ROLLBACK: WorkspaceRollbackPayload,
    CommandType.WORKSPACE_DIFF: WorkspaceDiffPayload,
    CommandType.WORKSPACE_STATUS: WorkspaceStatusPayload,
    CommandType.WORKSPACE_REGISTER: WorkspaceRegisterPayload,
    CommandType.WORKSPACE_GET: WorkspaceGetPayload,
    CommandType.WORKSPACE_LIST: WorkspaceListPayload,
    CommandType.GET_CHAIN_HISTORY: GetChainHistoryPayload,
    # Task 管理（11 个）
    CommandType.TASK_CREATE: TaskCreatePayload,
    CommandType.TASK_UPDATE: TaskUpdatePayload,
    CommandType.TASK_ASSIGN: TaskAssignPayload,
    CommandType.TASK_START: TaskIdPayload,
    CommandType.TASK_COMPLETE: TaskCompletePayload,
    CommandType.TASK_FAIL: TaskFailPayload,
    CommandType.TASK_CANCEL: TaskCancelPayload,
    CommandType.TASK_BLOCK: TaskBlockPayload,
    CommandType.TASK_LIST: TaskListPayload,
    CommandType.TASK_GET: TaskIdPayload,
    CommandType.TASK_DELETE: TaskDeletePayload,
    # Task 归因切面（3 个；响应经 command_result.data：TaskClaimListResultPayload /
    # TaskVerificationGapsPayload 有模型但按惯例不登记 result）
    CommandType.TASK_SUBMIT_COMPLETION: TaskSubmitCompletionPayload,
    CommandType.TASK_VERIFY: TaskVerifyPayload,
    CommandType.TASK_LIST_CLAIMS: TaskClaimListPayload,
    CommandType.TASK_DUMP: TaskDumpPayload,
    # Session 管理（5 个）
    CommandType.SESSION_CREATE: SessionCreatePayload,
    CommandType.SESSION_ACTIVATE: SessionActivatePayload,
    CommandType.SESSION_LIST: SessionListPayload,
    CommandType.SESSION_ARCHIVE: SessionArchivePayload,
    CommandType.SESSION_DELETE: SessionDeletePayload,
    CommandType.BRANCH_CREATE: BranchCreatePayload,
    CommandType.BRANCH_ACTIVATE: BranchActivatePayload,
    CommandType.BRANCH_LIST: BranchListPayload,
    CommandType.BRANCH_ARCHIVE: BranchArchivePayload,
    CommandType.BRANCH_DELETE: BranchDeletePayload,
    # Project 管理（15 个）
    CommandType.PROJECT_CREATE: ProjectCreatePayload,
    CommandType.PROJECT_UPDATE: ProjectUpdatePayload,
    CommandType.PROJECT_LIST: ProjectListPayload,
    CommandType.PROJECT_GET: ProjectIdPayload,
    CommandType.PROJECT_ARCHIVE: ProjectLifecyclePayload,
    CommandType.PROJECT_RESTORE: ProjectLifecyclePayload,
    CommandType.PROJECT_DELETE: ProjectDeletePayload,
    CommandType.PROJECT_ADD_AGENT: ProjectAddAgentPayload,
    CommandType.AGENT_ENTITY: AgentEntityPayload,
    CommandType.AGENT_ENTITY_CREATE: AgentEntityCreatePayload,
    CommandType.AGENT_ENTITY_LIST: AgentEntityListPayload,
    CommandType.PROJECT_REMOVE_AGENT: ProjectRemoveAgentPayload,
    CommandType.PROJECT_LINK_TASK: ProjectLinkTaskPayload,
    CommandType.PROJECT_UNLINK_TASK: ProjectUnlinkTaskPayload,
    CommandType.PROJECT_SET_RECOVERY: ProjectSetRecoveryPayload,
    CommandType.PROJECT_PAUSE: ProjectIdPayload,
    CommandType.PROJECT_RESUME: ProjectIdPayload,
    CommandType.PROJECT_STOP: ProjectIdPayload,
    # Room 管理（12 个）
    CommandType.ROOM_CREATE: RoomCreatePayload,
    CommandType.ROOM_LIST: RoomListPayload,
    CommandType.ROOM_GET: RoomIdPayload,
    CommandType.ROOM_UPDATE: RoomUpdatePayload,
    CommandType.ROOM_ARCHIVE: RoomLifecyclePayload,
    CommandType.ROOM_RESTORE: RoomLifecyclePayload,
    CommandType.ROOM_DELETE: RoomDeletePayload,
    CommandType.ROOM_JOIN: RoomJoinPayload,
    CommandType.ROOM_LEAVE: RoomLeavePayload,
    CommandType.ROOM_GET_MEMBERS: RoomIdPayload,
    CommandType.ROOM_GET_LOG: RoomGetLogPayload,
    CommandType.ROOM_SEND: RoomSendPayload,
    # 恢复（2 个）
    CommandType.RECONCILE_NOW: ReconcileNowPayload,
    CommandType.RECONCILE_STATUS: ReconcileStatusPayload,
    # Plugin 协商（Observer → Subject；响应经 command_result.data =
    # PluginNegotiateResultPayload，形状由 ghrah-plugin negotiator 单一权威）
    CommandType.PLUGIN_NEGOTIATE: PluginNegotiatePayload,
}

EVENT_PAYLOAD_MAP: dict[EventType, type[BaseModel]] = {
    EventType.AGENT_SPAWNED: AgentSpawnedPayload,
    EventType.AGENT_TERMINATED: AgentTerminatedPayload,
    EventType.AGENT_RESPONSE: AgentResponsePayload,
    EventType.ACTION_CHAIN_UPDATED: ActionChainUpdatedPayload,
    EventType.AGENT_ERROR: AgentErrorPayload,
    EventType.HEALTH_STATUS: HealthStatusPayload,
    EventType.ABILITY_RESULT: AbilityResultPayload,
    EventType.HITL_REQUEST: HITLRequestPayload,
    EventType.HITL_RESOLVED: HITLResolvedPayload,
    EventType.CONTEXT_USAGE_UPDATED: ContextUsageUpdatedPayload,
    EventType.WORKSPACE_CREATED: WorkspaceCreatedPayload,
    EventType.WORKSPACE_DESTROYED: WorkspaceDestroyedPayload,
    EventType.WORKSPACE_SNAPSHOT_CREATED: WorkspaceSnapshotCreatedPayload,
    EventType.WORKSPACE_ROLLED_BACK: WorkspaceRolledBackPayload,
    EventType.SESSION_CREATED: SessionCreatedPayload,
    EventType.SESSION_ACTIVATED: SessionActivatedPayload,
    EventType.SESSION_ARCHIVED: SessionArchivedPayload,
    EventType.SESSION_DELETED: SessionDeletedPayload,
    EventType.SESSION_LIST_RESULT: SessionListResultPayload,
    EventType.BRANCH_CREATED: BranchEventPayload,
    EventType.BRANCH_ACTIVATED: BranchEventPayload,
    EventType.BRANCH_ARCHIVED: BranchLifecyclePayload,
    EventType.BRANCH_DELETED: BranchLifecyclePayload,
    EventType.BRANCH_LIST_RESULT: BranchListResultPayload,
    # task_* 事件（9 个）payload 模型 TaskEventPayload 已存在
    EventType.TASK_CREATED: TaskEventPayload,
    EventType.TASK_UPDATED: TaskEventPayload,
    EventType.TASK_ASSIGNED: TaskEventPayload,
    EventType.TASK_STARTED: TaskEventPayload,
    EventType.TASK_COMPLETED: TaskEventPayload,
    EventType.TASK_FAILED: TaskEventPayload,
    EventType.TASK_CANCELED: TaskEventPayload,
    EventType.TASK_BLOCKED: TaskEventPayload,
    EventType.TASK_DELETED: TaskEventPayload,
    # Task 归因事件（3 个；task 附 claim，TaskClaimEventPayload 共用）
    EventType.TASK_DELIVERED: TaskClaimEventPayload,
    EventType.TASK_VERIFIED: TaskClaimEventPayload,
    EventType.TASK_REJECTED: TaskClaimEventPayload,
    # manifest_* 事件 payload 模型存在
    EventType.MANIFEST_ABILITY_CREATED: ManifestAbilityEventPayload,
    EventType.MANIFEST_ABILITY_UPDATED: ManifestAbilityEventPayload,
    EventType.MANIFEST_ABILITY_DELETED: ManifestAbilityEventPayload,
    EventType.MANIFEST_AGENT_CREATED: ManifestAgentEventPayload,
    EventType.MANIFEST_AGENT_UPDATED: ManifestAgentEventPayload,
    EventType.MANIFEST_AGENT_DELETED: ManifestAgentEventPayload,
    # Project 事件（11 个）
    EventType.PROJECT_CREATED: ProjectEventPayload,
    EventType.PROJECT_UPDATED: ProjectEventPayload,
    EventType.PROJECT_ARCHIVED: ProjectEventPayload,
    EventType.PROJECT_RESTORED: ProjectEventPayload,
    EventType.PROJECT_DELETED: ProjectEventPayload,
    EventType.PROJECT_PAUSED: ProjectEventPayload,
    EventType.PROJECT_RESUMED: ProjectEventPayload,
    EventType.PROJECT_STOPPED: ProjectEventPayload,
    EventType.PROJECT_AGENT_ADDED: ProjectAgentEventPayload,
    EventType.PROJECT_AGENT_REMOVED: ProjectAgentEventPayload,
    EventType.PROJECT_RECOVERY_SET: ProjectEventPayload,
    # Room 事件（8 个）
    EventType.ROOM_CREATED: RoomEventPayload,
    EventType.ROOM_UPDATED: RoomEventPayload,
    EventType.ROOM_ARCHIVED: RoomEventPayload,
    EventType.ROOM_RESTORED: RoomEventPayload,
    EventType.ROOM_DELETED: RoomDeletedEventPayload,
    EventType.ROOM_MEMBER_JOINED: RoomMemberEventPayload,
    EventType.ROOM_MEMBER_LEFT: RoomMemberEventPayload,
    EventType.ROOM_LOG_APPENDED: RoomLogEventPayload,
    # 恢复事件（2 个）
    EventType.SUBJECT_RECONCILED: ReconcileReportPayload,
    EventType.RECONCILE_FAILED: ReconcileReportPayload,
    # Plugin 事件（4 个；enabled/disabled 共用 PluginLifecyclePayload）
    EventType.PLUGIN_ENABLED: PluginLifecyclePayload,
    EventType.PLUGIN_DISABLED: PluginLifecyclePayload,
    EventType.PLUGIN_NEGOTIATED: PluginNegotiatedPayload,
    EventType.PLUGIN_CRASHED: PluginCrashedPayload,
}


PAYLOAD_MAP: dict[str, type[BaseModel]] = {
    **{k.value: v for k, v in COMMAND_PAYLOAD_MAP.items()},
    **{k.value: v for k, v in EVENT_PAYLOAD_MAP.items()},
    # command_result / error / ping / pong 不登记（见模块注释）
}


def envelope_from_dict(data: dict[str, Any]) -> Envelope:
    """从 dict 反序列化为 Envelope（唯一反序列化入口）。

    wire 信封保持宽（payload: Any）。已知 type → 查 PAYLOAD_MAP 收窄为对应
    Pydantic 模型实例；未知 type / 无 payload / 未登记 → payload 保持裸 dict
    （前向兼容，不崩）。

    Args:
        data: 原始 wire dict（来自 websocket receive_json）

    Returns:
        Envelope 实例。已知 type 时 payload 为对应 BaseModel 实例，
        否则 payload 为裸 dict（或原始值）。
    """
    msg_type = data.get("type", "")
    payload_cls = PAYLOAD_MAP.get(msg_type)
    raw_payload = data.get("payload", {})
    if payload_cls is not None and isinstance(raw_payload, dict):
        payload = payload_cls.model_validate(raw_payload)
    else:
        payload = raw_payload
    return Envelope(
        type=msg_type,
        payload=payload,
        request_id=data.get("request_id"),
        timestamp=data.get("timestamp"),
        client_type=data.get("client_type"),
        seq_id=data.get("seq_id"),
    )


def expect_payload(msg: Envelope, cls: type[T]) -> T:
    """在 handler 边界把 payload 断言为具体类型。

    若 payload 已是该类型（经 envelope_from_dict 收窄）直接返回；
    若是 dict（未登记 MAP / 旧路径）则 model_validate 收窄；
    校验失败抛 ValidationError（暴露 wire/schema 不符，比静默 .get() 更好）。

    Args:
        msg: 收到的消息信封
        cls: 期望的 payload 类型

    Returns:
        具体类型的 payload 实例

    Raises:
        pydantic.ValidationError: payload 与 cls 不符
    """
    if isinstance(msg.payload, cls):
        return msg.payload
    return cls.model_validate(msg.payload)


def payload_agent_name(payload: Any) -> str | None:
    """提取订阅过滤使用的 agent_name。

    保持旧 wire 行为：订阅过滤只认 payload["agent_name"]。
    缺失 agent_name 时返回 None，ConnectionManager 将其解释为不过滤
    agent、广播给所有匹配 event_type 的连接。
    """
    if isinstance(payload, BaseModel):
        val = getattr(payload, "agent_name", None)
        return val if isinstance(val, str) and val else None
    if isinstance(payload, dict):
        val = payload.get("agent_name")
        return val if isinstance(val, str) and val else None
    return None


def make_agent_key(project_id: str, agent_id: str) -> str:
    """Return the stable Observer/Subject key for one Project-owned Agent."""

    return f"{project_id}:{agent_id}"
