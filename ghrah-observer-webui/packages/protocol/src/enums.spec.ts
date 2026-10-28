import { describe, expect, it } from "vitest";
import {
  BRANCH_COMMANDS,
  CHAIN_HISTORY_COMMANDS,
  ClientType,
  CORE_COMMANDS,
  CommandType,
  EventType,
  MANIFEST_COMMANDS,
  PERSIST_COMMANDS,
  PLUGIN_COMMANDS,
  PROJECT_COMMANDS,
  ROOM_COMMANDS,
  SESSION_COMMANDS,
  SystemType,
  TASK_ATTRIBUTION_COMMANDS,
  TASK_COMMANDS,
  WORKSPACE_COMMANDS,
} from "./enums.js";

describe("ClientType", () => {
  it("has correct values matching Python protocol", () => {
    expect(ClientType.SUBJECT).toBe("subject");
    expect(ClientType.OBSERVER).toBe("observer");
    expect(ClientType.CORE).toBe("core");
  });

  it("has exactly 3 members", () => {
    expect(Object.values(ClientType)).toHaveLength(3);
  });
});

describe("CommandType", () => {
  const PYTHON_COMMAND_VALUES = new Set([
    "spawn_agent",
    "terminate_agent",
    "send_message",
    "broadcast_message",
    "register_ability",
    "unregister_ability",
    "list_agents",
    "health_check",
    "delegate",
    "get_agent_info",
    "agent_compact_context",
    "agent_reset",
    "init_cluster",
    "shutdown_cluster",
    "cluster_status",
    "list_clusters",
    "subscribe",
    "unsubscribe",
    "execute_ability",
    "hitl_response",
    "persist_save_node",
    "persist_load_node",
    "persist_load_chain",
    "persist_save_messages",
    "persist_load_messages",
    "persist_delete_chain",
    "persist_list_agents",
    "create_workspace",
    "destroy_workspace",
    "workspace_snapshot",
    "workspace_rollback",
    "workspace_diff",
    "workspace_status",
    "workspace_register",
    "workspace_get",
    "workspace_list",
    "manifest_list_abilities",
    "manifest_get_ability",
    "manifest_put_ability",
    "manifest_delete_ability",
    "manifest_list_agents",
    "manifest_get_agent",
    "manifest_put_agent",
    "manifest_delete_agent",
    "manifest_resolve_agent",
    "manifest_validate",
    "task_create",
    "task_update",
    "task_assign",
    "task_start",
    "task_complete",
    "task_fail",
    "task_cancel",
    "task_block",
    "task_list",
    "task_get",
    "task_delete",
    "task_submit_completion",
    "task_verify",
    "task_list_claims",
    "task_dump",
    "plugin_negotiate",
    "project_create",
    "project_update",
    "project_list",
    "project_get",
    "project_archive",
    "project_restore",
    "project_delete",
    "project_add_agent",
    "agent_entity",
    "agent_entity_create",
    "agent_entity_list",
    "project_remove_agent",
    "project_link_task",
    "project_unlink_task",
    "project_set_recovery",
    "project_pause",
    "project_resume",
    "project_stop",
    "room_create",
    "room_list",
    "room_get",
    "room_update",
    "room_archive",
    "room_restore",
    "room_delete",
    "room_join",
    "room_leave",
    "room_get_members",
    "room_get_log",
    "room_send",
    "session_create",
    "session_activate",
    "session_list",
    "session_archive",
    "session_delete",
    "branch_create",
    "branch_activate",
    "branch_list",
    "branch_archive",
    "branch_delete",
    "get_chain_history",
    "reconcile_now",
    "reconcile_status",
  ]);

  it("matches Python CommandType values exactly", () => {
    const tsValues = new Set(Object.values(CommandType));
    expect(tsValues).toEqual(PYTHON_COMMAND_VALUES);
  });

  it("has no duplicate values", () => {
    const values = Object.values(CommandType);
    expect(new Set(values).size).toBe(values.length);
  });
});

describe("EventType", () => {
  const PYTHON_EVENT_VALUES = new Set([
    "agent_spawned",
    "agent_terminated",
    "agent_response",
    "action_chain_updated",
    "agent_error",
    "health_status",
    "ability_result",
    "hitl_request",
    "hitl_resolved",
    "context_usage_updated",
    "workspace_created",
    "workspace_destroyed",
    "workspace_snapshot_created",
    "workspace_rolled_back",
    "manifest_ability_created",
    "manifest_ability_updated",
    "manifest_ability_deleted",
    "manifest_agent_created",
    "manifest_agent_updated",
    "manifest_agent_deleted",
    "task_created",
    "task_updated",
    "task_assigned",
    "task_started",
    "task_completed",
    "task_failed",
    "task_canceled",
    "task_blocked",
    "task_deleted",
    "task_delivered",
    "task_verified",
    "task_rejected",
    "project_created",
    "project_updated",
    "project_archived",
    "project_restored",
    "project_deleted",
    "project_paused",
    "project_resumed",
    "project_stopped",
    "project_agent_added",
    "project_agent_removed",
    "project_recovery_set",
    "room_created",
    "room_updated",
    "room_archived",
    "room_restored",
    "room_deleted",
    "room_member_joined",
    "room_member_left",
    "room_log_appended",
    "session_created",
    "session_activated",
    "session_archived",
    "session_deleted",
    "session_list_result",
    "branch_created",
    "branch_activated",
    "branch_archived",
    "branch_deleted",
    "branch_list_result",
    "subject_reconciled",
    "reconcile_failed",
    "plugin_enabled",
    "plugin_disabled",
    "plugin_negotiated",
    "plugin_crashed",
  ]);

  it("has exactly 65 values matching Python EventType", () => {
    const tsValues = new Set(Object.values(EventType));
    expect(tsValues).toEqual(PYTHON_EVENT_VALUES);
  });
});

describe("SystemType", () => {
  it("has correct values matching Python SystemType", () => {
    expect(SystemType.COMMAND_RESULT).toBe("command_result");
    expect(SystemType.PING).toBe("ping");
    expect(SystemType.PONG).toBe("pong");
    expect(SystemType.ERROR).toBe("error");
  });

  it("has exactly 4 members", () => {
    expect(Object.values(SystemType)).toHaveLength(4);
  });
});

describe("PERSIST_COMMANDS", () => {
  const PYTHON_PERSIST = new Set([
    "persist_save_node",
    "persist_load_node",
    "persist_load_chain",
    "persist_save_messages",
    "persist_load_messages",
    "persist_delete_chain",
    "persist_list_agents",
  ]);

  it("contains exactly Python persist command values", () => {
    expect(PERSIST_COMMANDS).toEqual(PYTHON_PERSIST);
  });
});

describe("CORE_COMMANDS", () => {
  const PYTHON_CORE_COMMANDS = new Set([
    "spawn_agent",
    "terminate_agent",
    "send_message",
    "broadcast_message",
    "register_ability",
    "unregister_ability",
    "list_agents",
    "health_check",
    "delegate",
    "get_agent_info",
    "agent_compact_context",
    "agent_reset",
    "init_cluster",
    "shutdown_cluster",
    "cluster_status",
    "list_clusters",
    "session_create",
    "session_activate",
    "session_list",
    "session_archive",
    "session_delete",
    "branch_create",
    "branch_activate",
    "branch_list",
    "branch_archive",
    "branch_delete",
  ]);

  it("contains exactly 26 core command values", () => {
    expect(CORE_COMMANDS).toEqual(PYTHON_CORE_COMMANDS);
  });
});

describe("SESSION_COMMANDS", () => {
  it("contains the five Project-scoped Session commands", () => {
    expect(SESSION_COMMANDS).toEqual(
      new Set([
        "session_create",
        "session_activate",
        "session_list",
        "session_archive",
        "session_delete",
      ]),
    );
  });
});

describe("BRANCH_COMMANDS", () => {
  it("contains the five Project-scoped Branch commands", () => {
    expect(BRANCH_COMMANDS).toEqual(
      new Set([
        "branch_create",
        "branch_activate",
        "branch_list",
        "branch_archive",
        "branch_delete",
      ]),
    );
  });
});

describe("CHAIN_HISTORY_COMMANDS", () => {
  it("contains get_chain_history", () => {
    expect(CHAIN_HISTORY_COMMANDS).toEqual(new Set(["get_chain_history"]));
  });
});

describe("WORKSPACE_COMMANDS", () => {
  const PYTHON_WORKSPACE = new Set([
    "create_workspace",
    "destroy_workspace",
    "workspace_snapshot",
    "workspace_rollback",
    "workspace_diff",
    "workspace_status",
    "workspace_register",
    "workspace_get",
    "workspace_list",
  ]);

  it("contains exactly 9 workspace command values", () => {
    expect(WORKSPACE_COMMANDS).toEqual(PYTHON_WORKSPACE);
  });
});

describe("MANIFEST_COMMANDS", () => {
  const PYTHON_MANIFEST = new Set([
    "manifest_list_abilities",
    "manifest_get_ability",
    "manifest_put_ability",
    "manifest_delete_ability",
    "manifest_list_agents",
    "manifest_get_agent",
    "manifest_put_agent",
    "manifest_delete_agent",
    "manifest_resolve_agent",
    "manifest_validate",
  ]);

  it("contains exactly 10 manifest command values", () => {
    expect(MANIFEST_COMMANDS).toEqual(PYTHON_MANIFEST);
  });
});

describe("TASK_COMMANDS", () => {
  const PYTHON_TASK = new Set([
    "task_create",
    "task_update",
    "task_assign",
    "task_start",
    "task_complete",
    "task_fail",
    "task_cancel",
    "task_block",
    "task_list",
    "task_get",
    "task_delete",
  ]);

  it("contains exactly 11 task command values", () => {
    expect(TASK_COMMANDS).toEqual(PYTHON_TASK);
  });
});

describe("TASK_ATTRIBUTION_COMMANDS", () => {
  const PYTHON_ATTRIBUTION = new Set([
    "task_submit_completion",
    "task_verify",
    "task_list_claims",
    "task_dump",
  ]);

  it("contains exactly 4 task attribution command values", () => {
    expect(TASK_ATTRIBUTION_COMMANDS).toEqual(PYTHON_ATTRIBUTION);
  });

  it("does not overlap with TASK_COMMANDS", () => {
    for (const command of TASK_ATTRIBUTION_COMMANDS) {
      expect(TASK_COMMANDS.has(command)).toBe(false);
    }
  });
});

describe("PLUGIN_COMMANDS", () => {
  const PYTHON_PLUGIN = new Set(["plugin_negotiate"]);

  it("contains exactly 1 plugin command value", () => {
    expect(PLUGIN_COMMANDS).toEqual(PYTHON_PLUGIN);
  });
});

describe("PROJECT_COMMANDS", () => {
  const PYTHON_PROJECT = new Set([
    "project_create",
    "project_update",
    "project_list",
    "project_get",
    "project_archive",
    "project_restore",
    "project_delete",
    "project_add_agent",
    "agent_entity",
    "agent_entity_create",
    "agent_entity_list",
    "project_remove_agent",
    "project_link_task",
    "project_unlink_task",
    "project_set_recovery",
    "project_pause",
    "project_resume",
    "project_stop",
  ]);

  it("contains exactly 18 project command values", () => {
    expect(PROJECT_COMMANDS).toEqual(PYTHON_PROJECT);
  });
});

describe("ROOM_COMMANDS", () => {
  const PYTHON_ROOM = new Set([
    "room_create",
    "room_list",
    "room_get",
    "room_update",
    "room_archive",
    "room_restore",
    "room_delete",
    "room_join",
    "room_leave",
    "room_get_members",
    "room_get_log",
    "room_send",
  ]);

  it("contains exactly 12 room command values", () => {
    expect(ROOM_COMMANDS).toEqual(PYTHON_ROOM);
  });
});
