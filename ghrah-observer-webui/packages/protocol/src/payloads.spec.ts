import { describe, expect, it } from "vitest";
import {
  AbilityDefinitionPayloadSchema,
  AbilityResultPayloadSchema,
  ActionChainUpdatedPayloadSchema,
  AgentConfigPayloadSchema,
  AgentErrorPayloadSchema,
  AgentResponsePayloadSchema,
  AgentSpawnedPayloadSchema,
  AgentTerminatedPayloadSchema,
  BroadcastMessagePayloadSchema,
  ClusterStatusPayloadSchema,
  CommandResultPayloadSchema,
  CreateWorkspacePayloadSchema,
  DelegatePayloadSchema,
  DestroyWorkspacePayloadSchema,
  ErrorPayloadSchema,
  ExecuteAbilityPayloadSchema,
  GetAgentInfoPayloadSchema,
  GetChainHistoryPayloadSchema,
  HealthCheckPayloadSchema,
  HealthStatusPayloadSchema,
  HITLRequestPayloadSchema,
  HITLResolvedPayloadSchema,
  HITLResponsePayloadSchema,
  InitClusterPayloadSchema,
  ListAgentsPayloadSchema,
  ListClustersPayloadSchema,
  ListClustersResultPayloadSchema,
  ManifestAbilityEventPayloadSchema,
  ManifestAgentEventPayloadSchema,
  ManifestDeletePayloadSchema,
  ManifestGetPayloadSchema,
  ManifestListPayloadSchema,
  ManifestPutPayloadSchema,
  ManifestResolvePayloadSchema,
  ManifestValidatePayloadSchema,
  PersistDeletePayloadSchema,
  PersistListPayloadSchema,
  PersistLoadPayloadSchema,
  PersistSavePayloadSchema,
  ProjectCreatePayloadSchema,
  ProjectDeletePayloadSchema,
  ProjectInfoPayloadSchema,
  ProjectLifecyclePayloadSchema,
  ProjectListPayloadSchema,
  ProjectUpdatePayloadSchema,
  RegisterAbilityPayloadSchema,
  RoomDeletePayloadSchema,
  RoomLifecyclePayloadSchema,
  SendMessagePayloadSchema,
  ShutdownClusterPayloadSchema,
  SpawnAgentPayloadSchema,
  SubscribePayloadSchema,
  TerminateAgentPayloadSchema,
  UnregisterAbilityPayloadSchema,
  UnsubscribePayloadSchema,
  WorkspaceCreatedPayloadSchema,
  WorkspaceDestroyedPayloadSchema,
  WorkspaceDiffPayloadSchema,
  WorkspaceGetPayloadSchema,
  WorkspaceListPayloadSchema,
  WorkspaceRegisterPayloadSchema,
  WorkspaceRollbackPayloadSchema,
  WorkspaceRolledBackPayloadSchema,
  WorkspaceSnapshotCreatedPayloadSchema,
  WorkspaceSnapshotPayloadSchema,
  WorkspaceStatusPayloadSchema,
} from "./payloads.js";

const AGENT_SCOPE = { project_id: "proj-001", agent_id: "agent-001" };

describe("Project payload schemas", () => {
  it("rejects removed legacy create fields", () => {
    expect(() =>
      ProjectCreatePayloadSchema.parse({
        name: "demo",
        default_workspace_locator: "/tmp/demo",
      }),
    ).toThrow();
  });

  it("rejects removed compatibility update fields", () => {
    expect(() =>
      ProjectUpdatePayloadSchema.parse({
        project_id: "project-1",
        instance_manifest_dir: "/tmp/manifests",
      }),
    ).toThrow();
  });

  it("uses explicit lifecycle and permanent-delete contracts", () => {
    expect(
      ProjectLifecyclePayloadSchema.parse({ project_id: "proj-001", expected_version: 3 }),
    ).toEqual({ project_id: "proj-001", expected_version: 3 });
    expect(
      ProjectDeletePayloadSchema.parse({ project_id: "proj-001", expected_version: 3 }),
    ).toEqual({ project_id: "proj-001", expected_version: 3, cascade_rooms: false });
    expect(ProjectListPayloadSchema.parse({}).archived).toBe(false);
    expect(ProjectDeletePayloadSchema.safeParse({ project_id: "proj-001" }).success).toBe(false);
  });

  it("requires optimistic locking for Room lifecycle and delete", () => {
    const target = { room_id: "room-001", expected_version: 5 };
    expect(RoomLifecyclePayloadSchema.parse(target)).toEqual(target);
    expect(RoomDeletePayloadSchema.parse(target)).toEqual(target);
    expect(RoomDeletePayloadSchema.safeParse({ room_id: "room-001" }).success).toBe(false);
  });

  it("preserves Project isolation and Agent runtime diagnostics", () => {
    const project = ProjectInfoPayloadSchema.parse({
      project_id: "proj-001",
      name: "demo",
      isolation: {
        agent_path_grants: { "agent-001": [{ workspace_id: "ws-1", subpath: "src" }] },
        agent_private_dir: false,
        task_scope: true,
      },
      agents: [
        {
          agent_id: "agent-001",
          name: "architect",
          cluster_id: "cluster-1",
          runtime_status: "error",
          runtime_error: "transport down",
        },
      ],
    });

    expect(project.isolation.agent_private_dir).toBe(false);
    expect(project.isolation.agent_path_grants["agent-001"]?.[0]?.subpath).toBe("src");
    expect(project.agents[0]?.runtime_status).toBe("error");
    expect(project.agents[0]?.runtime_error).toBe("transport down");
  });
});

describe("AgentConfigPayloadSchema", () => {
  it("parses with required name only", () => {
    const result = AgentConfigPayloadSchema.parse({ name: "test-agent" });
    expect(result.name).toBe("test-agent");
    expect(result.max_iterations).toBe(10);
    expect(result.description).toBe("");
    expect(result.system_prompt).toBe("");
    expect(result.endpoint_name).toBeUndefined();
  });

  it("parses with all fields", () => {
    const result = AgentConfigPayloadSchema.parse({
      name: "agent-1",
      endpoint_name: "default",
      description: "desc",
      system_prompt: "prompt",
      max_iterations: 5,
    });
    expect(result.max_iterations).toBe(5);
    expect(result.endpoint_name).toBe("default");
  });

  it("rejects missing name", () => {
    expect(() => AgentConfigPayloadSchema.parse({})).toThrow();
  });
});

describe("AbilityDefinitionPayloadSchema", () => {
  it("parses with required ability_type", () => {
    const result = AbilityDefinitionPayloadSchema.parse({ ability_type: "bash" });
    expect(result.ability_type).toBe("bash");
    expect(result.params).toEqual({});
  });

  it("parses with params", () => {
    const result = AbilityDefinitionPayloadSchema.parse({
      ability_type: "bash",
      params: { command: "ls" },
    });
    expect(result.params).toEqual({ command: "ls" });
  });
});

describe("SpawnAgentPayloadSchema", () => {
  it("parses with config only", () => {
    const result = SpawnAgentPayloadSchema.parse({
      project_id: "proj-001",
      config: { name: "agent-1" },
    });
    expect(result.config.name).toBe("agent-1");
    expect(result.abilities).toBeUndefined();
  });

  it("parses with abilities array", () => {
    const result = SpawnAgentPayloadSchema.parse({
      project_id: "proj-001",
      config: { name: "agent-1" },
      abilities: [{ ability_type: "bash" }],
    });
    expect(result.abilities).toHaveLength(1);
  });
});

describe("TerminateAgentPayloadSchema", () => {
  it("parses with name", () => {
    const result = TerminateAgentPayloadSchema.parse({ ...AGENT_SCOPE, name: "agent-1" });
    expect(result.name).toBe("agent-1");
  });

  it("rejects missing name", () => {
    expect(() => TerminateAgentPayloadSchema.parse({})).toThrow();
  });
});

describe("SendMessagePayloadSchema", () => {
  it("parses with required fields and default sender", () => {
    const result = SendMessagePayloadSchema.parse({
      project_id: "proj-001",
      agent_id: "agent-001",
      target: "agent-1",
      content: "hello",
    });
    expect(result.target).toBe("agent-1");
    expect(result.content).toBe("hello");
    expect(result.sender).toBe("user");
  });

  it("parses with custom sender", () => {
    const result = SendMessagePayloadSchema.parse({
      project_id: "proj-001",
      agent_id: "agent-001",
      target: "agent-1",
      content: "hello",
      sender: "agent-2",
      timeout: 30,
      metadata: { room_id: "room-1" },
    });
    expect(result.sender).toBe("agent-2");
    expect(result.timeout).toBe(30);
    expect(result.metadata).toEqual({ room_id: "room-1" });
  });
});

describe("BroadcastMessagePayloadSchema", () => {
  it("parses with content and default sender", () => {
    const result = BroadcastMessagePayloadSchema.parse({
      project_id: "proj-001",
      content: "hello all",
    });
    expect(result.content).toBe("hello all");
    expect(result.sender).toBe("user");
  });
});

describe("RegisterAbilityPayloadSchema", () => {
  it("parses with agent_name and ability", () => {
    const result = RegisterAbilityPayloadSchema.parse({
      ...AGENT_SCOPE,
      agent_name: "agent-1",
      ability: { ability_type: "bash" },
    });
    expect(result.agent_name).toBe("agent-1");
    expect(result.ability.ability_type).toBe("bash");
  });
});

describe("UnregisterAbilityPayloadSchema", () => {
  it("parses with agent_name and ability_name", () => {
    const result = UnregisterAbilityPayloadSchema.parse({
      ...AGENT_SCOPE,
      agent_name: "agent-1",
      ability_name: "bash",
    });
    expect(result.ability_name).toBe("bash");
  });
});

describe("ListAgentsPayloadSchema", () => {
  it("requires a Project scope", () => {
    const result = ListAgentsPayloadSchema.parse({ project_id: "proj-001" });
    expect(result).toEqual({ project_id: "proj-001" });
    expect(ListAgentsPayloadSchema.safeParse({}).success).toBe(false);
  });
});

describe("HealthCheckPayloadSchema", () => {
  it("parses empty object", () => {
    const result = HealthCheckPayloadSchema.parse({});
    expect(result).toEqual({});
  });
});

describe("DelegatePayloadSchema", () => {
  it("parses with all fields", () => {
    const result = DelegatePayloadSchema.parse({
      project_id: "proj-001",
      from_agent_id: "agent-001",
      to_agent_id: "agent-002",
      from_agent: "a1",
      to_agent: "a2",
      content: "delegate msg",
    });
    expect(result.from_agent).toBe("a1");
    expect(result.to_agent).toBe("a2");
  });
});

describe("GetAgentInfoPayloadSchema", () => {
  it("parses with name", () => {
    const result = GetAgentInfoPayloadSchema.parse({ ...AGENT_SCOPE, name: "agent-1" });
    expect(result.name).toBe("agent-1");
  });
});

describe("Project-scoped compatibility", () => {
  it("rejects new Agent commands without project_id/agent_id", () => {
    expect(TerminateAgentPayloadSchema.safeParse({ name: "agent-1" }).success).toBe(false);
    expect(GetChainHistoryPayloadSchema.safeParse({ agent_name: "agent-1" }).success).toBe(false);
  });

  it("accepts legacy events but exposes empty ownership fields", () => {
    const event = AgentErrorPayloadSchema.parse({ agent_name: "agent-1", error: "boom" });
    expect(event).toMatchObject({ project_id: "", agent_id: "", cluster_id: "" });
  });
});

describe("SubscribePayloadSchema", () => {
  it("parses with agent_names only", () => {
    const result = SubscribePayloadSchema.parse({
      agent_names: ["agent-1", "agent-2"],
    });
    expect(result.agent_names).toEqual(["agent-1", "agent-2"]);
    expect(result.event_types).toBeUndefined();
  });

  it("parses with null values", () => {
    const result = SubscribePayloadSchema.parse({
      agent_names: null,
      event_types: null,
    });
    expect(result.agent_names).toBeNull();
  });

  it("parses empty object (subscribe all)", () => {
    const result = SubscribePayloadSchema.parse({});
    expect(result.agent_names).toBeUndefined();
    expect(result.event_types).toBeUndefined();
  });
});

describe("UnsubscribePayloadSchema", () => {
  it("parses same as SubscribePayloadSchema", () => {
    const result = UnsubscribePayloadSchema.parse({
      agent_names: ["agent-1"],
    });
    expect(result.agent_names).toEqual(["agent-1"]);
  });
});

describe("ExecuteAbilityPayloadSchema", () => {
  it("parses with required fields and default tool_args", () => {
    const result = ExecuteAbilityPayloadSchema.parse({
      request_id: "req-001",
      agent_name: "agent-1",
      ability_name: "read_file",
    });
    expect(result.request_id).toBe("req-001");
    expect(result.tool_args).toEqual({});
  });

  it("parses with tool_args", () => {
    const result = ExecuteAbilityPayloadSchema.parse({
      request_id: "req-001",
      agent_name: "agent-1",
      ability_name: "read_file",
      tool_args: { path: "/tmp/test.txt" },
    });
    expect(result.tool_args).toEqual({ path: "/tmp/test.txt" });
  });
});

describe("HITLResponsePayloadSchema", () => {
  it("parses with required fields", () => {
    const result = HITLResponsePayloadSchema.parse({
      promise_id: "p-001",
      approved: true,
    });
    expect(result.promise_id).toBe("p-001");
    expect(result.approved).toBe(true);
    expect(result.reason).toBeUndefined();
  });

  it("parses with reason", () => {
    const result = HITLResponsePayloadSchema.parse({
      promise_id: "p-001",
      approved: false,
      reason: "unsafe",
    });
    expect(result.reason).toBe("unsafe");
  });

  it("parses the complete triplet without promise_id", () => {
    const result = HITLResponsePayloadSchema.parse({
      agent_name: "a",
      ability_name: "write_file",
      tool_call_id: "call-1",
      approved: true,
    });
    expect(result.agent_name).toBe("a");
  });

  it("rejects degenerate payload with no locator (fail-closed)", () => {
    expect(() => HITLResponsePayloadSchema.parse({ approved: true })).toThrow();
  });

  it("rejects partial triplet without promise_id", () => {
    expect(() =>
      HITLResponsePayloadSchema.parse({
        agent_name: "a",
        ability_name: "write_file",
        approved: true,
      }),
    ).toThrow();
  });
});

describe("HITLResolvedPayloadSchema", () => {
  it("parses with terminal status", () => {
    const result = HITLResolvedPayloadSchema.parse({
      promise_id: "p-001",
      agent_name: "agent-1",
      ability_name: "bash",
      tool_call_id: "call-1",
      status: "timeout",
    });
    expect(result.status).toBe("timeout");
    expect(result.promise_id).toBe("p-001");
  });

  it("rejects unknown status", () => {
    expect(() =>
      HITLResolvedPayloadSchema.parse({
        promise_id: "p-001",
        agent_name: "agent-1",
        ability_name: "bash",
        status: "expired",
      }),
    ).toThrow();
  });
});

describe("InitClusterPayloadSchema", () => {
  it("parses with required cluster_id and default config", () => {
    const result = InitClusterPayloadSchema.parse({ cluster_id: "cluster-1" });
    expect(result.cluster_id).toBe("cluster-1");
    expect(result.config).toEqual({});
  });

  it("rejects empty payload (cluster_id required)", () => {
    expect(InitClusterPayloadSchema.safeParse({}).success).toBe(false);
  });
});

describe("ShutdownClusterPayloadSchema", () => {
  it("parses with required cluster_id and default config", () => {
    const result = ShutdownClusterPayloadSchema.parse({ cluster_id: "cluster-1" });
    expect(result.cluster_id).toBe("cluster-1");
    expect(result.config).toEqual({});
  });

  it("rejects empty payload (cluster_id required)", () => {
    expect(ShutdownClusterPayloadSchema.safeParse({}).success).toBe(false);
  });
});

describe("ClusterStatusPayloadSchema", () => {
  it("parses with required cluster_id", () => {
    const result = ClusterStatusPayloadSchema.parse({ cluster_id: "cluster-1" });
    expect(result).toEqual({ cluster_id: "cluster-1" });
  });

  it("rejects empty payload (cluster_id required)", () => {
    expect(ClusterStatusPayloadSchema.safeParse({}).success).toBe(false);
  });
});

describe("ListClustersPayloadSchema", () => {
  it("parses empty object", () => {
    const result = ListClustersPayloadSchema.parse({});
    expect(result).toEqual({});
  });
});

describe("ListClustersResultPayloadSchema", () => {
  it("parses clusters list", () => {
    const result = ListClustersResultPayloadSchema.parse({
      clusters: [{ cluster_id: "cluster-1", active_agents: 2, status: "running", bound: true }],
    });
    expect(result.clusters).toHaveLength(1);
    expect(result.clusters[0].cluster_id).toBe("cluster-1");
    expect(result.clusters[0].bound).toBe(true);
  });
});

describe("PersistSavePayloadSchema", () => {
  it("parses with required fields and default namespace", () => {
    const result = PersistSavePayloadSchema.parse({
      key: "ctx-1",
      data: { messages: [] },
    });
    expect(result.key).toBe("ctx-1");
    expect(result.namespace).toBe("default");
  });

  it("parses with custom namespace", () => {
    const result = PersistSavePayloadSchema.parse({
      key: "ctx-1",
      data: {},
      namespace: "agent-1",
    });
    expect(result.namespace).toBe("agent-1");
  });
});

describe("PersistLoadPayloadSchema", () => {
  it("parses with key and default namespace", () => {
    const result = PersistLoadPayloadSchema.parse({ key: "ctx-1" });
    expect(result.namespace).toBe("default");
  });
});

describe("PersistDeletePayloadSchema", () => {
  it("parses with key and default namespace", () => {
    const result = PersistDeletePayloadSchema.parse({ key: "ctx-1" });
    expect(result.namespace).toBe("default");
  });
});

describe("PersistListPayloadSchema", () => {
  it("parses with defaults", () => {
    const result = PersistListPayloadSchema.parse({});
    expect(result.namespace).toBe("default");
    expect(result.prefix).toBeUndefined();
  });

  it("parses with namespace and prefix", () => {
    const result = PersistListPayloadSchema.parse({
      namespace: "agent-1",
      prefix: "ctx",
    });
    expect(result.namespace).toBe("agent-1");
    expect(result.prefix).toBe("ctx");
  });
});

describe("Workspace command payload schemas", () => {
  it("CreateWorkspacePayloadSchema", () => {
    const result = CreateWorkspacePayloadSchema.parse({ ...AGENT_SCOPE, agent_name: "agent-1" });
    expect(result.agent_name).toBe("agent-1");
  });

  it("DestroyWorkspacePayloadSchema", () => {
    const result = DestroyWorkspacePayloadSchema.parse({ ...AGENT_SCOPE, agent_name: "agent-1" });
    expect(result.agent_name).toBe("agent-1");
  });

  it("WorkspaceSnapshotPayloadSchema with default message", () => {
    const result = WorkspaceSnapshotPayloadSchema.parse({ ...AGENT_SCOPE, agent_name: "agent-1" });
    expect(result.message).toBe("");
  });

  it("WorkspaceRollbackPayloadSchema", () => {
    const result = WorkspaceRollbackPayloadSchema.parse({
      ...AGENT_SCOPE,
      agent_name: "agent-1",
      snapshot_id: "snap-001",
    });
    expect(result.snapshot_id).toBe("snap-001");
  });

  it("WorkspaceDiffPayloadSchema with optional snapshot_id", () => {
    const result = WorkspaceDiffPayloadSchema.parse({ ...AGENT_SCOPE, agent_name: "agent-1" });
    expect(result.snapshot_id).toBeUndefined();
  });

  it("WorkspaceStatusPayloadSchema", () => {
    const result = WorkspaceStatusPayloadSchema.parse({ ...AGENT_SCOPE, agent_name: "agent-1" });
    expect(result.agent_name).toBe("agent-1");
  });

  it("WorkspaceRegisterPayloadSchema with provider_type", () => {
    const result = WorkspaceRegisterPayloadSchema.parse({
      locator: "file:///tmp/agent-1",
      name: "agent-1",
      provider_type: "git",
    });
    expect(result.locator).toBe("file:///tmp/agent-1");
    expect(result.provider_type).toBe("git");
  });

  it("WorkspaceRegisterPayloadSchema defaults name and optional provider_type", () => {
    const result = WorkspaceRegisterPayloadSchema.parse({
      locator: "file:///tmp/data",
    });
    expect(result.name).toBe("");
    expect(result.provider_type).toBeUndefined();
  });

  it("WorkspaceGetPayloadSchema", () => {
    const result = WorkspaceGetPayloadSchema.parse({ workspace_id: "wid-001" });
    expect(result.workspace_id).toBe("wid-001");
  });

  it("WorkspaceListPayloadSchema defaults to undefined filter", () => {
    const result = WorkspaceListPayloadSchema.parse({});
    expect(result.provider_type).toBeUndefined();
  });

  it("WorkspaceListPayloadSchema with provider_type filter", () => {
    const result = WorkspaceListPayloadSchema.parse({ provider_type: "git" });
    expect(result.provider_type).toBe("git");
  });
});

describe("Event payload schemas", () => {
  it("AgentSpawnedPayloadSchema", () => {
    const result = AgentSpawnedPayloadSchema.parse({
      name: "agent-1",
      agent_id: "stable-1",
      incarnation_id: "inc-1",
      recovery_mode: "restored",
      config: { name: "agent-1", agent_id: "stable-1" },
    });
    expect(result.name).toBe("agent-1");
    expect(result.agent_id).toBe("stable-1");
    expect(result.recovery_mode).toBe("restored");
    expect(result.config.max_iterations).toBe(10);
  });

  it("AgentTerminatedPayloadSchema", () => {
    const result = AgentTerminatedPayloadSchema.parse({ name: "agent-1" });
    expect(result.name).toBe("agent-1");
  });

  it("AgentResponsePayloadSchema with defaults", () => {
    const result = AgentResponsePayloadSchema.parse({
      sender: "agent-1",
      recipient: "user",
      content: "result",
    });
    expect(result.message_type).toBe("result");
    expect(result.metadata).toEqual({});
  });

  it("ActionChainUpdatedPayloadSchema with default node", () => {
    const result = ActionChainUpdatedPayloadSchema.parse({
      agent_name: "agent-1",
    });
    expect(result.node).toMatchObject({
      id: "",
      agent_name: "",
      iteration: 0,
      ability_names: [],
      agent_state: {},
      messages_delta: [],
      action_results: [],
      metadata: {},
      session_id: "",
      created_on_branch_id: "",
      is_snapshot: false,
    });
    // 未提供时无默认值的可选字段应为 undefined。
    expect(result.node.parent_id).toBeUndefined();
    expect(result.node.messages_snapshot).toBeUndefined();
    expect(result.node.timestamp).toBeUndefined();
  });

  it("AgentErrorPayloadSchema", () => {
    const result = AgentErrorPayloadSchema.parse({
      agent_name: "agent-1",
      error: "crashed",
    });
    expect(result.error).toBe("crashed");
  });

  it("HealthStatusPayloadSchema", () => {
    const result = HealthStatusPayloadSchema.parse({
      status: { agent_1: true, agent_2: false },
    });
    expect(result.status).toEqual({ agent_1: true, agent_2: false });
  });

  it("AbilityResultPayloadSchema success", () => {
    const result = AbilityResultPayloadSchema.parse({
      request_id: "req-001",
      agent_name: "agent-1",
      ability_name: "read_file",
      success: true,
      result: { content: "file data" },
    });
    expect(result.success).toBe(true);
    expect(result.error).toBeUndefined();
  });

  it("AbilityResultPayloadSchema failure", () => {
    const result = AbilityResultPayloadSchema.parse({
      request_id: "req-002",
      agent_name: "agent-1",
      ability_name: "write_file",
      success: false,
      error: "Permission denied",
    });
    expect(result.success).toBe(false);
    expect(result.result).toBeUndefined();
  });

  it("HITLRequestPayloadSchema with defaults", () => {
    const result = HITLRequestPayloadSchema.parse({
      promise_id: "p-001",
      agent_name: "agent-1",
      ability_name: "bash",
    });
    expect(result.tool_args).toEqual({});
    expect(result.context).toEqual({});
    expect(result.reason).toBe("");
  });

  it("HITLRequestPayloadSchema parses approval reason", () => {
    const result = HITLRequestPayloadSchema.parse({
      promise_id: "p-002",
      agent_name: "agent-1",
      ability_name: "execute_command",
      reason: "Command requires approval: pnpm type-check",
    });
    expect(result.reason).toBe("Command requires approval: pnpm type-check");
  });
});

describe("Workspace event payload schemas", () => {
  it("WorkspaceCreatedPayloadSchema", () => {
    const result = WorkspaceCreatedPayloadSchema.parse({
      agent_name: "agent-1",
      path: "/tmp/agent-1",
    });
    expect(result.path).toBe("/tmp/agent-1");
  });

  it("WorkspaceDestroyedPayloadSchema", () => {
    const result = WorkspaceDestroyedPayloadSchema.parse({ agent_name: "agent-1" });
    expect(result.agent_name).toBe("agent-1");
  });

  it("WorkspaceSnapshotCreatedPayloadSchema with default message", () => {
    const result = WorkspaceSnapshotCreatedPayloadSchema.parse({
      agent_name: "agent-1",
      snapshot_id: "snap-001",
    });
    expect(result.message).toBe("");
  });

  it("WorkspaceRolledBackPayloadSchema", () => {
    const result = WorkspaceRolledBackPayloadSchema.parse({
      agent_name: "agent-1",
      snapshot_id: "snap-001",
    });
    expect(result.snapshot_id).toBe("snap-001");
  });
});

describe("System payload schemas", () => {
  it("CommandResultPayloadSchema success", () => {
    const result = CommandResultPayloadSchema.parse({
      request_id: "req-001",
      success: true,
      data: { name: "agent-1" },
    });
    expect(result.success).toBe(true);
    expect(result.error).toBeUndefined();
  });

  it("CommandResultPayloadSchema failure", () => {
    const result = CommandResultPayloadSchema.parse({
      request_id: "req-001",
      success: false,
      error: "agent_not_found",
      error_detail: "Agent a1 was not found",
    });
    expect(result.data).toBeUndefined();
    expect(result.error).toBe("agent_not_found");
    expect(result.error_detail).toBe("Agent a1 was not found");
  });

  it("ErrorPayloadSchema", () => {
    const result = ErrorPayloadSchema.parse({
      code: "UNKNOWN_COMMAND",
      message: "Unknown command type: foo",
    });
    expect(result.code).toBe("UNKNOWN_COMMAND");
    expect(result.details).toBeUndefined();
  });

  it("ErrorPayloadSchema with details", () => {
    const result = ErrorPayloadSchema.parse({
      code: "VALIDATION_ERROR",
      message: "Invalid payload",
      details: { field: "name" },
    });
    expect(result.details).toEqual({ field: "name" });
  });
});

describe("Manifest payload schemas", () => {
  it("ManifestListPayloadSchema with namespace", () => {
    const result = ManifestListPayloadSchema.parse({ namespace: "ghrah.fs" });
    expect(result.namespace).toBe("ghrah.fs");
  });

  it("ManifestListPayloadSchema without namespace", () => {
    const result = ManifestListPayloadSchema.parse({});
    expect(result.namespace).toBeUndefined();
  });

  it("ManifestGetPayloadSchema", () => {
    const result = ManifestGetPayloadSchema.parse({ full_name: "ghrah.fs.read_file" });
    expect(result.full_name).toBe("ghrah.fs.read_file");
  });

  it("ManifestPutPayloadSchema with default overwrite", () => {
    const result = ManifestPutPayloadSchema.parse({
      full_name: "ghrah.fs.read_file",
      content: "yaml: content",
    });
    expect(result.full_name).toBe("ghrah.fs.read_file");
    expect(result.content).toBe("yaml: content");
    expect(result.overwrite).toBe(false);
  });

  it("ManifestPutPayloadSchema with overwrite=true", () => {
    const result = ManifestPutPayloadSchema.parse({
      full_name: "ghrah.fs.read_file",
      content: "yaml: updated",
      overwrite: true,
    });
    expect(result.overwrite).toBe(true);
  });

  it("ManifestDeletePayloadSchema", () => {
    const result = ManifestDeletePayloadSchema.parse({ full_name: "ghrah.fs.read_file" });
    expect(result.full_name).toBe("ghrah.fs.read_file");
  });

  it("ManifestValidatePayloadSchema", () => {
    const result = ManifestValidatePayloadSchema.parse({
      content: "yaml: content",
      manifest_type: "ability",
    });
    expect(result.content).toBe("yaml: content");
    expect(result.manifest_type).toBe("ability");
  });

  it("ManifestResolvePayloadSchema without runtime_name", () => {
    const result = ManifestResolvePayloadSchema.parse({
      agent_full_name: "my_project.designer",
    });
    expect(result.agent_full_name).toBe("my_project.designer");
    expect(result.runtime_name).toBeUndefined();
  });

  it("ManifestResolvePayloadSchema with runtime_name", () => {
    const result = ManifestResolvePayloadSchema.parse({
      agent_full_name: "my_project.designer",
      runtime_name: "dev-agent-1",
    });
    expect(result.runtime_name).toBe("dev-agent-1");
  });

  it("ManifestAbilityEventPayloadSchema", () => {
    const result = ManifestAbilityEventPayloadSchema.parse({
      full_name: "ghrah.fs.read_file",
      namespace: "ghrah.fs",
    });
    expect(result.full_name).toBe("ghrah.fs.read_file");
    expect(result.namespace).toBe("ghrah.fs");
  });

  it("ManifestAgentEventPayloadSchema", () => {
    const result = ManifestAgentEventPayloadSchema.parse({
      full_name: "my_project.designer",
      namespace: "my_project",
    });
    expect(result.full_name).toBe("my_project.designer");
    expect(result.namespace).toBe("my_project");
  });

  it("SpawnAgentPayloadSchema with manifest_ref", () => {
    const result = SpawnAgentPayloadSchema.parse({
      project_id: "proj-001",
      config: { name: "agent-1" },
      manifest_ref: "my_project.designer",
    });
    expect(result.manifest_ref).toBe("my_project.designer");
  });

  it("SpawnAgentPayloadSchema without manifest_ref", () => {
    const result = SpawnAgentPayloadSchema.parse({
      project_id: "proj-001",
      config: { name: "agent-1" },
    });
    expect(result.manifest_ref).toBeUndefined();
  });
});
