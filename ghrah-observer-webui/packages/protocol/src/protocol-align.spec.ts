// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseMessage, ServerMessageSchema, serializeMessage } from "./message.js";
import {
  AbilityDefinitionPayloadSchema,
  AbilityResultPayloadSchema,
  ActionChainUpdatedPayloadSchema,
  ActionNodeSchema,
  ActionResultItemSchema,
  AgentCompactContextPayloadSchema,
  AgentConfigPayloadSchema,
  AgentErrorPayloadSchema,
  AgentResetPayloadSchema,
  AgentResponsePayloadSchema,
  AgentSpawnedPayloadSchema,
  AgentTerminatedPayloadSchema,
  BranchActivatePayloadSchema,
  BranchArchivePayloadSchema,
  BranchCreatePayloadSchema,
  BranchDeletePayloadSchema,
  BranchEventPayloadSchema,
  BranchInfoPayloadSchema,
  BranchLifecyclePayloadSchema,
  BranchListPayloadSchema,
  BranchListResultPayloadSchema,
  BroadcastMessagePayloadSchema,
  ChainHistoryResultPayloadSchema,
  ChatMessageWireSchema,
  ClusterInfoPayloadSchema,
  ClusterStatusPayloadSchema,
  CommandResultPayloadSchema,
  ContextUsageUpdatedPayloadSchema,
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
  PersistDeletePayloadSchema,
  PersistListPayloadSchema,
  PersistLoadPayloadSchema,
  PersistSavePayloadSchema,
  PluginCrashedPayloadSchema,
  PluginLifecyclePayloadSchema,
  PluginNegotiatedPayloadSchema,
  PluginNegotiatePayloadSchema,
  PluginNegotiateResultPayloadSchema,
  ProjectAddAgentPayloadSchema,
  ProjectAgentEventPayloadSchema,
  ProjectCreatePayloadSchema,
  ProjectDeletePayloadSchema,
  ProjectEventPayloadSchema,
  ProjectIdPayloadSchema,
  ProjectInfoPayloadSchema,
  ProjectLifecyclePayloadSchema,
  ProjectLinkTaskPayloadSchema,
  ProjectListPayloadSchema,
  ProjectListResultPayloadSchema,
  ProjectRemoveAgentPayloadSchema,
  ProjectSetRecoveryPayloadSchema,
  ProjectUnlinkTaskPayloadSchema,
  ProjectUpdatePayloadSchema,
  RegisterAbilityPayloadSchema,
  RoomCreatePayloadSchema,
  RoomDeletedEventPayloadSchema,
  RoomDeletePayloadSchema,
  RoomEventPayloadSchema,
  RoomGetLogPayloadSchema,
  RoomIdPayloadSchema,
  RoomInfoPayloadSchema,
  RoomJoinPayloadSchema,
  RoomLeavePayloadSchema,
  RoomLifecyclePayloadSchema,
  RoomListPayloadSchema,
  RoomListResultPayloadSchema,
  RoomLogEntryPayloadSchema,
  RoomLogEventPayloadSchema,
  RoomLogResultPayloadSchema,
  RoomMemberEventPayloadSchema,
  RoomMemberSchema,
  RoomSendPayloadSchema,
  RoomUpdatePayloadSchema,
  SendMessagePayloadSchema,
  SessionActivatedPayloadSchema,
  SessionActivatePayloadSchema,
  SessionArchivedPayloadSchema,
  SessionArchivePayloadSchema,
  SessionCreatedPayloadSchema,
  SessionCreatePayloadSchema,
  SessionDeletedPayloadSchema,
  SessionDeletePayloadSchema,
  SessionInfoPayloadSchema,
  SessionListPayloadSchema,
  SessionListResultPayloadSchema,
  ShutdownClusterPayloadSchema,
  SpawnAgentPayloadSchema,
  SubscribePayloadSchema,
  TaskAssignPayloadSchema,
  TaskBlockPayloadSchema,
  TaskCancelPayloadSchema,
  TaskClaimEventPayloadSchema,
  TaskClaimListPayloadSchema,
  TaskClaimListResultPayloadSchema,
  TaskClaimPayloadSchema,
  TaskCompletePayloadSchema,
  TaskCreatePayloadSchema,
  TaskDeletePayloadSchema,
  TaskDumpPayloadSchema,
  TaskDumpResultPayloadSchema,
  TaskEventPayloadSchema,
  TaskEvidencePayloadSchema,
  TaskFailPayloadSchema,
  TaskIdPayloadSchema,
  TaskInfoPayloadSchema,
  TaskListPayloadSchema,
  TaskListResultPayloadSchema,
  TaskSubmitCompletionPayloadSchema,
  TaskUpdatePayloadSchema,
  TaskVerificationGapsPayloadSchema,
  TaskVerifyPayloadSchema,
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

const SNAPSHOTS_DIR = path.join(import.meta.dirname, "__snapshots__");

function loadSnapshot(name: string): Record<string, unknown> {
  const filePath = path.join(SNAPSHOTS_DIR, `${name}.json`);
  return JSON.parse(fs.readFileSync(filePath, "utf-8")) as Record<string, unknown>;
}

const PAYLOAD_SCHEMA_MAP: Record<string, import("zod").ZodTypeAny> = {
  AgentConfigPayload: AgentConfigPayloadSchema,
  AgentConfigPayload_full: AgentConfigPayloadSchema,
  AbilityDefinitionPayload: AbilityDefinitionPayloadSchema,
  SpawnAgentPayload: SpawnAgentPayloadSchema,
  SpawnAgentPayload_minimal: SpawnAgentPayloadSchema,
  TerminateAgentPayload: TerminateAgentPayloadSchema,
  SendMessagePayload: SendMessagePayloadSchema,
  SendMessagePayload_default_sender: SendMessagePayloadSchema,
  BroadcastMessagePayload: BroadcastMessagePayloadSchema,
  RegisterAbilityPayload: RegisterAbilityPayloadSchema,
  UnregisterAbilityPayload: UnregisterAbilityPayloadSchema,
  ListAgentsPayload: ListAgentsPayloadSchema,
  HealthCheckPayload: HealthCheckPayloadSchema,
  DelegatePayload: DelegatePayloadSchema,
  GetAgentInfoPayload: GetAgentInfoPayloadSchema,
  AgentCompactContextPayload: AgentCompactContextPayloadSchema,
  AgentResetPayload: AgentResetPayloadSchema,
  GetChainHistoryPayload: GetChainHistoryPayloadSchema,
  ChainHistoryResultPayload: ChainHistoryResultPayloadSchema,
  SubscribePayload: SubscribePayloadSchema,
  SubscribePayload_all: SubscribePayloadSchema,
  UnsubscribePayload: UnsubscribePayloadSchema,
  ExecuteAbilityPayload: ExecuteAbilityPayloadSchema,
  ExecuteAbilityPayload_default_args: ExecuteAbilityPayloadSchema,
  HITLResponsePayload: HITLResponsePayloadSchema,
  HITLResponsePayload_with_reason: HITLResponsePayloadSchema,
  InitClusterPayload: InitClusterPayloadSchema,
  ShutdownClusterPayload: ShutdownClusterPayloadSchema,
  ClusterStatusPayload: ClusterStatusPayloadSchema,
  ListClustersPayload: ListClustersPayloadSchema,
  ClusterInfoPayload: ClusterInfoPayloadSchema,
  ListClustersResultPayload: ListClustersResultPayloadSchema,
  PersistSavePayload: PersistSavePayloadSchema,
  PersistSavePayload_default_ns: PersistSavePayloadSchema,
  PersistLoadPayload: PersistLoadPayloadSchema,
  PersistDeletePayload: PersistDeletePayloadSchema,
  PersistListPayload: PersistListPayloadSchema,
  PersistListPayload_defaults: PersistListPayloadSchema,
  CreateWorkspacePayload: CreateWorkspacePayloadSchema,
  DestroyWorkspacePayload: DestroyWorkspacePayloadSchema,
  WorkspaceSnapshotPayload: WorkspaceSnapshotPayloadSchema,
  WorkspaceSnapshotPayload_default_msg: WorkspaceSnapshotPayloadSchema,
  WorkspaceRollbackPayload: WorkspaceRollbackPayloadSchema,
  WorkspaceDiffPayload: WorkspaceDiffPayloadSchema,
  WorkspaceDiffPayload_null_snapshot: WorkspaceDiffPayloadSchema,
  WorkspaceStatusPayload: WorkspaceStatusPayloadSchema,
  WorkspaceRegisterPayload: WorkspaceRegisterPayloadSchema,
  WorkspaceRegisterPayload_auto: WorkspaceRegisterPayloadSchema,
  WorkspaceGetPayload: WorkspaceGetPayloadSchema,
  WorkspaceListPayload: WorkspaceListPayloadSchema,
  WorkspaceListPayload_filter: WorkspaceListPayloadSchema,
  AgentSpawnedPayload: AgentSpawnedPayloadSchema,
  AgentTerminatedPayload: AgentTerminatedPayloadSchema,
  AgentResponsePayload: AgentResponsePayloadSchema,
  AgentResponsePayload_defaults: AgentResponsePayloadSchema,
  ActionChainUpdatedPayload: ActionChainUpdatedPayloadSchema,
  ContextUsageUpdatedPayload: ContextUsageUpdatedPayloadSchema,
  ContextUsageUpdatedPayload_pre_call: ContextUsageUpdatedPayloadSchema,
  AgentErrorPayload: AgentErrorPayloadSchema,
  HealthStatusPayload: HealthStatusPayloadSchema,
  AbilityResultPayload_success: AbilityResultPayloadSchema,
  AbilityResultPayload_failure: AbilityResultPayloadSchema,
  HITLRequestPayload: HITLRequestPayloadSchema,
  HITLRequestPayload_with_args: HITLRequestPayloadSchema,
  HITLResolvedPayload: HITLResolvedPayloadSchema,
  WorkspaceCreatedPayload: WorkspaceCreatedPayloadSchema,
  WorkspaceDestroyedPayload: WorkspaceDestroyedPayloadSchema,
  WorkspaceSnapshotCreatedPayload: WorkspaceSnapshotCreatedPayloadSchema,
  WorkspaceRolledBackPayload: WorkspaceRolledBackPayloadSchema,
  CommandResultPayload_success: CommandResultPayloadSchema,
  CommandResultPayload_failure: CommandResultPayloadSchema,
  ErrorPayload: ErrorPayloadSchema,
  ErrorPayload_with_details: ErrorPayloadSchema,
  TaskInfoPayload: TaskInfoPayloadSchema,
  TaskCreatePayload: TaskCreatePayloadSchema,
  TaskUpdatePayload: TaskUpdatePayloadSchema,
  TaskIdPayload: TaskIdPayloadSchema,
  TaskAssignPayload: TaskAssignPayloadSchema,
  TaskCompletePayload: TaskCompletePayloadSchema,
  TaskFailPayload: TaskFailPayloadSchema,
  TaskCancelPayload: TaskCancelPayloadSchema,
  TaskBlockPayload: TaskBlockPayloadSchema,
  TaskListPayload: TaskListPayloadSchema,
  TaskDeletePayload: TaskDeletePayloadSchema,
  TaskListResultPayload: TaskListResultPayloadSchema,
  TaskEventPayload: TaskEventPayloadSchema,
  // S1 新增：Task 归因切面 + Plugin 协商/事件（Python gen_s1_snapshots.py 产物）
  TaskInfoPayload_verification: TaskInfoPayloadSchema,
  TaskEvidencePayload: TaskEvidencePayloadSchema,
  TaskClaimPayload: TaskClaimPayloadSchema,
  TaskSubmitCompletionPayload: TaskSubmitCompletionPayloadSchema,
  TaskVerifyPayload: TaskVerifyPayloadSchema,
  TaskClaimListPayload: TaskClaimListPayloadSchema,
  TaskClaimListResultPayload: TaskClaimListResultPayloadSchema,
  TaskClaimEventPayload: TaskClaimEventPayloadSchema,
  TaskVerificationGapsPayload: TaskVerificationGapsPayloadSchema,
  TaskDumpPayload: TaskDumpPayloadSchema,
  TaskDumpResultPayload: TaskDumpResultPayloadSchema,
  PluginNegotiatePayload: PluginNegotiatePayloadSchema,
  PluginNegotiateResultPayload: PluginNegotiateResultPayloadSchema,
  PluginLifecyclePayload: PluginLifecyclePayloadSchema,
  PluginNegotiatedPayload: PluginNegotiatedPayloadSchema,
  PluginCrashedPayload: PluginCrashedPayloadSchema,
  ProjectInfoPayload: ProjectInfoPayloadSchema,
  ProjectCreatePayload: ProjectCreatePayloadSchema,
  ProjectUpdatePayload: ProjectUpdatePayloadSchema,
  ProjectIdPayload: ProjectIdPayloadSchema,
  ProjectLifecyclePayload: ProjectLifecyclePayloadSchema,
  ProjectDeletePayload: ProjectDeletePayloadSchema,
  ProjectListPayload: ProjectListPayloadSchema,
  ProjectAddAgentPayload: ProjectAddAgentPayloadSchema,
  ProjectRemoveAgentPayload: ProjectRemoveAgentPayloadSchema,
  ProjectLinkTaskPayload: ProjectLinkTaskPayloadSchema,
  ProjectUnlinkTaskPayload: ProjectUnlinkTaskPayloadSchema,
  ProjectSetRecoveryPayload: ProjectSetRecoveryPayloadSchema,
  ProjectListResultPayload: ProjectListResultPayloadSchema,
  ProjectEventPayload: ProjectEventPayloadSchema,
  ProjectAgentEventPayload: ProjectAgentEventPayloadSchema,
  RoomMember: RoomMemberSchema,
  RoomInfoPayload: RoomInfoPayloadSchema,
  RoomLogEntryPayload: RoomLogEntryPayloadSchema,
  RoomCreatePayload: RoomCreatePayloadSchema,
  RoomListPayload: RoomListPayloadSchema,
  RoomListResultPayload: RoomListResultPayloadSchema,
  RoomIdPayload: RoomIdPayloadSchema,
  RoomUpdatePayload: RoomUpdatePayloadSchema,
  RoomLifecyclePayload: RoomLifecyclePayloadSchema,
  RoomDeletePayload: RoomDeletePayloadSchema,
  RoomJoinPayload: RoomJoinPayloadSchema,
  RoomLeavePayload: RoomLeavePayloadSchema,
  RoomGetLogPayload: RoomGetLogPayloadSchema,
  RoomLogResultPayload: RoomLogResultPayloadSchema,
  RoomSendPayload: RoomSendPayloadSchema,
  RoomEventPayload: RoomEventPayloadSchema,
  RoomDeletedEventPayload: RoomDeletedEventPayloadSchema,
  RoomMemberEventPayload: RoomMemberEventPayloadSchema,
  RoomLogEventPayload: RoomLogEventPayloadSchema,
  SessionCreatePayload: SessionCreatePayloadSchema,
  SessionActivatePayload: SessionActivatePayloadSchema,
  SessionListPayload: SessionListPayloadSchema,
  SessionArchivePayload: SessionArchivePayloadSchema,
  SessionDeletePayload: SessionDeletePayloadSchema,
  SessionInfoPayload: SessionInfoPayloadSchema,
  SessionCreatedPayload: SessionCreatedPayloadSchema,
  SessionActivatedPayload: SessionActivatedPayloadSchema,
  SessionArchivedPayload: SessionArchivedPayloadSchema,
  SessionDeletedPayload: SessionDeletedPayloadSchema,
  SessionListResultPayload: SessionListResultPayloadSchema,
  BranchCreatePayload: BranchCreatePayloadSchema,
  BranchActivatePayload: BranchActivatePayloadSchema,
  BranchListPayload: BranchListPayloadSchema,
  BranchArchivePayload: BranchArchivePayloadSchema,
  BranchDeletePayload: BranchDeletePayloadSchema,
  BranchInfoPayload: BranchInfoPayloadSchema,
  BranchEventPayload: BranchEventPayloadSchema,
  BranchLifecyclePayload: BranchLifecyclePayloadSchema,
  BranchListResultPayload: BranchListResultPayloadSchema,
};

describe("Pydantic-Zod cross-validation", () => {
  const snapshotFiles = fs.readdirSync(SNAPSHOTS_DIR).filter((f) => f.endsWith(".json"));

  it("all payload schemas have corresponding snapshots", () => {
    const schemaNames = Object.keys(PAYLOAD_SCHEMA_MAP);
    const snapshotNames = snapshotFiles.map((f) => f.replace(".json", ""));
    for (const name of schemaNames) {
      expect(snapshotNames).toContain(name);
    }
  });

  for (const file of snapshotFiles) {
    const name = file.replace(".json", "");
    const schema = PAYLOAD_SCHEMA_MAP[name];

    if (!schema) continue;

    it(`Zod parses Python snapshot for ${name}`, () => {
      const pythonSnapshot = loadSnapshot(name);

      const parsed = schema.parse(pythonSnapshot);

      const pythonKeys = Object.keys(pythonSnapshot).sort();
      const tsKeys = Object.keys(parsed).sort();

      for (const key of pythonKeys) {
        expect(tsKeys).toContain(key);
      }
    });

    it(`TS parsed output keys match Python for ${name}`, () => {
      const pythonSnapshot = loadSnapshot(name);

      const parsed = schema.parse(pythonSnapshot);

      const pythonKeys = new Set(Object.keys(pythonSnapshot));
      const tsKeys = new Set(Object.keys(parsed));

      const missingFromTs = [...pythonKeys].filter((k) => !tsKeys.has(k));
      const extraInTs = [...tsKeys].filter((k) => !pythonKeys.has(k));

      if (missingFromTs.length > 0) {
        expect(
          missingFromTs.every((k) => pythonSnapshot[k] === null),
          `Keys missing from TS: ${missingFromTs.join(", ")} — acceptable only if Python value was null`,
        ).toBe(true);
      }

      if (extraInTs.length > 0) {
        expect(
          extraInTs.every((k) => parsed[k] === undefined || parsed[k] === null),
          `Extra keys in TS: ${extraInTs.join(", ")} — acceptable only if value is undefined/null`,
        ).toBe(true);
      }
    });
  }
});

describe("ServerMessage cross-validation", () => {
  it("Zod parses Python minimal ServerMessage snapshot", () => {
    const pythonSnapshot = loadSnapshot("Message_minimal");
    const parsed = ServerMessageSchema.parse(pythonSnapshot);
    expect(parsed.type).toBe("ping");
    expect(parsed.payload).toEqual({});
  });

  it("Zod parses Python full ServerMessage snapshot", () => {
    const pythonSnapshot = loadSnapshot("Message_full");
    const parsed = ServerMessageSchema.parse(pythonSnapshot);
    expect(parsed.type).toBe("spawn_agent");
    expect(parsed.request_id).toBe("abc123");
    expect(parsed.timestamp).toBe(12345.678);
  });

  it("serializeMessage round-trips Python ServerMessage snapshot", () => {
    const pythonSnapshot = loadSnapshot("Message_full");
    const parsed = ServerMessageSchema.parse(pythonSnapshot);
    const serialized = serializeMessage(parsed);
    const reparsed = parseMessage(serialized);

    expect(reparsed.type).toBe(parsed.type);
    expect(reparsed.request_id).toBe(parsed.request_id);
  });
});

// NOTE: ActionChainUpdatedPayload 快照为 synthetic 样本（由 Python serialize_node
// 离线生成、手贴入仓），非运行时真实产物；字段形态对齐 serialize_node 输出契约。
describe("ActionNode typed schema alignment", () => {
  function loadNode(): Record<string, unknown> {
    const snap = loadSnapshot("ActionChainUpdatedPayload");
    return snap.node as Record<string, unknown>;
  }

  it("ActionNodeSchema parses serialize_node output keys (bidirectional)", () => {
    const node = loadNode();
    const parsed = ActionNodeSchema.parse(node);

    const pythonKeys = new Set(Object.keys(node));
    const tsKeys = new Set(Object.keys(parsed));

    // Python serialize_node 的每个键 TS 必须识别（不丢字段）。
    const missingFromTs = [...pythonKeys].filter((k) => !tsKeys.has(k));
    expect(missingFromTs, `Keys missing from TS: ${missingFromTs.join(", ")}`).toEqual([]);

    // TS 多出的键只允许是 schema 显式声明的默认值字段（防止泄漏未声明键）。
    const declaredTsKeys = new Set([
      "id",
      "parent_id",
      "agent_name",
      "timestamp",
      "iteration",
      "ability_names",
      "agent_state",
      "messages_delta",
      "messages_snapshot",
      "is_snapshot",
      "action_results",
      "metadata",
      "session_id",
      "created_on_branch_id",
    ]);
    const extraInTs = [...tsKeys].filter((k) => !pythonKeys.has(k));
    const undeclaredExtra = extraInTs.filter((k) => !declaredTsKeys.has(k));
    expect(undeclaredExtra, `Undeclared extra keys in TS: ${undeclaredExtra.join(", ")}`).toEqual(
      [],
    );
  });

  it("ActionNodeSchema preserves scalar fields from serialize_node", () => {
    const node = loadNode();
    const parsed = ActionNodeSchema.parse(node);

    expect(parsed.id).toBe("node-001");
    expect(parsed.agent_name).toBe("agent-1");
    expect(parsed.iteration).toBe(1);
    expect(parsed.created_on_branch_id).toBe("branch-main");
    expect(parsed.session_id).toBe("sess-001");
    expect(parsed.ability_names).toEqual(["conversation", "write_file"]);
  });

  it("ChatMessageWireSchema preserves source normalization values", () => {
    const node = loadNode();
    const messages = (node.messages_delta as unknown[]) ?? [];
    const parsed = messages.map((m) => ChatMessageWireSchema.parse(m));

    const sources = parsed.map((m) => m.source);
    expect(sources).toContain("human:user");
    expect(sources).toContain("agent:agent-1");
  });

  it("ActionResultItemSchema parses ability_name + action_result.data.file_path", () => {
    const node = loadNode();
    const results = (node.action_results as unknown[]) ?? [];
    const parsed = results.map((r) => ActionResultItemSchema.parse(r));

    expect(parsed).toHaveLength(1);
    expect(parsed[0].ability_name).toBe("write_file");
    expect(parsed[0].action_result?.outcome).toBe("success");
    expect(parsed[0].action_result?.data?.file_path).toBe("/tmp/test.txt");
  });

  it("ActionNodeSchema tolerates empty node {} default", () => {
    const parsed = ActionNodeSchema.parse({});
    expect(parsed.id).toBe("");
    expect(parsed.messages_delta).toEqual([]);
  });

  // S7 P1-3：commit 事件的权威 branch 快照必须与 BranchInfoPayload 契约严格对齐，
  // 漂移在测试期而非运行期暴露（对齐源是 _core_event_to_dict 的实际发射形状）。
  it("ActionChainUpdatedPayload.branch snapshot aligns strictly with BranchInfoPayloadSchema", () => {
    const snap = loadSnapshot("ActionChainUpdatedPayload");
    const branch = snap.branch as Record<string, unknown> | null | undefined;
    expect(branch, "snapshot must carry an authoritative branch object").toBeTruthy();

    const parsed = BranchInfoPayloadSchema.parse(branch);

    const pythonKeys = new Set(Object.keys(branch as Record<string, unknown>));
    const tsKeys = new Set(Object.keys(parsed));
    const missingFromTs = [...pythonKeys].filter((k) => !tsKeys.has(k));
    expect(missingFromTs, `Keys missing from TS: ${missingFromTs.join(", ")}`).toEqual([]);
    const extraInTs = [...tsKeys].filter((k) => !pythonKeys.has(k));
    expect(extraInTs, `Undeclared extra keys in TS: ${extraInTs.join(", ")}`).toEqual([]);

    expect(parsed.branch_id).toBe("branch-main");
    expect(parsed.lifecycle).toBe("open");
    expect(parsed.head_node_id).toBe("node-001");
  });
});
