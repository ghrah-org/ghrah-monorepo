# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""协议枚举定义。

客户端类型、命令/事件/系统消息类型、路由用命令分组与各域附属枚举。
"""

from __future__ import annotations

from enum import StrEnum

# ─── 客户端类型枚举 ───


class ClientType(StrEnum):
    """WebSocket 连接的客户端类型。

    区分 Subject 连接、Observer 连接和 Core 连接，
    用于事件广播时按类型过滤目标。

    - SUBJECT: Subject 服务连接，执行 Ability、持久化等
    - OBSERVER: Observer 客户端连接，接收事件、下发命令
    - CORE: Agent 的 CoreClient 连接，发布事件、请求执行 Ability
    """

    SUBJECT = "subject"
    OBSERVER = "observer"
    CORE = "core"  # ─── 消息类型枚举 ───


class CommandType(StrEnum):
    """命令类型。

    Observer → Subject → Core:
        Agent 管理命令，Observer 发起，经 Subject 转发到 Core。

    Observer → Subject:
        Workspace 管理命令，Observer 发起，Subject 本地处理。

    Core → Subject:
        Ability 执行和持久化命令，Core 发起，Subject 处理。

    Observer → Subject:
        HITL 响应命令，Observer 发起，Subject 处理。

    Observer (local):
        订阅命令，本地处理。
    """

    # ─── Agent 管理类（Observer → Subject → Core）───
    SPAWN_AGENT = "spawn_agent"
    TERMINATE_AGENT = "terminate_agent"
    SEND_MESSAGE = "send_message"
    BROADCAST_MESSAGE = "broadcast_message"
    REGISTER_ABILITY = "register_ability"
    UNREGISTER_ABILITY = "unregister_ability"
    LIST_AGENTS = "list_agents"
    HEALTH_CHECK = "health_check"
    DELEGATE = "delegate"
    GET_AGENT_INFO = "get_agent_info"
    AGENT_COMPACT_CONTEXT = "agent_compact_context"
    AGENT_RESET = "agent_reset"

    # ─── Supervisor 类（Observer → Subject → Core）───
    INIT_CLUSTER = "init_cluster"
    SHUTDOWN_CLUSTER = "shutdown_cluster"
    CLUSTER_STATUS = "cluster_status"
    LIST_CLUSTERS = "list_clusters"

    # ─── 订阅类（Observer 本地处理）───
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"

    # ─── Ability 执行类（Core → Subject）───
    EXECUTE_ABILITY = "execute_ability"

    # ─── HITL 类（Observer → Subject）───
    HITL_RESPONSE = "hitl_response"

    # ─── 持久化类（Core → Subject）───
    PERSIST_SAVE_NODE = "persist_save_node"
    PERSIST_LOAD_NODE = "persist_load_node"
    PERSIST_LOAD_CHAIN = "persist_load_chain"
    PERSIST_SAVE_MESSAGES = "persist_save_messages"
    PERSIST_LOAD_MESSAGES = "persist_load_messages"
    PERSIST_DELETE_CHAIN = "persist_delete_chain"
    PERSIST_LIST_AGENTS = "persist_list_agents"

    # ─── Workspace 管理类（Observer → Subject）───
    CREATE_WORKSPACE = "create_workspace"
    DESTROY_WORKSPACE = "destroy_workspace"
    WORKSPACE_SNAPSHOT = "workspace_snapshot"
    WORKSPACE_ROLLBACK = "workspace_rollback"
    WORKSPACE_DIFF = "workspace_diff"
    WORKSPACE_STATUS = "workspace_status"
    # workspace 一等资源命令（workspace_id 键控）
    WORKSPACE_REGISTER = "workspace_register"
    WORKSPACE_GET = "workspace_get"
    WORKSPACE_LIST = "workspace_list"

    # ─── Session 管理类（Observer → Subject → Core）───
    SESSION_CREATE = "session_create"
    SESSION_ACTIVATE = "session_activate"
    SESSION_LIST = "session_list"
    SESSION_ARCHIVE = "session_archive"
    SESSION_DELETE = "session_delete"
    BRANCH_CREATE = "branch_create"
    BRANCH_ACTIVATE = "branch_activate"
    BRANCH_LIST = "branch_list"
    BRANCH_ARCHIVE = "branch_archive"
    BRANCH_DELETE = "branch_delete"

    # ─── Manifest CRUD 类（Observer → Subject）───
    MANIFEST_LIST_ABILITIES = "manifest_list_abilities"
    MANIFEST_GET_ABILITY = "manifest_get_ability"
    MANIFEST_PUT_ABILITY = "manifest_put_ability"
    MANIFEST_DELETE_ABILITY = "manifest_delete_ability"
    MANIFEST_LIST_AGENTS = "manifest_list_agents"
    MANIFEST_GET_AGENT = "manifest_get_agent"
    MANIFEST_PUT_AGENT = "manifest_put_agent"
    MANIFEST_DELETE_AGENT = "manifest_delete_agent"
    MANIFEST_RESOLVE_AGENT = "manifest_resolve_agent"
    MANIFEST_VALIDATE = "manifest_validate"

    # ─── Chain History 类（Observer → Subject）───
    GET_CHAIN_HISTORY = "get_chain_history"

    # ─── Task 管理类（Observer/Core → Subject）───
    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    TASK_ASSIGN = "task_assign"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAIL = "task_fail"
    TASK_CANCEL = "task_cancel"
    TASK_BLOCK = "task_block"
    TASK_LIST = "task_list"
    TASK_GET = "task_get"
    TASK_DELETE = "task_delete"
    # ─── Task 归因切面（Observer/Core → Subject；完成声明/验收/claims 查询/全量快照）───
    TASK_SUBMIT_COMPLETION = "task_submit_completion"
    TASK_VERIFY = "task_verify"
    TASK_LIST_CLAIMS = "task_list_claims"
    TASK_DUMP = "task_dump"

    # ─── Plugin 类（Observer → Subject；协商：通用命令，不挂域前缀）───
    PLUGIN_NEGOTIATE = "plugin_negotiate"

    # ─── Project 管理类（Observer → Subject，15 个）───
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_LIST = "project_list"
    PROJECT_GET = "project_get"
    PROJECT_ARCHIVE = "project_archive"
    PROJECT_RESTORE = "project_restore"
    PROJECT_DELETE = "project_delete"
    PROJECT_ADD_AGENT = "project_add_agent"
    AGENT_ENTITY = "agent_entity"
    AGENT_ENTITY_CREATE = "agent_entity_create"
    AGENT_ENTITY_LIST = "agent_entity_list"
    PROJECT_REMOVE_AGENT = "project_remove_agent"
    PROJECT_LINK_TASK = "project_link_task"
    PROJECT_UNLINK_TASK = "project_unlink_task"
    PROJECT_SET_RECOVERY = "project_set_recovery"
    PROJECT_PAUSE = "project_pause"
    PROJECT_RESUME = "project_resume"
    PROJECT_STOP = "project_stop"

    # ─── Room 管理类（Observer/Core → Subject，12 个）───
    ROOM_CREATE = "room_create"
    ROOM_LIST = "room_list"
    ROOM_GET = "room_get"
    ROOM_UPDATE = "room_update"
    ROOM_ARCHIVE = "room_archive"
    ROOM_RESTORE = "room_restore"
    ROOM_DELETE = "room_delete"
    ROOM_JOIN = "room_join"
    ROOM_LEAVE = "room_leave"
    ROOM_GET_MEMBERS = "room_get_members"
    ROOM_GET_LOG = "room_get_log"
    ROOM_SEND = "room_send"

    # ─── 恢复类（Observer → Subject，2 个）───
    RECONCILE_NOW = "reconcile_now"
    RECONCILE_STATUS = "reconcile_status"


class EventType(StrEnum):
    """事件类型。

    方向说明：
    - Core 事件：Core → Subject + Observer
    - Subject 事件：Subject → Core + Observer
    - HITL 事件：Subject → Observer
    """

    AGENT_SPAWNED = "agent_spawned"
    AGENT_TERMINATED = "agent_terminated"
    AGENT_RESPONSE = "agent_response"
    ACTION_CHAIN_UPDATED = "action_chain_updated"
    AGENT_ERROR = "agent_error"
    HEALTH_STATUS = "health_status"
    ABILITY_RESULT = "ability_result"
    HITL_REQUEST = "hitl_request"
    HITL_RESOLVED = "hitl_resolved"
    CONTEXT_USAGE_UPDATED = "context_usage_updated"
    WORKSPACE_CREATED = "workspace_created"
    WORKSPACE_DESTROYED = "workspace_destroyed"
    WORKSPACE_SNAPSHOT_CREATED = "workspace_snapshot_created"
    WORKSPACE_ROLLED_BACK = "workspace_rolled_back"

    # ─── Manifest 变更事件（Subject → Observer）───
    MANIFEST_ABILITY_CREATED = "manifest_ability_created"
    MANIFEST_ABILITY_UPDATED = "manifest_ability_updated"
    MANIFEST_ABILITY_DELETED = "manifest_ability_deleted"
    MANIFEST_AGENT_CREATED = "manifest_agent_created"
    MANIFEST_AGENT_UPDATED = "manifest_agent_updated"
    MANIFEST_AGENT_DELETED = "manifest_agent_deleted"

    # ─── Session 事件（Core → Observer）───
    SESSION_CREATED = "session_created"
    SESSION_ACTIVATED = "session_activated"
    SESSION_ARCHIVED = "session_archived"
    SESSION_DELETED = "session_deleted"
    SESSION_LIST_RESULT = "session_list_result"
    BRANCH_CREATED = "branch_created"
    BRANCH_ACTIVATED = "branch_activated"
    BRANCH_ARCHIVED = "branch_archived"
    BRANCH_DELETED = "branch_deleted"
    BRANCH_LIST_RESULT = "branch_list_result"

    # ─── Task 事件（Subject → Observer/Core）───
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_ASSIGNED = "task_assigned"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELED = "task_canceled"
    TASK_BLOCKED = "task_blocked"
    TASK_DELETED = "task_deleted"

    # ─── Task 归因事件（Subject → Observer；claim 附带，completed = 验收通过）───
    TASK_DELIVERED = "task_delivered"
    TASK_VERIFIED = "task_verified"
    TASK_REJECTED = "task_rejected"

    # ─── Project 事件（Subject → Observer，11 个）───
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_ARCHIVED = "project_archived"
    PROJECT_RESTORED = "project_restored"
    PROJECT_DELETED = "project_deleted"
    PROJECT_PAUSED = "project_paused"
    PROJECT_RESUMED = "project_resumed"
    PROJECT_STOPPED = "project_stopped"
    PROJECT_AGENT_ADDED = "project_agent_added"
    PROJECT_AGENT_REMOVED = "project_agent_removed"
    PROJECT_RECOVERY_SET = "project_recovery_set"

    # ─── Room 事件（Subject → Observer/Core，8 个）───
    ROOM_CREATED = "room_created"
    ROOM_UPDATED = "room_updated"
    ROOM_ARCHIVED = "room_archived"
    ROOM_RESTORED = "room_restored"
    ROOM_DELETED = "room_deleted"
    ROOM_MEMBER_JOINED = "room_member_joined"
    ROOM_MEMBER_LEFT = "room_member_left"
    ROOM_LOG_APPENDED = "room_log_appended"

    # ─── 恢复事件（Subject → Observer，2 个）───
    SUBJECT_RECONCILED = "subject_reconciled"
    RECONCILE_FAILED = "reconcile_failed"

    # ─── Plugin 事件（Subject → Observer；协商与崩溃可见性）───
    PLUGIN_ENABLED = "plugin_enabled"
    PLUGIN_DISABLED = "plugin_disabled"
    PLUGIN_NEGOTIATED = "plugin_negotiated"
    PLUGIN_CRASHED = "plugin_crashed"


class TaskStatus(StrEnum):
    """Task lifecycle state（归因状态机：delivered = 已交付待验收，completed = 验收通过）。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskPriority(StrEnum):
    """Task priority label."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class SystemType(StrEnum):
    """系统消息类型。"""

    COMMAND_RESULT = "command_result"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"


MessageType = CommandType | EventType | SystemType  # ─── Project 附属枚举 ───


class ProjectStatus(StrEnum):
    """Project lifecycle state（与 ghrah-subject project/models.py 对齐）。"""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


class RecoveryAction(StrEnum):
    """恢复动作（on_restart 取值 / reconcile 未知 workspace 处理）。"""

    RESUME = "resume"
    PAUSE = "pause"
    DROP = "drop"  # ─── Room 附属枚举 ───


class RoomSubjectType(StrEnum):
    """Room 成员/作者类型。"""

    AGENT = "agent"
    HUMAN = "human"


class ClaimantType(StrEnum):
    """Task 归因切面：完成声明的 claimant 类型（human|agent）。"""

    AGENT = "agent"
    HUMAN = "human"


class RoomStatus(StrEnum):
    """Room 生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"
