export enum ClientType {
  SUBJECT = "subject",
  OBSERVER = "observer",
  CORE = "core",
}

export enum CommandType {
  SPAWN_AGENT = "spawn_agent",
  TERMINATE_AGENT = "terminate_agent",
  SEND_MESSAGE = "send_message",
  BROADCAST_MESSAGE = "broadcast_message",
  REGISTER_ABILITY = "register_ability",
  UNREGISTER_ABILITY = "unregister_ability",
  LIST_AGENTS = "list_agents",
  HEALTH_CHECK = "health_check",
  DELEGATE = "delegate",
  GET_AGENT_INFO = "get_agent_info",
  AGENT_COMPACT_CONTEXT = "agent_compact_context",
  AGENT_RESET = "agent_reset",

  INIT_CLUSTER = "init_cluster",
  SHUTDOWN_CLUSTER = "shutdown_cluster",
  CLUSTER_STATUS = "cluster_status",
  LIST_CLUSTERS = "list_clusters",

  SUBSCRIBE = "subscribe",
  UNSUBSCRIBE = "unsubscribe",

  EXECUTE_ABILITY = "execute_ability",

  HITL_RESPONSE = "hitl_response",

  PERSIST_SAVE_NODE = "persist_save_node",
  PERSIST_LOAD_NODE = "persist_load_node",
  PERSIST_LOAD_CHAIN = "persist_load_chain",
  PERSIST_SAVE_MESSAGES = "persist_save_messages",
  PERSIST_LOAD_MESSAGES = "persist_load_messages",
  PERSIST_DELETE_CHAIN = "persist_delete_chain",
  PERSIST_LIST_AGENTS = "persist_list_agents",

  CREATE_WORKSPACE = "create_workspace",
  DESTROY_WORKSPACE = "destroy_workspace",
  WORKSPACE_SNAPSHOT = "workspace_snapshot",
  WORKSPACE_ROLLBACK = "workspace_rollback",
  WORKSPACE_DIFF = "workspace_diff",
  WORKSPACE_STATUS = "workspace_status",
  WORKSPACE_REGISTER = "workspace_register",
  WORKSPACE_GET = "workspace_get",
  WORKSPACE_LIST = "workspace_list",

  MANIFEST_LIST_ABILITIES = "manifest_list_abilities",
  MANIFEST_GET_ABILITY = "manifest_get_ability",
  MANIFEST_PUT_ABILITY = "manifest_put_ability",
  MANIFEST_DELETE_ABILITY = "manifest_delete_ability",
  MANIFEST_LIST_AGENTS = "manifest_list_agents",
  MANIFEST_GET_AGENT = "manifest_get_agent",
  MANIFEST_PUT_AGENT = "manifest_put_agent",
  MANIFEST_DELETE_AGENT = "manifest_delete_agent",
  MANIFEST_RESOLVE_AGENT = "manifest_resolve_agent",
  MANIFEST_VALIDATE = "manifest_validate",

  TASK_CREATE = "task_create",
  TASK_UPDATE = "task_update",
  TASK_ASSIGN = "task_assign",
  TASK_START = "task_start",
  TASK_COMPLETE = "task_complete",
  TASK_FAIL = "task_fail",
  TASK_CANCEL = "task_cancel",
  TASK_BLOCK = "task_block",
  TASK_LIST = "task_list",
  TASK_GET = "task_get",
  TASK_DELETE = "task_delete",

  // Task 归因切面（完成声明/验收/claims 查询/全量快照）
  TASK_SUBMIT_COMPLETION = "task_submit_completion",
  TASK_VERIFY = "task_verify",
  TASK_LIST_CLAIMS = "task_list_claims",
  TASK_DUMP = "task_dump",

  // Plugin 协商（L1 通用命令，不挂域前缀）
  PLUGIN_NEGOTIATE = "plugin_negotiate",

  PROJECT_CREATE = "project_create",
  PROJECT_UPDATE = "project_update",
  PROJECT_LIST = "project_list",
  PROJECT_GET = "project_get",
  PROJECT_ARCHIVE = "project_archive",
  PROJECT_RESTORE = "project_restore",
  PROJECT_DELETE = "project_delete",
  PROJECT_ADD_AGENT = "project_add_agent",
  AGENT_ENTITY = "agent_entity",
  AGENT_ENTITY_CREATE = "agent_entity_create",
  AGENT_ENTITY_LIST = "agent_entity_list",
  PROJECT_REMOVE_AGENT = "project_remove_agent",
  PROJECT_LINK_TASK = "project_link_task",
  PROJECT_UNLINK_TASK = "project_unlink_task",
  PROJECT_SET_RECOVERY = "project_set_recovery",
  PROJECT_PAUSE = "project_pause",
  PROJECT_RESUME = "project_resume",
  PROJECT_STOP = "project_stop",

  ROOM_CREATE = "room_create",
  ROOM_LIST = "room_list",
  ROOM_GET = "room_get",
  ROOM_UPDATE = "room_update",
  ROOM_ARCHIVE = "room_archive",
  ROOM_RESTORE = "room_restore",
  ROOM_DELETE = "room_delete",
  ROOM_JOIN = "room_join",
  ROOM_LEAVE = "room_leave",
  ROOM_GET_MEMBERS = "room_get_members",
  ROOM_GET_LOG = "room_get_log",
  ROOM_SEND = "room_send",

  SESSION_CREATE = "session_create",
  SESSION_ACTIVATE = "session_activate",
  SESSION_LIST = "session_list",
  SESSION_ARCHIVE = "session_archive",
  SESSION_DELETE = "session_delete",
  BRANCH_CREATE = "branch_create",
  BRANCH_ACTIVATE = "branch_activate",
  BRANCH_LIST = "branch_list",
  BRANCH_ARCHIVE = "branch_archive",
  BRANCH_DELETE = "branch_delete",

  GET_CHAIN_HISTORY = "get_chain_history",

  RECONCILE_NOW = "reconcile_now",
  RECONCILE_STATUS = "reconcile_status",
}

export enum EventType {
  AGENT_SPAWNED = "agent_spawned",
  AGENT_TERMINATED = "agent_terminated",
  AGENT_RESPONSE = "agent_response",
  ACTION_CHAIN_UPDATED = "action_chain_updated",
  AGENT_ERROR = "agent_error",
  HEALTH_STATUS = "health_status",
  ABILITY_RESULT = "ability_result",
  HITL_REQUEST = "hitl_request",
  HITL_RESOLVED = "hitl_resolved",
  CONTEXT_USAGE_UPDATED = "context_usage_updated",
  WORKSPACE_CREATED = "workspace_created",
  WORKSPACE_DESTROYED = "workspace_destroyed",
  WORKSPACE_SNAPSHOT_CREATED = "workspace_snapshot_created",
  WORKSPACE_ROLLED_BACK = "workspace_rolled_back",

  MANIFEST_ABILITY_CREATED = "manifest_ability_created",
  MANIFEST_ABILITY_UPDATED = "manifest_ability_updated",
  MANIFEST_ABILITY_DELETED = "manifest_ability_deleted",
  MANIFEST_AGENT_CREATED = "manifest_agent_created",
  MANIFEST_AGENT_UPDATED = "manifest_agent_updated",
  MANIFEST_AGENT_DELETED = "manifest_agent_deleted",

  TASK_CREATED = "task_created",
  TASK_UPDATED = "task_updated",
  TASK_ASSIGNED = "task_assigned",
  TASK_STARTED = "task_started",
  TASK_COMPLETED = "task_completed",
  TASK_FAILED = "task_failed",
  TASK_CANCELED = "task_canceled",
  TASK_BLOCKED = "task_blocked",
  TASK_DELETED = "task_deleted",

  // Task 归因事件（claim 附带，completed = 验收通过）
  TASK_DELIVERED = "task_delivered",
  TASK_VERIFIED = "task_verified",
  TASK_REJECTED = "task_rejected",

  PROJECT_CREATED = "project_created",
  PROJECT_UPDATED = "project_updated",
  PROJECT_ARCHIVED = "project_archived",
  PROJECT_RESTORED = "project_restored",
  PROJECT_DELETED = "project_deleted",
  PROJECT_PAUSED = "project_paused",
  PROJECT_RESUMED = "project_resumed",
  PROJECT_STOPPED = "project_stopped",
  PROJECT_AGENT_ADDED = "project_agent_added",
  PROJECT_AGENT_REMOVED = "project_agent_removed",
  PROJECT_RECOVERY_SET = "project_recovery_set",

  ROOM_CREATED = "room_created",
  ROOM_UPDATED = "room_updated",
  ROOM_ARCHIVED = "room_archived",
  ROOM_RESTORED = "room_restored",
  ROOM_DELETED = "room_deleted",
  ROOM_MEMBER_JOINED = "room_member_joined",
  ROOM_MEMBER_LEFT = "room_member_left",
  ROOM_LOG_APPENDED = "room_log_appended",

  SESSION_CREATED = "session_created",
  SESSION_ACTIVATED = "session_activated",
  SESSION_ARCHIVED = "session_archived",
  SESSION_DELETED = "session_deleted",
  SESSION_LIST_RESULT = "session_list_result",
  BRANCH_CREATED = "branch_created",
  BRANCH_ACTIVATED = "branch_activated",
  BRANCH_ARCHIVED = "branch_archived",
  BRANCH_DELETED = "branch_deleted",
  BRANCH_LIST_RESULT = "branch_list_result",

  SUBJECT_RECONCILED = "subject_reconciled",
  RECONCILE_FAILED = "reconcile_failed",

  // Plugin 事件（协商与崩溃可见性）
  PLUGIN_ENABLED = "plugin_enabled",
  PLUGIN_DISABLED = "plugin_disabled",
  PLUGIN_NEGOTIATED = "plugin_negotiated",
  PLUGIN_CRASHED = "plugin_crashed",
}

export enum SystemType {
  COMMAND_RESULT = "command_result",
  PING = "ping",
  PONG = "pong",
  ERROR = "error",
}

export const PERSIST_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.PERSIST_SAVE_NODE,
  CommandType.PERSIST_LOAD_NODE,
  CommandType.PERSIST_LOAD_CHAIN,
  CommandType.PERSIST_SAVE_MESSAGES,
  CommandType.PERSIST_LOAD_MESSAGES,
  CommandType.PERSIST_DELETE_CHAIN,
  CommandType.PERSIST_LIST_AGENTS,
]);

export const CORE_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.SPAWN_AGENT,
  CommandType.TERMINATE_AGENT,
  CommandType.SEND_MESSAGE,
  CommandType.BROADCAST_MESSAGE,
  CommandType.REGISTER_ABILITY,
  CommandType.UNREGISTER_ABILITY,
  CommandType.LIST_AGENTS,
  CommandType.HEALTH_CHECK,
  CommandType.DELEGATE,
  CommandType.GET_AGENT_INFO,
  CommandType.AGENT_COMPACT_CONTEXT,
  CommandType.AGENT_RESET,
  CommandType.INIT_CLUSTER,
  CommandType.SHUTDOWN_CLUSTER,
  CommandType.CLUSTER_STATUS,
  CommandType.LIST_CLUSTERS,
  CommandType.SESSION_CREATE,
  CommandType.SESSION_ACTIVATE,
  CommandType.SESSION_LIST,
  CommandType.SESSION_ARCHIVE,
  CommandType.SESSION_DELETE,
  CommandType.BRANCH_CREATE,
  CommandType.BRANCH_ACTIVATE,
  CommandType.BRANCH_LIST,
  CommandType.BRANCH_ARCHIVE,
  CommandType.BRANCH_DELETE,
]);

export const SESSION_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.SESSION_CREATE,
  CommandType.SESSION_ACTIVATE,
  CommandType.SESSION_LIST,
  CommandType.SESSION_ARCHIVE,
  CommandType.SESSION_DELETE,
]);

export const BRANCH_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.BRANCH_CREATE,
  CommandType.BRANCH_ACTIVATE,
  CommandType.BRANCH_LIST,
  CommandType.BRANCH_ARCHIVE,
  CommandType.BRANCH_DELETE,
]);

export const CHAIN_HISTORY_COMMANDS: ReadonlySet<string> = new Set([CommandType.GET_CHAIN_HISTORY]);

export const WORKSPACE_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.CREATE_WORKSPACE,
  CommandType.DESTROY_WORKSPACE,
  CommandType.WORKSPACE_SNAPSHOT,
  CommandType.WORKSPACE_ROLLBACK,
  CommandType.WORKSPACE_DIFF,
  CommandType.WORKSPACE_STATUS,
  CommandType.WORKSPACE_REGISTER,
  CommandType.WORKSPACE_GET,
  CommandType.WORKSPACE_LIST,
]);

export const MANIFEST_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.MANIFEST_LIST_ABILITIES,
  CommandType.MANIFEST_GET_ABILITY,
  CommandType.MANIFEST_PUT_ABILITY,
  CommandType.MANIFEST_DELETE_ABILITY,
  CommandType.MANIFEST_LIST_AGENTS,
  CommandType.MANIFEST_GET_AGENT,
  CommandType.MANIFEST_PUT_AGENT,
  CommandType.MANIFEST_DELETE_AGENT,
  CommandType.MANIFEST_RESOLVE_AGENT,
  CommandType.MANIFEST_VALIDATE,
]);

export const TASK_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.TASK_CREATE,
  CommandType.TASK_UPDATE,
  CommandType.TASK_ASSIGN,
  CommandType.TASK_START,
  CommandType.TASK_COMPLETE,
  CommandType.TASK_FAIL,
  CommandType.TASK_CANCEL,
  CommandType.TASK_BLOCK,
  CommandType.TASK_LIST,
  CommandType.TASK_GET,
  CommandType.TASK_DELETE,
]);

export const TASK_ATTRIBUTION_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.TASK_SUBMIT_COMPLETION,
  CommandType.TASK_VERIFY,
  CommandType.TASK_LIST_CLAIMS,
  CommandType.TASK_DUMP,
]);

export const PLUGIN_COMMANDS: ReadonlySet<string> = new Set([CommandType.PLUGIN_NEGOTIATE]);

export const PROJECT_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.PROJECT_CREATE,
  CommandType.PROJECT_UPDATE,
  CommandType.PROJECT_LIST,
  CommandType.PROJECT_GET,
  CommandType.PROJECT_ARCHIVE,
  CommandType.PROJECT_RESTORE,
  CommandType.PROJECT_DELETE,
  CommandType.PROJECT_ADD_AGENT,
  CommandType.AGENT_ENTITY,
  CommandType.AGENT_ENTITY_CREATE,
  CommandType.AGENT_ENTITY_LIST,
  CommandType.PROJECT_REMOVE_AGENT,
  CommandType.PROJECT_LINK_TASK,
  CommandType.PROJECT_UNLINK_TASK,
  CommandType.PROJECT_SET_RECOVERY,
  CommandType.PROJECT_PAUSE,
  CommandType.PROJECT_RESUME,
  CommandType.PROJECT_STOP,
]);

export const ROOM_COMMANDS: ReadonlySet<string> = new Set([
  CommandType.ROOM_CREATE,
  CommandType.ROOM_LIST,
  CommandType.ROOM_GET,
  CommandType.ROOM_UPDATE,
  CommandType.ROOM_ARCHIVE,
  CommandType.ROOM_RESTORE,
  CommandType.ROOM_DELETE,
  CommandType.ROOM_JOIN,
  CommandType.ROOM_LEAVE,
  CommandType.ROOM_GET_MEMBERS,
  CommandType.ROOM_GET_LOG,
  CommandType.ROOM_SEND,
]);

export type MessageType = CommandType | EventType | SystemType;
