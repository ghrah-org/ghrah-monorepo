// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  type AbilityDefinitionPayload,
  type ActionNode,
  ActionNodeSchema,
  AgentCompactContextPayloadSchema,
  AgentEntityCreatePayloadSchema,
  AgentEntityListPayloadSchema,
  AgentEntityPayloadSchema,
  AgentSnapshotSchema,
  AgentSpecPatchSchema,
  AgentConfigPayloadSchema,
  type AgentConfigPayload,
  AgentResetPayloadSchema,
  BranchActivatePayloadSchema,
  BranchArchivePayloadSchema,
  BranchCreatePayloadSchema,
  BranchDeletePayloadSchema,
  type BranchInfoPayload,
  BranchListPayloadSchema,
  CommandType,
  EventType,
  GetChainHistoryPayloadSchema,
  GetAgentInfoPayloadSchema,
  type HitlRequestListView,
  type HITLRequestPayload,
  HITLResponsePayloadSchema,
  ListAgentsPayloadSchema,
  PluginNegotiatePayloadSchema,
  type PythonHalfInfo,
  ProjectCreatePayloadSchema,
  ProjectAddAgentPayloadSchema,
  ProjectRemoveAgentPayloadSchema,
  ProjectDeletePayloadSchema,
  SpawnAgentPayloadSchema,
  ProjectIdPayloadSchema,
  type ProjectInfoPayload,
  ProjectLifecyclePayloadSchema,
  ProjectListPayloadSchema,
  ProjectTransitionPayloadSchema,
  ProjectUpdatePayloadSchema,
  RoomCreatePayloadSchema,
  RoomDeletePayloadSchema,
  RoomGetLogPayloadSchema,
  RoomIdPayloadSchema,
  type RoomInfoPayload,
  RoomJoinPayloadSchema,
  RoomLeavePayloadSchema,
  RoomLifecyclePayloadSchema,
  RoomListPayloadSchema,
  type RoomLogEntryPayload,
  type RoomMember,
  type RoomTranscriptView,
  RoomSendPayloadSchema,
  RoomUpdatePayloadSchema,
  SessionActivatePayloadSchema,
  SessionArchivePayloadSchema,
  SessionCreatePayloadSchema,
  SessionDeletePayloadSchema,
  type SessionInfoPayload,
  SessionListPayloadSchema,
  SendMessagePayloadSchema,
  TaskClaimListPayloadSchema,
  type TaskClaimPayload,
  TaskCreatePayloadSchema,
  TaskDumpPayloadSchema,
  TaskIdPayloadSchema,
  type TaskInfoPayload,
  TaskListPayloadSchema,
  TerminateAgentPayloadSchema,
  type TranscriptDeliveryView,
  type TranscriptEntryView,
  type TurnCardView,
  type TurnDetailView,
  TurnAbortPayloadSchema,
  type TurnAbortView,
  TurnAbortViewSchema,
  TurnRollbackPayloadSchema,
  type TurnRollbackPreviewView,
  TurnRollbackPreviewViewSchema,
  TurnRollbackViewSchema,
} from "@ghrah/protocol";
import { ZodError, z } from "zod";

export interface CommandOutcome {
  success: boolean;
  data?: unknown;
  error?: string;
  errorDetail?: string;
}

export type EmitFn = (eventType: EventType, payload: Record<string, unknown>) => void;

export interface MockStateOptions {
  /** HITL 审批超时（ms），超时自动拒绝。默认 30s。 */
  hitlTimeoutMs?: number;
  /** spawn_agent 后 ACTION_CHAIN_UPDATED 周期（ms）。默认 2s。 */
  chainIntervalMs?: number;
  /** 是否模拟 agent 认知轨迹（周期性 ACTION_CHAIN_UPDATED）。默认 false。 */
  simulateChains?: boolean;
  /** 是否模拟消息投递状态推进（pending → processing → completed）。默认 false。 */
  simulateDeliveries?: boolean;
  /** 投递状态每拍推进间隔（ms）。默认 1200。 */
  deliveryIntervalMs?: number;
  /** 在演示审批所在的 Room 预置轮次卡样例。 */
  roomTurnExamples?: boolean;
}

/**
 * 单条消息对一个 agent 的投递记录。
 *
 * 形状严格对齐 Core 侧 ``get_room_log`` 的读通口径
 * （``ghrah-subject`` 的 room manager 在注入 ``data.deliveries`` 时逐字段给出
 * delivery_id / recipient_id / state / error / blocked_reason）。
 */
export interface MockDeliveryRecord {
  delivery_id: string;
  /** 接收方 agent 的稳定 ID（room 成员的 subject 已归一为 agentId）。 */
  recipient_id: string;
  /** pending | processing | completed；blocked 由 blocked_reason 派生，不在此写。 */
  state: string;
  error: string | null;
  blocked_reason: string | null;
  turn_id?: string | null;
  target_session_id?: string | null;
  delivery_policy?: "inject" | "queue";
  display_state?: string;
  queue_position?: number;
  cancelable?: boolean;
}

interface AgentRecord {
  projectId: string;
  agentId: string;
  name: string;
  config: AgentConfigPayload;
  abilities: AbilityDefinitionPayload[];
  runtimeState: "running" | "stopped";
  incarnationId: string | null;
}

interface AgentActionState {
  projectId: string;
  agentId: string;
  activeSessionId: string;
  sessions: Map<string, SessionInfoPayload>;
  branches: Map<string, BranchInfoPayload>;
  nodes: Map<string, ActionNode>;
}

interface DirectMessage {
  projectId: string;
  acceptedAt?: number;
  message: { message_id: string; sender_id: string; content: string; reply_to: string | null };
  deliveries: Array<MockDeliveryRecord & { consumed_node_id: string | null }>;
}

export interface PendingHitl {
  payload: HITLRequestPayload;
  sessionId: string | undefined;
  createdAt: string;
  deadlineAt: string | null;
  status: "pending" | "approved" | "rejected";
  reason: string | null;
}

interface MockRoomTurn extends TurnDetailView {
  projectId: string;
  roomId: string;
}

const ok = (data?: unknown): CommandOutcome => ({ success: true, data: data ?? null });
const fail = (error: string, errorDetail?: string): CommandOutcome => ({
  success: false,
  error,
  ...(errorDetail ? { errorDetail } : {}),
});

function isoNow(): string {
  return new Date().toISOString();
}

function uuid(): string {
  return crypto.randomUUID();
}

function agentKey(projectId: string, agentId: string): string {
  return `${projectId}\u0000${agentId}`;
}

function mockMessageId(entry: RoomLogEntryPayload): string {
  return typeof entry.data.mm_message_id === "string" ? entry.data.mm_message_id : `mm:${entry.id}`;
}

function mockDeliveryView(record: MockDeliveryRecord, messageId: string): TranscriptDeliveryView {
  const queued = record.state === "pending" && !record.turn_id;
  return {
    delivery_id: record.delivery_id,
    recipient_id: record.recipient_id,
    state: record.state,
    error: record.error,
    blocked_reason: record.blocked_reason,
    message_id: messageId,
    turn_id: record.turn_id ?? null,
    delivery_policy: record.delivery_policy ?? "queue",
    display_state: record.display_state ?? (queued ? "queued" : record.state),
    queue_position: record.queue_position ?? (queued ? 1 : 0),
    cancelable: record.cancelable ?? queued,
    queue_reason: null,
    injection_step: null,
  };
}

function mockHumanState(deliveries: TranscriptDeliveryView[]): string | null {
  if (deliveries.length === 0) return null;
  const states = new Set(deliveries.map((delivery) => delivery.display_state));
  if (states.has("consumed") || states.has("injected")) return "injected";
  if (states.has("queued") || states.has("blocked")) return "queued";
  if (["failed", "interrupted", "parked", "cancelled"].some((state) => states.has(state)))
    return "failed";
  return "sent";
}

function isActiveTurn(turn: TurnCardView): boolean {
  return turn.status === "open" || turn.status === "waiting_hitl";
}

const MockSendAbilitySchema = z.object({
  name: z.literal("send_message"),
  args: z.object({
    target: z.string().min(1),
    content: z.string(),
    fire_and_forget: z.boolean().optional().default(false),
  }),
});

/**
 * mock 的内置 Agent 模板：真实产品里能力来自模板成员，由 Core 在创建时授予。
 * mock 没有模板库，用这份固定成员代替——`send_message` 供互发消息的剧本使用。
 */
const MOCK_TEMPLATE_ABILITIES: ReadonlyArray<AbilityDefinitionPayload> = [
  { ability_type: "send_message", params: {} },
  { ability_type: "read_file", params: {} },
];

/**
 * 模拟 Subject 状态机。命令/事件面严格对齐「Room Unit 形态契约」：
 * 同 payload schema、同事件名、同 seq 分配语义（room 内 seq 单点分配、单调递增）。
 */
export class MockState {
  readonly projects = new Map<string, ProjectInfoPayload>();
  readonly rooms = new Map<string, RoomInfoPayload>();
  readonly agents = new Map<string, AgentRecord>();
  readonly roomLogs = new Map<string, RoomLogEntryPayload[]>();
  readonly tasks = new Map<string, TaskInfoPayload>();
  readonly pendingHitl = new Map<string, PendingHitl>();
  readonly actionStates = new Map<string, AgentActionState>();
  private readonly _directMessages: DirectMessage[] = [];
  /** 场景声明的 Python 半清单（mock 协商数据源，非权威）。 */
  pythonHalves: PythonHalfInfo[] = [];
  /** 归因种子：claims（含内嵌 evidence 的冻结副本）与独立 evidence 表投影。 */
  readonly claims = new Map<string, TaskClaimPayload>();
  readonly evidence = new Map<string, TaskClaimPayload["evidence"][number]>();

  /** 由 server 注入：广播事件（带 seq_id 分配与订阅过滤）。 */
  emit: EmitFn = () => {};

  private readonly _roomTurnExamples: boolean;
  private readonly _hitlTimeoutMs: number;
  private readonly _chainIntervalMs: number;
  private readonly _simulateChains: boolean;
  private readonly _deliveryIntervalMs: number;
  private readonly _simulateDeliveries: boolean;
  private readonly _chainTimers = new Map<string, ReturnType<typeof setInterval>>();
  /** room log entry.id → 该条消息对各 agent 的投递记录（读通时注入 data.deliveries）。 */
  private readonly _deliveries = new Map<string, MockDeliveryRecord[]>();
  private readonly _roomTurns = new Map<string, MockRoomTurn>();
  private readonly _deliveryTimers = new Map<string, ReturnType<typeof setTimeout>>();
  // 累计用量模拟（成本口径）：post_call 模拟时累加，链模拟停止后保留供 get_agent_info 恢复
  private readonly _cumulativeUsage = new Map<
    string,
    { input: number; output: number; cache_read: number }
  >();
  private readonly _hitlTimers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(options: MockStateOptions = {}) {
    this._roomTurnExamples = options.roomTurnExamples ?? false;
    this._hitlTimeoutMs = options.hitlTimeoutMs ?? 30_000;
    this._chainIntervalMs = options.chainIntervalMs ?? 2_000;
    this._simulateChains = options.simulateChains ?? false;
    this._deliveryIntervalMs = options.deliveryIntervalMs ?? 1_200;
    this._simulateDeliveries = options.simulateDeliveries ?? false;
  }

  /** 清理所有定时器（chain 模拟 + 投递推进 + HITL 超时）。 */
  dispose(): void {
    for (const timer of this._chainTimers.values()) clearInterval(timer);
    this._chainTimers.clear();
    for (const timer of this._deliveryTimers.values()) clearTimeout(timer);
    this._deliveryTimers.clear();
    for (const timer of this._hitlTimers.values()) clearTimeout(timer);
    this._hitlTimers.clear();
  }

  // ─── 命令路由 ───

  handleCommand(commandType: string, rawPayload: Record<string, unknown>): CommandOutcome {
    switch (commandType) {
      case CommandType.AGENT_ENTITY_CREATE:
        return this._parse(AgentEntityCreatePayloadSchema, rawPayload, (payload) => {
          const result = this.spawnAgent(payload.project_id, {
            name: payload.name,
            templateId: payload.template_id,
            templateRevision: payload.revision,
            start: payload.start,
          });
          if (!result.success) return result;
          const data = result.data as { agent_id: string };
          return this._entity({
            project_id: payload.project_id,
            agent_id: data.agent_id,
            operation: { type: "snapshot" },
          });
        });
      case CommandType.AGENT_ENTITY_LIST:
        return this._parse(AgentEntityListPayloadSchema, rawPayload, (payload) => {
          const project = this.projects.get(payload.project_id);
          if (!project || project.archived_at) return fail("project_unavailable");
          return ok({
            agents: this._agentsForProject(payload.project_id).map((agent) =>
              this._snapshot(project, agent),
            ),
            available_ability_types: [],
            model_config_names: ["mock-model"],
          });
        });
      case CommandType.AGENT_ENTITY:
        return this._parse(AgentEntityPayloadSchema, rawPayload, (payload) =>
          this._entity(payload),
        );
      case CommandType.SEND_MESSAGE:
        return this._parse(SendMessagePayloadSchema, rawPayload, (payload) =>
          this._sendMessage(payload),
        );
      case CommandType.REGISTER_ABILITY:
      case CommandType.UNREGISTER_ABILITY:
        return fail("legacy_ability_registration_disabled");
      // Room
      case CommandType.ROOM_CREATE:
        return this._parse(RoomCreatePayloadSchema, rawPayload, (p) => this._roomCreate(p));
      case CommandType.ROOM_LIST:
        return this._parse(RoomListPayloadSchema, rawPayload, (p) => this._roomList(p));
      case CommandType.ROOM_GET:
        return this._parse(RoomIdPayloadSchema, rawPayload, (p) => {
          const room = this.rooms.get(p.room_id);
          return room ? ok({ room }) : fail(`room not found: ${p.room_id}`);
        });
      case CommandType.ROOM_UPDATE:
        return this._parse(RoomUpdatePayloadSchema, rawPayload, (p) => this._roomUpdate(p));
      case CommandType.ROOM_ARCHIVE:
        return this._parse(RoomLifecyclePayloadSchema, rawPayload, (p) =>
          this._roomArchive(p.room_id, p.expected_version),
        );
      case CommandType.ROOM_RESTORE:
        return this._parse(RoomLifecyclePayloadSchema, rawPayload, (p) =>
          this._roomRestore(p.room_id, p.expected_version),
        );
      case CommandType.ROOM_DELETE:
        return this._parse(RoomDeletePayloadSchema, rawPayload, (p) => this._roomDelete(p));
      case CommandType.ROOM_JOIN:
        return this._parse(RoomJoinPayloadSchema, rawPayload, (p) => this._roomJoin(p));
      case CommandType.ROOM_LEAVE:
        return this._parse(RoomLeavePayloadSchema, rawPayload, (p) => this._roomLeave(p));
      case CommandType.ROOM_GET_MEMBERS:
        return this._parse(RoomIdPayloadSchema, rawPayload, (p) => this._roomMembers(p.room_id));
      case CommandType.ROOM_GET_LOG:
        return this._parse(RoomGetLogPayloadSchema, rawPayload, (p) => this._roomGetLog(p));
      case CommandType.ROOM_SEND:
        return this._parse(RoomSendPayloadSchema, rawPayload, (p) => this._roomSend(p));

      // Project（最小实现）
      case CommandType.PROJECT_CREATE:
        return this._parse(ProjectCreatePayloadSchema, rawPayload, (p) => this._projectCreate(p));
      case CommandType.PROJECT_LIST:
        return this._parse(ProjectListPayloadSchema, rawPayload, (p) => {
          let list = [...this.projects.values()];
          if (p.status != null) list = list.filter((proj) => proj.status === p.status);
          list = list.filter((project) => (project.archived_at != null) === p.archived);
          return ok({ projects: list, count: list.length });
        });
      case CommandType.PROJECT_GET:
        return this._parse(ProjectIdPayloadSchema, rawPayload, (p) => {
          const project = this.projects.get(p.project_id);
          return project ? ok({ project }) : fail(`project not found: ${p.project_id}`);
        });
      case CommandType.PROJECT_UPDATE:
        return this._parse(ProjectUpdatePayloadSchema, rawPayload, (p) => this._projectUpdate(p));
      case CommandType.PROJECT_ARCHIVE:
        return this._parse(ProjectLifecyclePayloadSchema, rawPayload, (p) =>
          this._projectArchive(p.project_id, p.expected_version),
        );
      case CommandType.PROJECT_RESTORE:
        return this._parse(ProjectLifecyclePayloadSchema, rawPayload, (p) =>
          this._projectRestore(p.project_id, p.expected_version),
        );
      case CommandType.PROJECT_DELETE:
        return this._parse(ProjectDeletePayloadSchema, rawPayload, (p) =>
          this._projectDelete(p.project_id, p.expected_version, p.cascade_rooms),
        );
      case CommandType.PROJECT_PAUSE:
        return this._parse(ProjectTransitionPayloadSchema, rawPayload, (p) =>
          this._projectTransition(
            p.project_id,
            "paused",
            EventType.PROJECT_PAUSED,
            p.expected_version,
          ),
        );
      case CommandType.PROJECT_RESUME:
        return this._parse(ProjectTransitionPayloadSchema, rawPayload, (p) =>
          this._projectTransition(
            p.project_id,
            "active",
            EventType.PROJECT_RESUMED,
            p.expected_version,
          ),
        );
      case CommandType.PROJECT_STOP:
        return this._parse(ProjectTransitionPayloadSchema, rawPayload, (p) =>
          this._projectTransition(
            p.project_id,
            "stopped",
            EventType.PROJECT_STOPPED,
            p.expected_version,
          ),
        );

      // Task（最小实现）
      case CommandType.TASK_CREATE:
        return this._parse(TaskCreatePayloadSchema, rawPayload, (p) => this._taskCreate(p));
      case CommandType.TASK_LIST:
        return this._parse(TaskListPayloadSchema, rawPayload, (p) => {
          let list = [...this.tasks.values()];
          if (p.project_id != null) list = list.filter((t) => t.project_id === p.project_id);
          if (p.agent_name != null) list = list.filter((t) => t.agent_name === p.agent_name);
          list = list.slice(0, p.limit);
          return ok({ tasks: list, count: list.length });
        });
      case CommandType.TASK_GET:
        return this._parse(TaskIdPayloadSchema, rawPayload, (p) => {
          const task = this.tasks.get(p.task_id);
          return task ? ok({ task }) : fail(`task not found: ${p.task_id}`);
        });

      // Plugin / Task 归因（mock：数据来自场景种子，协商计算镜像 Python negotiate）
      case CommandType.PLUGIN_NEGOTIATE:
        return this._parse(PluginNegotiatePayloadSchema, rawPayload, (p) =>
          ok(this._negotiate(p.enabled_ts ?? [])),
        );
      case CommandType.TASK_DUMP:
        return this._parse(TaskDumpPayloadSchema, rawPayload, (p) => {
          let taskList = [...this.tasks.values()];
          if (p.project_id != null)
            taskList = taskList.filter((t) => t.project_id === p.project_id);
          if (!p.include_deleted) taskList = taskList.filter((t) => t.status !== "canceled");
          if (p.limit != null) taskList = taskList.slice(0, p.limit);
          const taskIds = new Set(taskList.map((t) => t.task_id));
          const claims = [...this.claims.values()].filter((c) => taskIds.has(c.task_id));
          const evidenceIds = new Set(claims.flatMap((c) => c.evidence.map((e) => e.evidence_id)));
          const evidence = [...this.evidence.values()].filter((e) =>
            evidenceIds.has(e.evidence_id),
          );
          return ok({ tasks: taskList, claims, evidence });
        });
      case CommandType.TASK_LIST_CLAIMS:
        return this._parse(TaskClaimListPayloadSchema, rawPayload, (p) => {
          let list = [...this.claims.values()];
          if (p.task_id != null) list = list.filter((c) => c.task_id === p.task_id);
          if (p.claimant_id != null) list = list.filter((c) => c.claimant_id === p.claimant_id);
          if (p.state != null) {
            const states = Array.isArray(p.state) ? new Set(p.state) : new Set([p.state]);
            list = list.filter((c) => states.has(c.state));
          }
          list = list.slice(0, p.limit);
          return ok({ claims: list, count: list.length });
        });

      // Agent / Core
      case CommandType.SPAWN_AGENT:
        // 真实 Core 已拒绝该命令；mock 保留为「按模板创建」的别名，供测试与 CLI
        // 造数据。`manifest_ref` 被当作模板 id（修订固定 1）。
        return this._parse(SpawnAgentPayloadSchema, rawPayload, (p) =>
          this.spawnAgent(p.project_id, {
            name: p.config.name,
            agentId: p.config.agent_id ?? "",
            templateId: p.manifest_ref || "mock.agent",
            templateRevision: 1,
            start: true,
          }),
        );
      case CommandType.PROJECT_ADD_AGENT:
        return this._parse(ProjectAddAgentPayloadSchema, rawPayload, (payload) => {
          const project = this.projects.get(payload.project_id);
          if (!project) return fail("project_unavailable");
          if (payload.expected_version != null && project.version !== payload.expected_version)
            return fail("agent_specification_conflict");
          return this.spawnAgent(payload.project_id, {
            name: payload.agent.name,
            templateId: payload.agent.template_id,
            templateRevision: payload.agent.revision,
            start: payload.agent.desired_state === "running",
          });
        });
      case CommandType.PROJECT_REMOVE_AGENT:
        return this._parse(ProjectRemoveAgentPayloadSchema, rawPayload, (payload) => {
          const project = this.projects.get(payload.project_id);
          const key = agentKey(payload.project_id, payload.agent_id);
          const agent = this.agents.get(key);
          if (!project || project.archived_at) return fail("project_unavailable");
          if (!agent || agent.name !== payload.agent_name) return fail("agent_not_found");
          if (payload.expected_version != null && project.version !== payload.expected_version)
            return fail("agent_specification_conflict");
          if (agent.runtimeState === "running")
            this.terminateAgent(payload.project_id, payload.agent_id, agent.name);
          this.agents.delete(key);
          project.agents = project.agents.filter((spec) => spec.agent_id !== payload.agent_id);
          project.version += 1;
          project.updated_at = isoNow();
          this.emit(EventType.PROJECT_AGENT_REMOVED, {
            project,
            project_id: payload.project_id,
            agent_id: payload.agent_id,
            agent_name: payload.agent_name,
          });
          return ok({ agent_id: payload.agent_id });
        });
      case CommandType.TERMINATE_AGENT:
        return this._parse(TerminateAgentPayloadSchema, rawPayload, (p) =>
          this.terminateAgent(p.project_id, p.agent_id, p.name),
        );
      case CommandType.AGENT_COMPACT_CONTEXT:
        return this._parse(AgentCompactContextPayloadSchema, rawPayload, (p) =>
          this._agentCompactContext(p),
        );
      case CommandType.AGENT_RESET:
        return this._parse(AgentResetPayloadSchema, rawPayload, (p) => this._agentReset(p));
      case CommandType.LIST_AGENTS:
        return this._parse(ListAgentsPayloadSchema, rawPayload, (p) =>
          this._listAgents(p.project_id),
        );
      case CommandType.GET_AGENT_INFO:
        return this._parse(GetAgentInfoPayloadSchema, rawPayload, (p) => this._getAgentInfo(p));
      case CommandType.TURN_ABORT:
        return this._parse(TurnAbortPayloadSchema, rawPayload, (p) => this._turnAbort(p));
      case CommandType.TURN_ROLLBACK:
        return this._parse(TurnRollbackPayloadSchema, rawPayload, (p) => this._turnRollback(p));
      case CommandType.HEALTH_CHECK:
        return ok({ status: "ok" });

      case CommandType.SESSION_CREATE:
        return this._parse(SessionCreatePayloadSchema, rawPayload, (p) => this._sessionCreate(p));
      case CommandType.SESSION_ACTIVATE:
        return this._parse(SessionActivatePayloadSchema, rawPayload, (p) =>
          this._sessionActivate(p),
        );
      case CommandType.SESSION_LIST:
        return this._parse(SessionListPayloadSchema, rawPayload, (p) => this._sessionList(p));
      case CommandType.SESSION_ARCHIVE:
        return this._parse(SessionArchivePayloadSchema, rawPayload, (p) =>
          this._sessionLifecycle(p, "archived"),
        );
      case CommandType.SESSION_DELETE:
        return this._parse(SessionDeletePayloadSchema, rawPayload, (p) =>
          this._sessionLifecycle(p, "deleted"),
        );
      case CommandType.BRANCH_CREATE:
        return this._parse(BranchCreatePayloadSchema, rawPayload, (p) => this._branchCreate(p));
      case CommandType.BRANCH_ACTIVATE:
        return this._parse(BranchActivatePayloadSchema, rawPayload, (p) => this._branchActivate(p));
      case CommandType.BRANCH_LIST:
        return this._parse(BranchListPayloadSchema, rawPayload, (p) => this._branchList(p));
      case CommandType.BRANCH_ARCHIVE:
        return this._parse(BranchArchivePayloadSchema, rawPayload, (p) =>
          this._branchLifecycle(p, "archived"),
        );
      case CommandType.BRANCH_DELETE:
        return this._parse(BranchDeletePayloadSchema, rawPayload, (p) =>
          this._branchLifecycle(p, "deleted"),
        );
      case CommandType.GET_CHAIN_HISTORY:
        return this._parse(GetChainHistoryPayloadSchema, rawPayload, (p) => this._chainHistory(p));

      // HITL 单路径（core 侧）：按 promise_id 匹配 pending（协议 HITLResponsePayload 无 tool_call_id 字段）
      case CommandType.HITL_RESPONSE:
        return this._parse(HITLResponsePayloadSchema, rawPayload, (p) =>
          this.resolveHitl(p.promise_id, p.approved, p.reason ?? null),
        );

      default:
        return fail(`unknown command: ${commandType}`);
    }
  }

  /**
   * 协商计算（镜像 ghrah-plugin negotiator 的纯函数语义，mock 数据源为场景声明）：
   * matched（双侧在场且版本相等）/ python_only / ts_only / version_conflicts /
   * missing_capabilities（Python requires 并集 − 双侧 provides 并集）。
   */
  private _negotiate(
    enabledTs: Array<{ plugin_id: string; version: string; provides?: string[] }>,
  ) {
    const pyById = new Map(this.pythonHalves.map((item) => [item.plugin_id, item]));
    const tsById = new Map(enabledTs.map((item) => [item.plugin_id, item]));
    const matched: Array<Record<string, unknown>> = [];
    const versionConflicts: Array<Record<string, unknown>> = [];
    for (const [pluginId, py] of pyById) {
      const ts = tsById.get(pluginId);
      if (!ts) continue;
      if (ts.version === py.version) {
        matched.push({
          plugin_id: pluginId,
          version: py.version,
          provides: [...new Set([...py.provides, ...(ts.provides ?? [])])],
          instances: py.instances ?? [],
        });
      } else {
        versionConflicts.push({
          plugin_id: pluginId,
          python_version: py.version,
          ts_version: ts.version,
        });
      }
    }
    const provided = new Set([
      ...this.pythonHalves.flatMap((item) => item.provides),
      ...enabledTs.flatMap((item) => item.provides ?? []),
    ]);
    const missingCapabilities = [
      ...new Set(
        this.pythonHalves.flatMap((item) =>
          (item.requires_capabilities ?? []).filter((cap) => !provided.has(cap)),
        ),
      ),
    ];
    return {
      matched,
      python_only: this.pythonHalves.filter((item) => !tsById.has(item.plugin_id)),
      ts_only: enabledTs.filter((item) => !pyById.has(item.plugin_id)),
      version_conflicts: versionConflicts,
      missing_capabilities: missingCapabilities,
      instances: Object.fromEntries(
        this.pythonHalves.map((item) => [item.plugin_id, item.instances ?? []]),
      ),
    };
  }

  private _parse<S extends z.ZodTypeAny>(
    schema: S,
    raw: Record<string, unknown>,
    handler: (payload: z.output<S>) => CommandOutcome,
  ): CommandOutcome {
    try {
      return handler(schema.parse(raw));
    } catch (err) {
      if (err instanceof ZodError) {
        return fail(`invalid payload: ${err.issues.map((i) => i.message).join("; ")}`);
      }
      throw err;
    }
  }

  private _snapshot(project: ProjectInfoPayload, agent: AgentRecord) {
    const state = this.actionStates.get(agentKey(project.project_id, agent.agentId));
    return AgentSnapshotSchema.parse({
      identity: { project_id: project.project_id, agent_id: agent.agentId },
      specification: project.agents.find((spec) => spec.agent_id === agent.agentId),
      specification_revision: project.version,
      runtime: {
        availability: agent.runtimeState === "running" ? "online" : "offline",
        incarnation_id: agent.runtimeState === "running" ? agent.incarnationId : null,
        busy: agent.runtimeState === "running" ? false : null,
      },
      context: {
        availability: "available",
        active_session_id: state?.activeSessionId || null,
        session_count: state?.sessions.size ?? 0,
        branch_count: state?.branches.size ?? 0,
      },
      mailbox: { availability: "available" },
      abilities: {
        available_types: [],
        origin: project.agents.find((spec) => spec.agent_id === agent.agentId)?.origin ?? null,
        loaded_names:
          agent.runtimeState === "running"
            ? agent.abilities.map((ability) => ability.ability_type)
            : null,
        model_config_names: ["mock-model"],
        runtime_availability: agent.runtimeState === "running" ? "online" : "offline",
      },
      observed_at: isoNow(),
    });
  }

  /** 更新记录的展示字段；配置由 Core 实例快照承载，不接受改写。 */
  patchAgentSpecification(
    projectId: string,
    agentId: string,
    raw: Record<string, unknown>,
  ): CommandOutcome {
    return this._parse(AgentSpecPatchSchema, raw, (change) => {
      const project = this.projects.get(projectId);
      if (!project || project.archived_at) return fail("project_unavailable");
      const spec = project.agents.find((item) => item.agent_id === agentId);
      if (!spec) return fail("agent_not_found");
      if (project.version !== change.expected_revision) return fail("agent_specification_conflict");
      if (change.presentation == null) return fail("invalid_request");
      spec.presentation = { ...spec.presentation, ...change.presentation };
      project.version += 1;
      project.updated_at = isoNow();
      this.emit(EventType.PROJECT_UPDATED, { project });
      return ok({ agent_id: agentId });
    });
  }

  private _entity(payload: z.output<typeof AgentEntityPayloadSchema>): CommandOutcome {
    const project = this.projects.get(payload.project_id);
    if (!project || project.archived_at) return fail("project_unavailable");
    const key = agentKey(payload.project_id, payload.agent_id);
    const agent = this.agents.get(key);
    const spec = project.agents.find((item) => item.agent_id === payload.agent_id);
    if (!agent || !spec) return fail("agent_not_found");
    const op = payload.operation;
    const state = this.actionStates.get(key)!;
    const scope = {
      project_id: payload.project_id,
      agent_id: payload.agent_id,
      agent_name: agent.name,
    };
    if (op.type === "snapshot") return ok(this._snapshot(project, agent));
    if (op.type === "abilities.list") return ok(this._snapshot(project, agent).abilities);
    if (op.type === "context.list_sessions") {
      return ok({
        active_session_id: state.activeSessionId || null,
        sessions: [...state.sessions.values()].filter((item) => item.lifecycle !== "deleted"),
        branches: [...state.branches.values()].filter((item) => item.lifecycle !== "deleted"),
      });
    }
    if (op.type === "context.get_history") return this._chainHistory({ ...scope, ...op });
    if (op.type === "context.create_session" || op.type === "context.activate_session") {
      if (project.status !== "active" || agent.runtimeState !== "running")
        return fail("agent_not_running");
      const outcome =
        op.type === "context.create_session"
          ? this._sessionCreate({
              ...scope,
              ...op,
              system_prompt: op.system_prompt ?? agent.config.system_prompt,
            })
          : this._sessionActivate({ ...scope, ...op });
      return outcome.success ? ok({ session: outcome.data }) : outcome;
    }
    if (op.type === "mailbox.list_inbox" || op.type === "mailbox.list_outbox") {
      const inbox = op.type === "mailbox.list_inbox";
      const messages = this._directMessages
        .filter((item) => item.projectId === payload.project_id)
        .map((item, index) => ({ ...item, seq: index + 1 }))
        .filter((item) =>
          inbox
            ? item.deliveries.some((delivery) => delivery.recipient_id === payload.agent_id)
            : item.message.sender_id === payload.agent_id,
        )
        .filter((item) => op.before_seq == null || item.seq < op.before_seq)
        .reverse();
      const page = messages.slice(0, op.limit);
      return ok({
        items: page.map(({ message, deliveries }) => ({
          message,
          deliveries: inbox
            ? deliveries.filter((delivery) => delivery.recipient_id === payload.agent_id)
            : deliveries,
        })),
        next_before_seq: messages.length > page.length ? page.at(-1)!.seq : null,
      });
    }
    if (op.type === "mailbox.get_message") {
      const item = this._directMessages.find(
        (item) =>
          item.projectId === payload.project_id &&
          item.message.message_id === op.message_id &&
          (item.message.sender_id === payload.agent_id ||
            item.deliveries.some((delivery) => delivery.recipient_id === payload.agent_id)),
      );
      return item
        ? ok({ message: item.message, deliveries: item.deliveries })
        : fail("message_not_found");
    }
    if (
      op.type === "mailbox.retry" ||
      op.type === "mailbox.resume" ||
      op.type === "mailbox.cancel"
    ) {
      if (
        op.type !== "mailbox.cancel" &&
        (project.status !== "active" || agent.runtimeState !== "running")
      )
        return fail("agent_not_running");
      const delivery = this._directMessages
        .filter((item) => item.projectId === payload.project_id)
        .flatMap((item) => item.deliveries)
        .find(
          (item) => item.delivery_id === op.delivery_id && item.recipient_id === payload.agent_id,
        );
      if (!delivery) return fail("delivery_not_found");
      if (op.type === "mailbox.cancel") {
        if (delivery.state !== "pending" || delivery.turn_id || delivery.consumed_node_id)
          return fail("delivery_not_cancelable");
        delivery.state = "interrupted";
        delivery.error = "cancelled_by_user";
        delivery.blocked_reason = null;
        delivery.display_state = "cancelled";
        delivery.cancelable = false;
        delivery.queue_position = 0;
        return ok({ delivery });
      }
      if (op.type === "mailbox.retry" && delivery.error === "cancelled_by_user") {
        delivery.state = "pending";
        delivery.error = null;
        delivery.display_state = "queued";
        delivery.cancelable = true;
        return ok({ delivery });
      }
      return fail("invalid_delivery_state");
    }
    if (
      "expected_revision" in op &&
      op.expected_revision != null &&
      op.expected_revision !== project.version
    )
      return fail("agent_specification_conflict");
    if (
      "expected_incarnation_id" in op &&
      op.expected_incarnation_id != null &&
      op.expected_incarnation_id !== agent.incarnationId
    )
      return fail("agent_incarnation_conflict");
    if (op.type === "abilities.configure") return fail("abilities_managed_by_access_store");
    if (op.type === "model.configure") {
      // 配置由 Core 实例快照承载，Subject 侧不再接受改写。
      return fail("agent_config_immutable");
    } else if (op.type === "start") {
      if (project.status !== "active") return fail("project_not_active");
      if (agent.runtimeState === "running") return ok(this._snapshot(project, agent));
      agent.runtimeState = "running";
      agent.incarnationId = uuid();
      if (!state.activeSessionId) {
        const session = this._newSession(state, agent.name, {
          systemPrompt: agent.config.system_prompt,
        });
        state.activeSessionId = session.session_id;
      }
      spec.desired_state = "running";
      this._setAgentSpecRuntime(project, agent.agentId, "running");
      this.emit(EventType.AGENT_SPAWNED, this._agentEvent(agent));
      if (this._simulateChains) this._startChainSimulation(key);
    } else if (op.type === "stop") {
      spec.desired_state = "stopped";
      const result = this.terminateAgent(payload.project_id, payload.agent_id, agent.name);
      if (!result.success) return result;
    } else return fail("unsupported_entity_operation");
    if (op.type !== "stop") project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_UPDATED, { project });
    if (op.type === "start")
      this.emit(EventType.ABILITY_TREE_CHANGED, {
        project_id: payload.project_id,
        agent_id: payload.agent_id,
        reason: "runtime",
      });
    return ok(this._snapshot(project, agent));
  }

  private _sendMessage(payload: z.output<typeof SendMessagePayloadSchema>): CommandOutcome {
    const project = this.projects.get(payload.project_id);
    if (!project || project.archived_at || project.status !== "active")
      return fail("project_not_active");
    const source = this.agents.get(agentKey(payload.project_id, payload.agent_id));
    if (!source || source.name !== payload.target) return fail("agent_identity_mismatch");
    if (source.runtimeState !== "running") return fail("agent_not_running");
    let recipient = source;
    let senderId = payload.sender;
    let content = payload.content;
    const parsedExecute = MockSendAbilitySchema.safeParse(payload.metadata?.execute_ability);
    if (payload.metadata?.execute_ability !== undefined && !parsedExecute.success)
      return fail("invalid_request");
    const execute = parsedExecute.success ? parsedExecute.data : undefined;
    if (execute !== undefined) {
      if (execute.name !== "send_message") return fail("mock_capability_unavailable");
      if (!source.abilities.some((ability) => ability.ability_type === "send_message"))
        return fail("source_ability_missing");
      const target = execute.args?.target;
      if (typeof target !== "string" || typeof execute.args?.content !== "string")
        return fail("invalid_request");
      const destination = this.agents.get(agentKey(payload.project_id, target));
      if (!destination) return fail("agent_not_found");
      if (destination.runtimeState !== "running") return fail("agent_not_running");
      recipient = destination;
      senderId = source.agentId;
      content = execute.args.content;
      const sourceState = this.actionStates.get(agentKey(payload.project_id, source.agentId))!;
      const sourceSession = sourceState.sessions.get(sourceState.activeSessionId)!;
      if (
        sourceSession.lifecycle !== "open" ||
        sourceState.branches.get(sourceSession.active_branch_id)?.lifecycle !== "open"
      )
        return fail("context_not_open");
    }
    const key = agentKey(payload.project_id, recipient.agentId);
    const state = this.actionStates.get(key)!;
    const session = state.sessions.get(state.activeSessionId)!;
    const branch = state.branches.get(session.active_branch_id)!;
    if (session.lifecycle !== "open" || branch.lifecycle !== "open")
      return fail("context_not_open");
    if (payload.delivery_policy === "inject") {
      const prior = this._directMessages.find(
        (item) =>
          item.projectId === payload.project_id &&
          item.message.message_id === payload.metadata?.request_id &&
          item.deliveries[0]?.recipient_id === recipient.agentId,
      );
      const existing = prior?.deliveries[0];
      if (prior && prior.message.content !== content) return fail("idempotency_conflict");
      const active = [...this.pendingHitl.values()].find(
        (item) =>
          item.status === "pending" &&
          item.payload.project_id === payload.project_id &&
          item.payload.agent_id === recipient.agentId,
      );
      const turnId = execute ? null : (active?.payload.turn_id ?? null);
      const position =
        1 +
        this._directMessages
          .filter((item) => item.projectId === payload.project_id)
          .flatMap((item) => item.deliveries)
          .filter(
            (delivery) =>
              delivery.recipient_id === recipient.agentId &&
              delivery.target_session_id === session.session_id &&
              delivery.state === "pending" &&
              !delivery.turn_id &&
              !delivery.consumed_node_id,
          ).length;
      const delivery = existing ?? {
        delivery_id: uuid(),
        recipient_id: recipient.agentId,
        state: "pending",
        error: null,
        blocked_reason: null,
        consumed_node_id: null,
        delivery_policy: "inject" as const,
        turn_id: turnId,
        display_state: turnId ? "injected" : "queued",
        queue_position: turnId ? 0 : position,
        target_session_id: session.session_id,
        cancelable: !turnId,
      };
      const messageId =
        prior?.message.message_id ??
        (typeof payload.metadata?.request_id === "string" ? payload.metadata.request_id : uuid());
      const acceptedAt = prior?.acceptedAt ?? Date.now() / 1000;
      if (!prior)
        this._directMessages.push({
          projectId: payload.project_id,
          acceptedAt,
          message: { message_id: messageId, sender_id: senderId, content, reply_to: null },
          deliveries: [delivery],
        });
      return ok({
        message_id: messageId,
        accepted_at: acceptedAt,
        deliveries: [
          {
            delivery_id: delivery.delivery_id,
            policy: delivery.delivery_policy ?? "inject",
            policy_applied: Boolean(delivery.turn_id),
            turn_id: delivery.turn_id ?? null,
            display_state: delivery.display_state,
            queue_position: delivery.queue_position ?? 0,
          },
        ],
      });
    }
    const reply = `[mock:${recipient.name}] ${content}`;
    const node: ActionNode = {
      id: uuid(),
      parent_id: branch.head_node_id,
      agent_name: recipient.name,
      timestamp: isoNow(),
      iteration: session.iteration_count + 1,
      ability_names: ["conversation"],
      agent_state: {},
      messages_delta: [
        {
          role: "user",
          content_blocks: [{ type: "text", text: content }],
          source: execute ? `agent:${senderId}` : `human:${senderId}`,
          metadata: {},
        },
        { role: "ai", content_blocks: [{ type: "text", text: reply }], metadata: {} },
      ],
      is_snapshot: false,
      action_results: [],
      metadata: {},
      session_id: session.session_id,
      created_on_branch_id: branch.branch_id,
    };
    state.nodes.set(node.id, node);
    branch.head_node_id = node.id;
    session.iteration_count += 1;
    session.message_count += 2;
    const messageId = uuid();
    this._directMessages.push({
      projectId: payload.project_id,
      message: { message_id: messageId, sender_id: senderId, content, reply_to: null },
      deliveries: [
        {
          delivery_id: uuid(),
          recipient_id: recipient.agentId,
          state: "completed",
          error: null,
          blocked_reason: null,
          consumed_node_id: node.id,
        },
      ],
    });
    this.emit(EventType.ACTION_CHAIN_UPDATED, {
      project_id: payload.project_id,
      agent_id: recipient.agentId,
      agent_name: recipient.name,
      node,
      branch,
    });
    const responseData = {
      message_id: messageId,
      ...(execute?.args.fire_and_forget ? {} : { response: reply }),
      fire_and_forget: execute?.args.fire_and_forget ?? false,
    };
    if (execute && source.agentId !== recipient.agentId) {
      const sourceState = this.actionStates.get(agentKey(payload.project_id, source.agentId))!;
      const sourceSession = sourceState.sessions.get(sourceState.activeSessionId)!;
      const sourceBranch = sourceState.branches.get(sourceSession.active_branch_id)!;
      const sourceNode: ActionNode = {
        ...node,
        id: uuid(),
        parent_id: sourceBranch.head_node_id,
        agent_name: source.name,
        iteration: sourceSession.iteration_count + 1,
        ability_names: ["send_message"],
        session_id: sourceSession.session_id,
        created_on_branch_id: sourceBranch.branch_id,
        messages_delta: [
          {
            role: "user",
            content_blocks: [{ type: "text", text: payload.content }],
            source: `human:${payload.sender}`,
            metadata: {},
          },
          {
            role: "ai",
            content_blocks: [
              { type: "tool_call", id: uuid(), name: "send_message", arguments: execute.args },
            ],
            metadata: {},
          },
        ],
        action_results: [
          {
            ability_name: "send_message",
            action_result: { outcome: "success", data: responseData },
          },
        ],
      };
      sourceState.nodes.set(sourceNode.id, sourceNode);
      sourceBranch.head_node_id = sourceNode.id;
      sourceSession.iteration_count += 1;
      sourceSession.message_count += sourceNode.messages_delta.length;
      this.emit(EventType.ACTION_CHAIN_UPDATED, {
        project_id: payload.project_id,
        agent_id: source.agentId,
        agent_name: source.name,
        node: sourceNode,
        branch: sourceBranch,
      });
    }
    this.emit(EventType.AGENT_RESPONSE, {
      project_id: payload.project_id,
      agent_id: recipient.agentId,
      sender: recipient.name,
      recipient: payload.sender,
      content: reply,
    });
    return ok({
      content: execute
        ? JSON.stringify({
            outcome: "success",
            data: responseData,
          })
        : reply,
      message_id: messageId,
    });
  }

  // ─── Room ───

  private _roomCreate(p: { project_id: string; name: string }): CommandOutcome {
    const project = this.projects.get(p.project_id);
    if (!project) {
      return fail(`project not found: ${p.project_id}`);
    }
    if (project.archived_at != null) return fail("project_archived");
    const room: RoomInfoPayload = {
      room_id: uuid(),
      project_id: p.project_id,
      name: p.name,
      status: "active",
      members: [],
      seq_watermark: 0,
      version: 1,
      created_at: isoNow(),
      updated_at: isoNow(),
      archived_at: null,
    };
    this.rooms.set(room.room_id, room);
    this.roomLogs.set(room.room_id, []);
    this.emit(EventType.ROOM_CREATED, { room });
    return ok({ room });
  }

  private _roomList(p: {
    project_id?: string | null;
    status?: "active" | "archived" | null;
  }): CommandOutcome {
    let list = [...this.rooms.values()];
    if (p.project_id != null) list = list.filter((r) => r.project_id === p.project_id);
    if (p.status != null) list = list.filter((r) => r.status === p.status);
    list = list.filter((room) => this.projects.get(room.project_id)?.archived_at == null);
    return ok({ rooms: list, count: list.length });
  }

  private _roomUpdate(p: {
    room_id: string;
    name?: string | null;
    expected_version?: number | null;
  }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    if (p.expected_version != null && p.expected_version !== room.version) {
      return fail(
        "room_version_conflict",
        `version conflict: expected ${p.expected_version}, current ${room.version}`,
      );
    }
    if (p.name != null) room.name = p.name;
    room.version += 1;
    room.updated_at = isoNow();
    this.emit(EventType.ROOM_UPDATED, { room });
    return ok({ room });
  }

  private _roomArchive(roomId: string, expectedVersion: number): CommandOutcome {
    const room = this.rooms.get(roomId);
    if (!room) return fail(`room not found: ${roomId}`);
    const project = this.projects.get(room.project_id);
    if (!project || project.archived_at != null) return fail("project_archived");
    if (room.status === "archived") return ok({ room });
    if (expectedVersion !== room.version) {
      return fail(
        "room_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${room.version}`,
      );
    }
    room.status = "archived";
    room.archived_at = isoNow();
    room.version += 1;
    room.updated_at = isoNow();
    this.emit(EventType.ROOM_ARCHIVED, { room });
    return ok({ room });
  }

  private _roomRestore(roomId: string, expectedVersion: number): CommandOutcome {
    const room = this.rooms.get(roomId);
    if (!room) return fail(`room not found: ${roomId}`);
    const project = this.projects.get(room.project_id);
    if (!project || project.archived_at != null) return fail("project_archived");
    if (room.status === "active") return ok({ room });
    if (expectedVersion !== room.version) {
      return fail(
        "room_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${room.version}`,
      );
    }
    room.status = "active";
    room.archived_at = null;
    room.version += 1;
    room.updated_at = isoNow();
    this.emit(EventType.ROOM_RESTORED, { room });
    return ok({ room });
  }

  private _roomDelete(p: { room_id: string; expected_version: number }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const project = this.projects.get(room.project_id);
    if (!project || project.archived_at != null) return fail("project_archived");
    if (p.expected_version !== room.version) {
      return fail(
        "room_version_conflict",
        `version conflict: expected ${p.expected_version}, current ${room.version}`,
      );
    }
    this.rooms.delete(p.room_id);
    this.roomLogs.delete(p.room_id);
    this.emit(EventType.ROOM_DELETED, {
      room_id: room.room_id,
      project_id: room.project_id,
    });
    return ok({ room_id: room.room_id, project_id: room.project_id });
  }

  private _roomJoin(p: {
    room_id: string;
    subject: string;
    subject_type: "agent" | "human";
    subject_name?: string;
  }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    let subject = p.subject;
    let subjectName = p.subject_name;
    if (p.subject_type === "agent") {
      const matches = this._agentsForProject(room.project_id).filter(
        (agent) => agent.agentId === p.subject || agent.name === p.subject,
      );
      if (matches.length > 1) return fail(`ambiguous agent in room project: ${p.subject}`);
      const agent = matches[0];
      if (!agent) return fail("agent_project_mismatch");
      subject = agent.agentId;
      subjectName = agent.name;
    }
    if (!room.members.some((member) => member.subject === subject)) {
      const member: RoomMember = {
        subject,
        subject_type: p.subject_type,
        subject_name: subjectName,
        joined_at: isoNow(),
      };
      room.members.push(member);
      room.updated_at = isoNow();
      this.emit(EventType.ROOM_MEMBER_JOINED, { room, member });
    }
    return ok({ room });
  }

  private _roomLeave(p: { room_id: string; subject: string }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    const matches = room.members.filter(
      (member) => member.subject === p.subject || member.subject_name === p.subject,
    );
    const subject = matches.length === 1 ? matches[0].subject : p.subject;
    const idx = room.members.findIndex((member) => member.subject === subject);
    if (idx < 0) return fail(`subject not in room: ${p.subject}`);
    room.members.splice(idx, 1);
    room.updated_at = isoNow();
    this.emit(EventType.ROOM_MEMBER_LEFT, { room, subject });
    return ok({ room });
  }

  private _roomGetLog(p: {
    room_id: string;
    since_seq?: number | null;
    limit?: number;
  }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    let entries = this.roomLogs.get(p.room_id) ?? [];
    if (p.since_seq != null) entries = entries.filter((e) => e.seq > p.since_seq!);
    const limit = p.limit ?? 100;
    entries = entries.slice(-limit);
    // 读通注入投递态：与真实 Core 一致，deliveries 只在 room_get_log 的返回里出现，
    // 不经 ROOM_LOG_APPENDED 事件下发。
    const projected = entries.map((entry) => {
      const records = this._deliveries.get(entry.id);
      if (!records) return entry;
      return {
        ...entry,
        data: {
          ...entry.data,
          deliveries: records.map((record) => ({ ...record })),
        },
      };
    });
    return ok({ room_id: p.room_id, entries: projected, count: projected.length });
  }

  roomTranscript(
    room: RoomInfoPayload,
    { beforeSeq, sinceSeq, limit }: { beforeSeq?: number; sinceSeq?: number; limit: number },
  ): RoomTranscriptView {
    const log = this.roomLogs.get(room.room_id) ?? [];
    const latestSeq = log.at(-1)?.seq ?? 0;
    const matching =
      sinceSeq !== undefined
        ? log.filter((entry) => entry.seq > sinceSeq)
        : beforeSeq === undefined
          ? log
          : log.filter((entry) => entry.seq < beforeSeq);
    const hasMore = matching.length > limit;
    const selected = sinceSeq !== undefined ? matching.slice(0, limit) : matching.slice(-limit);
    const entries = selected.map((entry) => this._transcriptEntry(entry));
    const openerEntries = new Map(entries.map((entry) => [entry.message_id, entry.entry_id]));
    const referencedTurns = new Set(
      entries.flatMap((entry) => [
        entry.turn_id,
        ...entry.deliveries.map((delivery) => delivery.turn_id),
      ]),
    );
    return {
      project_id: room.project_id,
      room_id: room.room_id,
      entries,
      turns: [...this._roomTurns.values()]
        .filter(
          ({ roomId, turn }) =>
            roomId === room.room_id && (referencedTurns.has(turn.turn_id) || isActiveTurn(turn)),
        )
        .map(({ turn }) => ({
          ...turn,
          opener_entry_id: openerEntries.get(turn.opener_message_id) ?? null,
        })),
      latest_seq: latestSeq,
      next_before_seq: sinceSeq === undefined && hasMore ? (entries[0]?.seq ?? null) : null,
      next_since_seq: sinceSeq === undefined ? null : (entries.at(-1)?.seq ?? sinceSeq),
      has_more: hasMore,
      read_at: isoNow(),
    };
  }

  private _transcriptEntry(entry: RoomLogEntryPayload): TranscriptEntryView {
    const messageId = mockMessageId(entry);
    const deliveries = (this._deliveries.get(entry.id) ?? []).map((record) =>
      mockDeliveryView(record, messageId),
    );
    const data = entry.data;
    const nodeId = typeof data.node_id === "string" ? data.node_id : null;
    const injection = deliveries.find(
      (delivery) => delivery.delivery_policy === "inject" && delivery.turn_id,
    );
    return {
      entry_id: entry.id,
      seq: entry.seq,
      author: entry.author,
      author_type: entry.author_type,
      timestamp: entry.timestamp,
      message_id: deliveries.length || typeof data.mm_message_id === "string" ? messageId : nodeId,
      content: typeof data.message === "string" ? data.message : "",
      content_blocks: Array.isArray(data.content_blocks) ? data.content_blocks : null,
      targets: Array.isArray(data.targets) ? data.targets : [],
      deliveries,
      turn_id: typeof data.turn_id === "string" ? data.turn_id : null,
      insert_before_turn_id: entry.author_type === "human" ? (injection?.turn_id ?? null) : null,
      injection_step: injection?.injection_step ?? null,
      human_state: entry.author_type === "human" ? mockHumanState(deliveries) : null,
      node_id: nodeId,
      ability: typeof data.ability === "string" ? data.ability : null,
    };
  }

  listTurns(
    projectId: string,
    agentId: string,
    sessionId?: string,
    status?: string,
  ): TurnCardView[] {
    return [...this._roomTurns.values()].reverse().flatMap((record) => {
      const { turn } = record;
      const matches =
        record.projectId === projectId &&
        turn.agent_name === agentId &&
        (sessionId === undefined || sessionId === turn.session_id) &&
        (status === undefined || status === turn.status);
      return matches ? [turn] : [];
    });
  }

  turnDetail(projectId: string, agentId: string, turnId: string): TurnDetailView | null {
    const record = this._roomTurns.get(turnId);
    if (record?.projectId !== projectId || record.turn.agent_name !== agentId) return null;
    return { turn: record.turn, nodes: record.nodes };
  }

  /** 读回滚预览：以轮次现有分支 / 节点字段补齐，未知轮次返回 null。 */
  rollbackPreview(
    projectId: string,
    agentId: string,
    turnId: string,
    target: "base" | "node",
    nodeId: string | null,
  ): TurnRollbackPreviewView | null {
    const record = this._findTurnRecord(projectId, agentId, turnId);
    if (!record) return null;
    const { turn, nodes } = record;
    const targetNodeId = this._rollbackTargetNodeId(record, target, nodeId);
    return TurnRollbackPreviewViewSchema.parse({
      turn_id: turn.turn_id,
      session_id: turn.session_id,
      target,
      target_node_id: targetNodeId,
      target_branch_id: turn.branch_id ?? `${turn.turn_id}:branch`,
      base_node_id: turn.base_node_id,
      start_node_id: turn.start_node_id,
      end_node_id: turn.end_node_id,
      truncated_node_count: target === "base" ? nodes.length : 0,
      crosses_compact: false,
      target_is_compact: nodes.some((node) => node.is_snapshot === true),
      head_unchanged: targetNodeId === turn.end_node_id,
      released_delivery_ids: [],
    });
  }

  private _findTurnRecord(
    projectId: string,
    agentId: string,
    turnId: string,
  ): MockRoomTurn | undefined {
    const record = this._roomTurns.get(turnId);
    if (record?.projectId !== projectId || record.turn.agent_name !== agentId) return undefined;
    return record;
  }

  private _latestActiveTurn(
    projectId: string,
    agentId: string,
    sessionId: string | null,
  ): MockRoomTurn | undefined {
    return [...this._roomTurns.values()]
      .reverse()
      .find(
        (record) =>
          record.projectId === projectId &&
          record.turn.agent_name === agentId &&
          isActiveTurn(record.turn) &&
          (sessionId === null || record.turn.session_id === sessionId),
      );
  }

  private _rollbackTargetNodeId(
    record: MockRoomTurn,
    target: "base" | "node",
    nodeId: string | null,
  ): string {
    const { turn } = record;
    if (target === "node" && nodeId) return nodeId;
    return (
      turn.base_node_id ?? turn.start_node_id ?? turn.end_node_id ?? `${turn.turn_id}:base-node`
    );
  }

  /** turn_abort：按 Agent 级寻址中止活动轮次；终态轮次回 already_terminal。 */
  private _turnAbort(payload: z.output<typeof TurnAbortPayloadSchema>): CommandOutcome {
    const agent = this.agents.get(agentKey(payload.project_id, payload.agent_id));
    if (!agent) return fail("agent_not_found");
    if (agent.runtimeState !== "running") return fail("agent_offline");
    const record = payload.turn_id
      ? this._findTurnRecord(payload.project_id, payload.agent_id, payload.turn_id)
      : this._latestActiveTurn(payload.project_id, payload.agent_id, payload.session_id);
    if (!record) return fail("turn_not_found");
    const view: TurnAbortView = {
      turn_id: record.turn.turn_id,
      status: record.turn.status,
      closing_kind: record.turn.closing_kind,
      aborted: false,
      already_terminal: true,
      result_pending: false,
      cancelled_hitl: 0,
      released_deliveries: 0,
      incomplete_node_id: record.turn.incomplete_node_id,
      recovery_branch_id: record.turn.recovery_branch_id,
      opener_delivery_id: record.turn.opener_delivery_id,
    };
    if (!isActiveTurn(record.turn)) {
      return ok(TurnAbortViewSchema.parse(view));
    }
    record.turn.status = "aborted";
    record.turn.closing_kind = "external_abort";
    record.turn.waiting_since = null;
    record.turn.closed_at = isoNow();
    record.turn.abort_reason = payload.reason || null;
    this._emitTurnClosed(agent, record.turn);
    return ok(
      TurnAbortViewSchema.parse({
        ...view,
        status: record.turn.status,
        closing_kind: record.turn.closing_kind,
        aborted: true,
        already_terminal: false,
      }),
    );
  }

  /** turn_rollback：活动轮拒绝，已回滚幂等，其余关闭轮次并返回回滚结果。 */
  private _turnRollback(payload: z.output<typeof TurnRollbackPayloadSchema>): CommandOutcome {
    const agent = this.agents.get(agentKey(payload.project_id, payload.agent_id));
    if (!agent) return fail("agent_not_found");
    const record = this._findTurnRecord(payload.project_id, payload.agent_id, payload.turn_id);
    if (!record) return fail("turn_not_found");
    if (isActiveTurn(record.turn)) return fail("turn_active");
    const alreadyApplied = record.turn.status === "rolled_back";
    if (!alreadyApplied) {
      record.turn.status = "rolled_back";
      record.turn.closing_kind = "rolled_back";
      record.turn.waiting_since = null;
      record.turn.closed_at = isoNow();
      const targetNodeId = this._rollbackTargetNodeId(record, payload.target, payload.node_id);
      record.turn.end_node_id = targetNodeId;
      this._emitTurnClosed(agent, record.turn);
    }
    return ok(
      TurnRollbackViewSchema.parse({
        turn_id: record.turn.turn_id,
        session_id: record.turn.session_id,
        status: record.turn.status,
        closing_kind: record.turn.closing_kind ?? "rolled_back",
        target: payload.target,
        target_node_id: this._rollbackTargetNodeId(record, payload.target, payload.node_id),
        branch_id: record.turn.branch_id ?? `${record.turn.turn_id}:branch`,
        parent_branch_id: record.turn.end_branch_id,
        released_delivery_ids: [],
        already_applied: alreadyApplied,
        rolled_back_at: record.turn.closed_at ?? isoNow(),
      }),
    );
  }

  private _emitTurnClosed(agent: AgentRecord, turn: TurnCardView): void {
    this.emit(EventType.TURN_CLOSED, {
      project_id: agent.projectId,
      agent_id: agent.agentId,
      agent_name: agent.name,
      turn_id: turn.turn_id,
      session_id: turn.session_id,
      status: turn.status,
      closing_kind: turn.closing_kind,
      end_node_id: turn.end_node_id,
    });
  }

  hitlPending(projectId: string, agentId: string, sessionId?: string): HitlRequestListView {
    const items: HitlRequestListView["items"] = [];
    for (const pending of this.pendingHitl.values()) {
      const { payload } = pending;
      if (
        payload.project_id !== projectId ||
        payload.agent_id !== agentId ||
        pending.status !== "pending"
      )
        continue;
      if (sessionId !== undefined && pending.sessionId !== sessionId) continue;
      items.push({
        promise_id: payload.promise_id,
        turn_id: payload.turn_id ?? `mock-turn-${payload.promise_id}`,
        delivery_id: payload.delivery_id ?? null,
        ability_name: payload.ability_name,
        tool_call: payload.tool_args,
        reason: payload.reason ?? "",
        options: payload.shell_remember ?? {},
        status: "pending",
        created_at: pending.createdAt,
        deadline_at: pending.deadlineAt,
        resolved_at: null,
        resolved_by: null,
        resolution: null,
        shell_remember: payload.shell_remember ?? null,
      });
    }
    return { agent_id: agentId, items };
  }

  /** room_send：room 内 seq 单点分配 + 追加 RoomLog + 广播 ROOM_LOG_APPENDED；同步返回 {entry}。 */
  private _roomSend(p: {
    room_id: string;
    author: string;
    author_type: "agent" | "human";
    data?: Record<string, unknown>;
  }): CommandOutcome {
    const room = this.rooms.get(p.room_id);
    if (!room) return fail(`room not found: ${p.room_id}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    const memberIdentity = (value: string): string => {
      const matches = room.members.filter(
        (member) => member.subject === value || member.subject_name === value,
      );
      return matches.length === 1 ? matches[0].subject : value;
    };
    const author = p.author_type === "agent" ? memberIdentity(p.author) : p.author;
    if (
      p.author_type === "agent" &&
      !room.members.some((member) => member.subject === author && member.subject_type === "agent")
    ) {
      return fail(`author not in room: ${p.author}`);
    }
    // 写时校验：data.targets（{message, targets?} 约定）须 ⊆ 该 room 的 agent 成员；
    // 缺省/空 = 整室广播。
    const targets = p.data?.targets;
    let data = p.data ?? {};
    if (targets !== undefined) {
      if (!Array.isArray(targets) || !targets.every((t) => typeof t === "string")) {
        return fail("invalid targets: expected string[]");
      }
      const normalizedTargets = (targets as string[]).map(memberIdentity);
      for (const t of normalizedTargets) {
        const isMember = room.members.some((m) => m.subject === t && m.subject_type === "agent");
        if (!isMember) return fail(`target not in room: ${t}`);
      }
      data = { ...data, targets: normalizedTargets };
    }
    const seq = room.seq_watermark + 1;
    room.seq_watermark = seq;
    room.updated_at = isoNow();
    const entry: RoomLogEntryPayload = {
      id: uuid(),
      room_id: room.room_id,
      seq,
      author,
      author_type: p.author_type,
      timestamp: Date.now() / 1000,
      data,
    };
    this.roomLogs.get(room.room_id)!.push(entry);
    this.emit(EventType.ROOM_LOG_APPENDED, { entry });
    this._startDeliverySimulation(room, entry);
    return ok({ entry });
  }

  /**
   * 为一条刚落账的 room 消息建投递记录并起推进定时器。
   *
   * 只在 ``simulateDeliveries`` 开启时动作（默认关闭，不扰动既有用例）。
   * 记录**不经事件**下发：真实 Core 也只在 ``room_get_log`` 读通时注入
   * ``data.deliveries``，前端靠轮询 ``room_get_log`` 取得推进结果。
   * 之所以不重发 ``ROOM_LOG_APPENDED``：前端 ``onRoomLogAppended`` 仅以其作为 transcript 失效信号，
   * 同 id 重发会被静默丢弃，达不到刷新目的。
   */
  private _startDeliverySimulation(room: RoomInfoPayload, entry: RoomLogEntryPayload): void {
    if (!this._simulateDeliveries) return;
    const declared = entry.data?.targets;
    const targeted = Array.isArray(declared) && declared.length > 0;
    const recipients = targeted
      ? (declared as string[])
      : room.members
          .filter((member) => member.subject_type === "agent")
          .map((member) => member.subject);
    if (recipients.length === 0) return;
    this._deliveries.set(
      entry.id,
      recipients.map((recipient) => ({
        delivery_id: uuid(),
        recipient_id: recipient,
        state: "pending",
        error: null,
        blocked_reason: null,
      })),
    );
    const advance = (next: string, delay: number) => {
      const timer = setTimeout(() => {
        const records = this._deliveries.get(entry.id);
        if (!records) return;
        for (const record of records) {
          if (record.state === "pending" || record.state === "processing") {
            record.state = next;
            if (next === "processing" && (entry.author_type === "human" || targeted)) {
              this._openRoomTurn(room, entry, record);
            } else if (next === "completed" && record.turn_id) {
              this._closeRoomTurn(record.turn_id);
            }
          }
        }
        this._deliveryTimers.delete(`${entry.id}:${next}`);
      }, delay);
      this._deliveryTimers.set(`${entry.id}:${next}`, timer);
    };
    // 两拍推进：先 processing 表示已接手，再 completed 表示接收回调返回。
    advance("processing", this._deliveryIntervalMs);
    advance("completed", this._deliveryIntervalMs * 2);
  }

  private _openRoomTurn(
    room: RoomInfoPayload,
    entry: RoomLogEntryPayload,
    delivery: MockDeliveryRecord,
  ) {
    const key = agentKey(room.project_id, delivery.recipient_id);
    const action = this.actionStates.get(key);
    const session = action?.sessions.get(action.activeSessionId);
    if (!session) return;
    const turn: TurnCardView = {
      turn_id: uuid(),
      agent_name: delivery.recipient_id,
      session_id: session.session_id,
      status: "open",
      closing_kind: null,
      base_node_id: null,
      start_node_id: null,
      end_node_id: null,
      branch_id: session.active_branch_id,
      end_branch_id: null,
      opener_delivery_id: delivery.delivery_id,
      opened_at: isoNow(),
      waiting_since: null,
      closed_at: null,
      abort_reason: null,
      incomplete_node_id: null,
      recovery_branch_id: null,
      turn_index:
        this.listTurns(room.project_id, delivery.recipient_id, session.session_id).length + 1,
      opener_message_id: mockMessageId(entry),
      opener_entry_id: null,
      parent_turn_id: null,
      stats: {
        steps: 0,
        node_count: 0,
        token_input: 0,
        token_output: 0,
        token_total: 0,
        token_cache_read: 0,
        token_cache_write: 0,
        cache_hit_rate: null,
        duration_ms: null,
        waiting_ms: 0,
      },
    };
    delivery.turn_id = turn.turn_id;
    this._roomTurns.set(turn.turn_id, {
      projectId: room.project_id,
      roomId: room.room_id,
      turn,
      nodes: [],
    });
    this.emit(EventType.TURN_OPENED, {
      project_id: room.project_id,
      agent_id: delivery.recipient_id,
      agent_name: this.agents.get(key)?.name ?? delivery.recipient_id,
      turn_id: turn.turn_id,
      session_id: turn.session_id,
      status: turn.status,
      opener_delivery_id: delivery.delivery_id,
    });
  }

  /** 为视觉和组件验收构造一组可通过正常 transcript / detail 读出的历史。 */
  seedRoomTurnExamples(roomId: string, agentId: string, waitingTurnId: string): void {
    const room = this.rooms.get(roomId);
    if (!room || this._roomTurns.has(waitingTurnId)) return;
    const action = this.actionStates.get(agentKey(room.project_id, agentId));
    const session = action?.sessions.get(action.activeSessionId);
    const log = this.roomLogs.get(roomId);
    if (!session || !log) return;
    const seedId = `example:${roomId}`;
    if (this._roomTurns.has(`${seedId}:closed`)) return;
    const start = Date.now();
    const configurations = [
      { id: "closed", status: "closed", message: "Summarize the delivery check." },
      {
        id: "failed",
        status: "failed",
        message: "Read the missing manifest.",
        reason: "Manifest unavailable",
      },
      { id: "compact", status: "closed", message: "Compact the context." },
      {
        id: "aborted",
        status: "aborted",
        message: "Run a review.",
        reason: "Stopped by user",
        recovery: "example-recovery",
      },
      { id: "interrupted", status: "interrupted", message: "Check the session." },
      {
        id: "rolled_back",
        status: "rolled_back",
        message: "Roll back the last changes.",
        recovery: "example-rollback",
      },
      { id: "waiting", status: "waiting_hitl", message: "Request deployment approval." },
    ];
    const indexBase = this.listTurns(room.project_id, agentId, session.session_id).length;
    for (const [index, example] of configurations.entries()) {
      const waiting = example.status === "waiting_hitl";
      const turnId = waiting ? waitingTurnId : `${seedId}:${example.id}`;
      const opened = new Date(start - (configurations.length - index) * 15000).toISOString();
      const entry: RoomLogEntryPayload = {
        id: `${turnId}:entry`,
        room_id: roomId,
        seq: ++room.seq_watermark,
        author: index % 2 ? "Ada" : "user",
        author_type: "human",
        timestamp: Date.parse(opened) / 1000,
        data: { message: example.message, targets: [agentId] },
      };
      log.push(entry);
      const delivery: MockDeliveryRecord = {
        delivery_id: `${turnId}:delivery`,
        recipient_id: agentId,
        state: waiting ? "processing" : example.status === "failed" ? "failed" : "completed",
        error: example.reason ?? null,
        blocked_reason: null,
        turn_id: turnId,
      };
      this._deliveries.set(entry.id, [delivery]);
      const node = ActionNodeSchema.parse({
        id: `${turnId}:node`,
        agent_name: agentId,
        turn_id: turnId,
        timestamp: opened,
        ability_names: ["read_file"],
        ...(example.id === "compact"
          ? {
              is_snapshot: true,
              metadata: {
                node_kind: "compact",
                trigger_source: "manual",
                tokens_before: 1400,
                tokens_after: 300,
                summarized_range: ["first", "last"],
                kept_recent_nodes: 1,
                post_check: "ok",
              },
              messages_snapshot: [
                {
                  role: "ai",
                  source: "summary",
                  content_blocks: [{ type: "text", text: "Compacted context." }],
                },
              ],
            }
          : example.id === "interrupted"
            ? {
                metadata: { node_kind: "incomplete", error: "Connection lost" },
              }
            : {}),
      });
      const nodes = example.id === "failed" ? [] : [node];
      const turn: TurnCardView = {
        turn_id: turnId,
        agent_name: agentId,
        session_id: session.session_id,
        status: example.status,
        closing_kind: waiting ? null : example.status,
        base_node_id: null,
        start_node_id: nodes[0]?.id ?? null,
        end_node_id: nodes[0]?.id ?? null,
        branch_id: session.active_branch_id,
        end_branch_id: null,
        opener_delivery_id: delivery.delivery_id,
        opened_at: opened,
        waiting_since: waiting ? new Date(start).toISOString() : null,
        closed_at: waiting ? null : new Date(Date.parse(opened) + 5000).toISOString(),
        abort_reason: example.reason ?? null,
        incomplete_node_id: example.id === "interrupted" ? node.id : null,
        recovery_branch_id: example.recovery ?? null,
        turn_index: indexBase + index + 1,
        opener_message_id: mockMessageId(entry),
        opener_entry_id: entry.id,
        parent_turn_id: null,
        stats: {
          steps: example.id === "compact" || example.id === "interrupted" ? 0 : nodes.length,
          node_count: nodes.length,
          token_input: 1400,
          token_output: 200,
          token_total: 1600,
          token_cache_read: 700,
          token_cache_write: 100,
          cache_hit_rate: 0.5,
          duration_ms: waiting ? null : 5000,
          waiting_ms: 0,
        },
      };
      this._roomTurns.set(turnId, { projectId: room.project_id, roomId, turn, nodes });
    }
    const tail = log.at(-1);
    if (tail) this.emit(EventType.ROOM_LOG_APPENDED, { entry: tail });
  }

  /**
   * 为控制面场景预置三类轮次（open / waiting_hitl / closed）。
   *
   * 形状与 `seedRoomTurnExamples` 一致：经 transcript / detail / turns 读面可见，
   * 供中止与回滚的 e2e 直接消费。返回三个 turn_id；room / session 缺失时返回 null。
   */
  seedTurnControlTurns(
    roomId: string,
    agentId: string,
  ): { open: string; waiting: string; closed: string } | null {
    const room = this.rooms.get(roomId);
    if (!room) return null;
    const action = this.actionStates.get(agentKey(room.project_id, agentId));
    const session = action?.sessions.get(action.activeSessionId);
    const log = this.roomLogs.get(roomId);
    if (!session || !log) return null;
    const seedId = `control:${roomId}:${agentId}`;
    const configurations = [
      { id: "open", status: "open", message: "Apply the control plane changes." },
      { id: "waiting", status: "waiting_hitl", message: "Approve the deployment." },
      { id: "closed", status: "closed", message: "Summarize the last turn." },
    ] as const;
    const indexBase = this.listTurns(room.project_id, agentId, session.session_id).length;
    const start = Date.now();
    const ids = { open: "", waiting: "", closed: "" };
    for (const [index, example] of configurations.entries()) {
      const turnId = `${seedId}:${example.id}`;
      ids[example.id] = turnId;
      const waiting = example.status === "waiting_hitl";
      const active = example.status === "open" || waiting;
      const opened = new Date(start - (configurations.length - index) * 10_000).toISOString();
      const entry: RoomLogEntryPayload = {
        id: `${turnId}:entry`,
        room_id: roomId,
        seq: ++room.seq_watermark,
        author: "user",
        author_type: "human",
        timestamp: Date.parse(opened) / 1000,
        data: { message: example.message, targets: [agentId] },
      };
      log.push(entry);
      const delivery: MockDeliveryRecord = {
        delivery_id: `${turnId}:delivery`,
        recipient_id: agentId,
        state: active ? "processing" : "completed",
        error: null,
        blocked_reason: null,
        turn_id: turnId,
      };
      this._deliveries.set(entry.id, [delivery]);
      const node = ActionNodeSchema.parse({
        id: `${turnId}:node`,
        agent_name: agentId,
        turn_id: turnId,
        timestamp: opened,
        ability_names: ["read_file"],
      });
      const nodes = waiting ? [] : [node];
      const turn: TurnCardView = {
        turn_id: turnId,
        agent_name: agentId,
        session_id: session.session_id,
        status: example.status,
        closing_kind: active ? null : example.status,
        base_node_id: null,
        start_node_id: active ? null : node.id,
        end_node_id: active ? null : node.id,
        branch_id: session.active_branch_id,
        end_branch_id: null,
        opener_delivery_id: delivery.delivery_id,
        opened_at: opened,
        waiting_since: waiting ? opened : null,
        closed_at: active ? null : new Date(Date.parse(opened) + 5000).toISOString(),
        abort_reason: null,
        incomplete_node_id: null,
        recovery_branch_id: null,
        turn_index: indexBase + index + 1,
        opener_message_id: mockMessageId(entry),
        opener_entry_id: entry.id,
        parent_turn_id: null,
        stats: {
          steps: nodes.length,
          node_count: nodes.length,
          token_input: 900,
          token_output: 120,
          token_total: 1020,
          token_cache_read: 400,
          token_cache_write: 60,
          cache_hit_rate: 0.44,
          duration_ms: active ? null : 5000,
          waiting_ms: 0,
        },
      };
      this._roomTurns.set(turnId, { projectId: room.project_id, roomId, turn, nodes });
    }
    const tail = log.at(-1);
    if (tail) this.emit(EventType.ROOM_LOG_APPENDED, { entry: tail });
    return ids;
  }

  private _closeRoomTurn(turnId: string) {
    const record = this._roomTurns.get(turnId);
    if (!record || !isActiveTurn(record.turn)) return;
    const { turn, projectId, nodes } = record;
    turn.status = "closed";
    turn.closing_kind = "completed";
    turn.closed_at = isoNow();
    turn.waiting_since = null;
    turn.stats = {
      ...turn.stats,
      steps: nodes.length,
      node_count: nodes.length,
      duration_ms: Math.max(0, Date.parse(turn.closed_at) - Date.parse(turn.opened_at)),
    };
    this.emit(EventType.TURN_CLOSED, {
      project_id: projectId,
      agent_id: turn.agent_name,
      agent_name: this.agents.get(agentKey(projectId, turn.agent_name))?.name ?? turn.agent_name,
      turn_id: turn.turn_id,
      session_id: turn.session_id,
      status: turn.status,
      closing_kind: turn.closing_kind,
      end_node_id: turn.end_node_id,
    });
  }

  /**
   * 模拟一次 Agent 回复落账并广播 ``agent_response``。
   *
   * 供活动行退场与统计回执联调：先走 room_send 把回复写进 room log，
   * 再发 agent_response（前端以它为「本轮结束」的兜底信号）。
   *
   * @param agentId 回复的 agent 稳定 ID（须已是该 room 的 agent 成员）
   * @param roomId 目标 room
   * @param content 回复正文
   * @param iteration 本轮步数，写入 metadata 供回执读取
   * @returns 落账结果；失败时 success=false 且不广播事件
   */
  emitAgentResponse(
    agentId: string,
    roomId: string,
    content: string,
    iteration = 1,
  ): CommandOutcome {
    const room = this.rooms.get(roomId);
    if (!room) return fail(`room not found: ${roomId}`);
    const agent = [...this.agents.values()].find(
      (candidate) => candidate.agentId === agentId && candidate.projectId === room.project_id,
    );
    if (!agent) return fail(`agent not in room project: ${agentId}`);
    const active = [...this._roomTurns.values()]
      .reverse()
      .find(
        ({ roomId: scope, turn }) =>
          scope === roomId && turn.agent_name === agentId && isActiveTurn(turn),
      );
    const sent = this._roomSend({
      room_id: roomId,
      author: agentId,
      author_type: "agent",
      data: { message: content, ...(active ? { turn_id: active.turn.turn_id } : {}) },
    });
    if (!sent.success) return sent;
    if (active) this._closeRoomTurn(active.turn.turn_id);
    this.emit(EventType.AGENT_RESPONSE, {
      project_id: agent.projectId,
      agent_id: agent.agentId,
      agent_name: agent.name,
      sender: agent.name,
      recipient: "room",
      content,
      content_blocks: null,
      message_type: "result",
      metadata: { iteration, room_id: roomId },
    });
    return ok({ entry: (sent.data as { entry?: RoomLogEntryPayload })?.entry });
  }

  private _activeRoomGuard(room: RoomInfoPayload): CommandOutcome | null {
    const project = this.projects.get(room.project_id);
    if (!project || project.archived_at != null) return fail("project_archived");
    if (room.status === "archived") return fail("resource_archived");
    return null;
  }

  private _roomMembers(roomId: string): CommandOutcome {
    const room = this.rooms.get(roomId);
    if (!room) return fail(`room not found: ${roomId}`);
    const guard = this._activeRoomGuard(room);
    if (guard) return guard;
    return ok({ room_id: room.room_id, members: room.members, count: room.members.length });
  }

  // ─── Project / Task（最小实现） ───

  private _projectCreate(p: {
    name: string;
    description?: string;
    project_root_locator?: string;
    manifest_ref?: string;
    shell_cwd?: string;
  }): CommandOutcome {
    const project: ProjectInfoPayload = {
      project_id: uuid(),
      name: p.name,
      description: p.description ?? "",
      project_root_locator: p.project_root_locator ?? "",
      manifest_ref: p.manifest_ref ?? "",
      shell_cwd: p.shell_cwd ?? "",
      workspaces: [],
      agents: [],
      task_ids: [],
      isolation: {
        agent_path_grants: {},
        agent_private_dir: true,
        effect_allowlist: null,
        hitl_override: null,
        task_scope: true,
      },
      status: "active",
      recovery: "resume",
      version: 1,
      created_at: isoNow(),
      updated_at: isoNow(),
      archived_at: null,
      deleted_at: null,
    };
    this.projects.set(project.project_id, project);
    this.emit(EventType.PROJECT_CREATED, { project });
    return ok({ project });
  }

  private _projectUpdate(p: {
    project_id: string;
    name?: string | null;
    description?: string | null;
    manifest_ref?: string | null;
    shell_cwd?: string | null;
    expected_version?: number | null;
  }): CommandOutcome {
    const project = this.projects.get(p.project_id);
    if (!project) return fail(`project not found: ${p.project_id}`);
    if (project.archived_at != null) return fail("resource_archived");
    if (p.expected_version != null && p.expected_version !== project.version) {
      return fail(
        "project_version_conflict",
        `version conflict: expected ${p.expected_version}, current ${project.version}`,
      );
    }
    if (p.name != null) project.name = p.name;
    if (p.description != null) project.description = p.description;
    if (p.manifest_ref != null) project.manifest_ref = p.manifest_ref;
    if (p.shell_cwd != null) project.shell_cwd = p.shell_cwd;
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_UPDATED, { project });
    return ok({ project });
  }

  private _projectArchive(projectId: string, expectedVersion: number): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return ok({ project });
    if (expectedVersion !== project.version) {
      return fail(
        "project_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${project.version}`,
      );
    }
    for (const agent of this._agentsForProject(projectId)) {
      agent.runtimeState = "stopped";
      this._setAgentSpecRuntime(project, agent.agentId, "stopped");
      this._stopChainSimulation(agentKey(projectId, agent.agentId));
    }
    project.archived_at = isoNow();
    project.status = "stopped";
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_ARCHIVED, { project });
    return ok({ project });
  }

  private _projectRestore(projectId: string, expectedVersion: number): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at == null) return ok({ project });
    if (expectedVersion !== project.version) {
      return fail(
        "project_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${project.version}`,
      );
    }
    project.archived_at = null;
    project.status = "stopped";
    for (const spec of project.agents) spec.runtime_status = "stopped";
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_RESTORED, { project });
    return ok({ project });
  }

  private _projectDelete(
    projectId: string,
    expectedVersion: number,
    cascadeRooms: boolean,
  ): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (expectedVersion !== project.version) {
      return fail(
        "project_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${project.version}`,
      );
    }
    const projectRooms = [...this.rooms.values()].filter((room) => room.project_id === projectId);
    if (projectRooms.length > 0 && !cascadeRooms)
      return fail("project_has_rooms", String(projectRooms.length));
    for (const room of projectRooms) {
      this.rooms.delete(room.room_id);
      this.roomLogs.delete(room.room_id);
    }
    for (const agent of this._agentsForProject(projectId)) {
      const key = agentKey(projectId, agent.agentId);
      this._stopChainSimulation(key);
      this.agents.delete(key);
      this.actionStates.delete(key);
    }
    for (const [taskId, task] of this.tasks) {
      if (task.project_id === projectId) this.tasks.delete(taskId);
    }
    for (const [promiseId, pending] of this.pendingHitl) {
      if (pending.payload.project_id !== projectId) continue;
      const timer = this._hitlTimers.get(promiseId);
      if (timer != null) clearTimeout(timer);
      this._hitlTimers.delete(promiseId);
      this.pendingHitl.delete(promiseId);
    }
    for (let index = this._directMessages.length - 1; index >= 0; index -= 1) {
      if (this._directMessages[index].projectId === projectId)
        this._directMessages.splice(index, 1);
    }
    this.projects.delete(projectId);
    this.emit(EventType.PROJECT_DELETED, { project });
    return ok({
      project_id: projectId,
      deleted: true,
      storage_purged: true,
      rooms_deleted: projectRooms.length,
    });
  }

  private _projectTransition(
    projectId: string,
    status: "active" | "paused" | "stopped",
    eventType: EventType,
    expectedVersion?: number | null,
  ): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return fail("resource_archived");
    if (expectedVersion != null && expectedVersion !== project.version) {
      return fail(
        "project_version_conflict",
        `version conflict: expected ${expectedVersion}, current ${project.version}`,
      );
    }
    project.status = status;
    if (status !== "active") {
      for (const agent of this._agentsForProject(projectId)) {
        agent.runtimeState = "stopped";
        this._setAgentSpecRuntime(project, agent.agentId, "stopped");
        this._stopChainSimulation(agentKey(projectId, agent.agentId));
      }
    }
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(eventType, { project });
    return ok({ project });
  }

  private _taskCreate(p: {
    title: string;
    project_id: string;
    description?: string;
    agent_id?: string | null;
    agent_name?: string | null;
    priority?: "low" | "normal" | "high" | "urgent";
    parent_id?: string | null;
    dependencies?: string[];
    metadata?: Record<string, unknown>;
  }): CommandOutcome {
    if (!this.projects.has(p.project_id)) {
      return fail(`project not found: ${p.project_id}`);
    }
    const task: TaskInfoPayload = {
      task_id: uuid(),
      project_id: p.project_id,
      title: p.title,
      description: p.description ?? "",
      agent_id: p.agent_id ?? null,
      agent_name: p.agent_name ?? null,
      status: "pending",
      priority: p.priority ?? "normal",
      parent_id: p.parent_id ?? null,
      dependencies: p.dependencies ?? [],
      result: null,
      error: null,
      created_at: isoNow(),
      updated_at: isoNow(),
      started_at: null,
      completed_at: null,
      metadata: p.metadata ?? {},
    };
    this.tasks.set(task.task_id, task);
    this.projects.get(p.project_id)!.task_ids.push(task.task_id);
    this.emit(EventType.TASK_CREATED, { task });
    return ok({ task });
  }

  // ─── Agent ───

  /** 手动 compact：构造 compact 节点入链并广播占用更新（模拟 ghrah.builtin 回合）。 */
  private _agentCompactContext(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
  }): CommandOutcome {
    const state = this._actionState(p);
    if (!state) return fail("agent context not found");
    const session = state.sessions.get(state.activeSessionId);
    if (!session) return fail("no active session");
    const branch = state.branches.get(session.active_branch_id);
    if (!branch) return fail("no active branch");

    const tokensBefore = 6800;
    const tokensAfter = 2400;
    const node: ActionNode = {
      id: uuid(),
      parent_id: branch.head_node_id,
      agent_name: p.agent_name,
      timestamp: isoNow(),
      iteration: session.iteration_count,
      ability_names: ["compact"],
      agent_state: {},
      messages_delta: [
        {
          role: "system",
          content_blocks: [
            { type: "text", text: "[Context Summary] Compacted summary of earlier rounds." },
          ],
          metadata: {},
        },
      ],
      messages_snapshot: null,
      is_snapshot: true,
      action_results: [],
      metadata: {
        node_kind: "compact",
        trigger_source: "manual",
        method: "ghrah.builtin",
        summarized_range: "n-001..n-004",
        kept_recent_nodes: 2,
        tokens_before: tokensBefore,
        tokens_after: tokensAfter,
        post_check: "ok",
        degraded: false,
      },
      session_id: session.session_id,
      created_on_branch_id: branch.branch_id,
    };
    branch.head_node_id = node.id;
    state.nodes.set(node.id, node);
    this.emit(EventType.ACTION_CHAIN_UPDATED, {
      project_id: state.projectId,
      agent_id: state.agentId,
      agent_name: p.agent_name,
      node,
      branch: { ...branch },
    });
    this.emit(EventType.CONTEXT_USAGE_UPDATED, {
      project_id: state.projectId,
      agent_id: state.agentId,
      agent_name: p.agent_name,
      phase: "post_call",
      occupied_tokens: tokensAfter,
      basis: "real",
      budget_tokens: 8192,
      budget_source: "declared",
      compact_threshold: 0.8,
      real_input_tokens: tokensAfter,
      real_output_tokens: 320,
      real_cache_read_tokens: Math.floor(tokensAfter * 0.6),
      real_cache_write_tokens: 256,
      compaction: { occupied_tokens: tokensBefore, threshold: 0.8, needs_compaction: true },
      iteration: session.iteration_count,
    });
    return ok({ executed: true, node_id: node.id, trigger_source: "manual" });
  }

  /** agent_reset：新起默认 Session/main Branch 并激活（J3 语义：旧 Session 全保留）。 */
  private _agentReset(p: { project_id: string; agent_id: string }): CommandOutcome {
    const key = agentKey(p.project_id, p.agent_id);
    const agent = this.agents.get(key);
    if (!agent || agent.runtimeState !== "running") {
      return fail(`agent not found: ${p.agent_id}`);
    }
    const state = this.actionStates.get(key);
    if (!state) return fail("agent context not found");
    const previousSessionId = state.activeSessionId;
    const session = this._newSession(state, agent.name, {});
    state.activeSessionId = session.session_id;
    this.emit(EventType.SESSION_CREATED, {
      project_id: p.project_id,
      agent_id: p.agent_id,
      agent_name: agent.name,
      session,
    });
    this.emit(EventType.SESSION_ACTIVATED, {
      project_id: p.project_id,
      agent_id: p.agent_id,
      agent_name: agent.name,
      session,
    });
    return ok({
      session_id: session.session_id,
      branch_id: session.active_branch_id,
      root_node_id: session.root_node_id,
      previous_session_id: previousSessionId,
    });
  }

  private _getAgentInfo(p: { project_id: string; agent_id: string; name: string }): CommandOutcome {
    const agent = this.agents.get(agentKey(p.project_id, p.agent_id));
    if (!agent) return fail(`agent not found: ${p.agent_id}`);
    if (agent.name !== p.name) return fail("agent_identity_mismatch");
    const key = agentKey(p.project_id, p.agent_id);
    const cumulative = this._cumulativeUsage.get(key) ?? { input: 0, output: 0, cache_read: 0 };
    const context_usage =
      cumulative.input > 0
        ? {
            phase: "post_call",
            occupied_tokens: null,
            basis: "anchor",
            budget_tokens: 8192,
            budget_source: "declared",
            compact_threshold: 0.8,
            real_input_tokens: null,
            real_output_tokens: null,
            real_cache_read_tokens: null,
            real_cache_write_tokens: null,
            cumulative_input_tokens: cumulative.input,
            cumulative_output_tokens: cumulative.output,
            cumulative_cache_read_tokens: cumulative.cache_read,
          }
        : null;
    return ok({
      name: agent.name,
      state: { name: agent.name, initialized: true, runtime_state: agent.runtimeState },
      abilities:
        agent.runtimeState === "running"
          ? agent.abilities.map((ability) => ability.ability_type)
          : agent.abilities,
      available_ability_types: [],
      runtime_state: agent.runtimeState,
      context_usage,
      project_id: p.project_id,
      agent_id: p.agent_id,
    });
  }

  private _actionState(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
  }): AgentActionState | null {
    const state = this.actionStates.get(agentKey(p.project_id, p.agent_id));
    if (!state || state.projectId !== p.project_id || state.agentId !== p.agent_id) return null;
    const record = this.agents.get(agentKey(p.project_id, p.agent_id));
    if (!record || record.name !== p.agent_name) return null;
    return state;
  }

  private _newSession(
    state: AgentActionState,
    agentName: string,
    options: {
      name?: string | null;
      systemPrompt?: string | null;
      originSessionId?: string | null;
      originNodeId?: string | null;
      metadata?: Record<string, unknown>;
    } = {},
  ): SessionInfoPayload {
    const sessionId = uuid();
    const branchId = uuid();
    const rootId = uuid();
    const origin = options.originNodeId ? state.nodes.get(options.originNodeId) : undefined;
    const originMessages = origin ? this._messagesAt(state, origin.id) : [];
    const root: ActionNode = {
      id: rootId,
      parent_id: null,
      agent_name: agentName,
      timestamp: isoNow(),
      iteration: 0,
      ability_names: ["init"],
      agent_state: { ...origin?.agent_state },
      messages_delta: [],
      messages_snapshot: originMessages,
      is_snapshot: true,
      action_results: [],
      metadata: {},
      session_id: sessionId,
      created_on_branch_id: branchId,
    };
    const branch: BranchInfoPayload = {
      branch_id: branchId,
      session_id: sessionId,
      name: "main",
      head_node_id: rootId,
      lifecycle: "open",
      parent_branch_id: null,
      fork_point_node_id: null,
      created_at: isoNow(),
      metadata: {},
    };
    const session: SessionInfoPayload = {
      project_id: state.projectId,
      agent_id: state.agentId,
      session_id: sessionId,
      agent_name: agentName,
      name: options.name ?? `Session ${state.sessions.size + 1}`,
      root_node_id: rootId,
      active_branch_id: branchId,
      lifecycle: "open",
      system_prompt: options.systemPrompt ?? "",
      origin_agent_name: null,
      origin_session_id: options.originSessionId ?? null,
      origin_node_id: options.originNodeId ?? null,
      created_at: isoNow(),
      metadata: options.metadata ?? {},
      message_count: originMessages.length,
      iteration_count: 0,
    };
    state.nodes.set(rootId, root);
    state.branches.set(branchId, branch);
    state.sessions.set(sessionId, session);
    return session;
  }

  private _messagesAt(
    state: AgentActionState,
    nodeId: string,
  ): NonNullable<ActionNode["messages_snapshot"]> {
    const history: ActionNode[] = [];
    let node = state.nodes.get(nodeId);
    while (node) {
      history.push(node);
      node = node.parent_id ? state.nodes.get(node.parent_id) : undefined;
    }
    history.reverse();
    let messages: NonNullable<ActionNode["messages_snapshot"]> = [];
    let start = 0;
    for (let index = history.length - 1; index >= 0; index -= 1) {
      const item = history[index];
      const snapshot = item.messages_snapshot;
      if (item.is_snapshot && snapshot != null) {
        messages = [...snapshot];
        start = index + 1;
        break;
      }
    }
    for (const item of history.slice(start)) messages.push(...item.messages_delta);
    return messages;
  }

  private _sessionCreate(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    name?: string | null;
    origin_session_id?: string | null;
    origin_node_id?: string | null;
    system_prompt?: string | null;
    metadata?: Record<string, unknown>;
  }): CommandOutcome {
    const state = this._actionState(p);
    if (!state) return fail("agent context not found");
    if ((p.origin_session_id == null) !== (p.origin_node_id == null)) {
      return fail("origin_session_id and origin_node_id must be set together");
    }
    if (p.origin_session_id && !state.sessions.has(p.origin_session_id)) {
      return fail(`session not found: ${p.origin_session_id}`);
    }
    if (p.origin_node_id && state.nodes.get(p.origin_node_id)?.session_id !== p.origin_session_id) {
      return fail(`node not found in session: ${p.origin_node_id}`);
    }
    const session = this._newSession(state, p.agent_name, {
      name: p.name,
      originSessionId: p.origin_session_id,
      originNodeId: p.origin_node_id,
      systemPrompt: p.system_prompt,
      metadata: p.metadata,
    });
    this.emit(EventType.SESSION_CREATED, { ...p, session });
    return ok(session);
  }

  private _sessionActivate(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    session_id: string;
  }): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    if (!state || !session || session.lifecycle === "deleted")
      return fail(`session not found: ${p.session_id}`);
    state.activeSessionId = session.session_id;
    this.emit(EventType.SESSION_ACTIVATED, { ...p, session });
    return ok(session);
  }

  private _sessionList(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
  }): CommandOutcome {
    const state = this._actionState(p);
    if (!state) return fail("agent context not found");
    return ok({
      active_session_id: state.activeSessionId,
      sessions: [...state.sessions.values()].filter((item) => item.lifecycle !== "deleted"),
    });
  }

  private _sessionLifecycle(
    p: { project_id: string; agent_id: string; agent_name: string; session_id: string },
    action: "archived" | "deleted",
  ): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    if (!state || !session) return fail(`session not found: ${p.session_id}`);
    if (state.activeSessionId === p.session_id) return fail(`cannot ${action} active session`);
    session.lifecycle = action;
    this.emit(action === "archived" ? EventType.SESSION_ARCHIVED : EventType.SESSION_DELETED, p);
    return ok({ session_id: p.session_id, [action]: true });
  }

  private _branchCreate(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    session_id: string;
    name: string;
    from_node_id?: string | null;
    parent_branch_id?: string | null;
    metadata?: Record<string, unknown>;
  }): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    if (!state || !session) return fail(`session not found: ${p.session_id}`);
    const parent = state.branches.get(p.parent_branch_id ?? session.active_branch_id);
    if (!parent || parent.session_id !== p.session_id) return fail("parent branch not found");
    const headNodeId = p.from_node_id ?? parent.head_node_id;
    const node = state.nodes.get(headNodeId);
    if (!node || node.session_id !== p.session_id) return fail("fork node not found");
    const branch: BranchInfoPayload = {
      branch_id: uuid(),
      session_id: p.session_id,
      name: p.name,
      head_node_id: headNodeId,
      lifecycle: "open",
      parent_branch_id: parent.branch_id,
      fork_point_node_id: headNodeId,
      created_at: isoNow(),
      metadata: p.metadata ?? {},
    };
    state.branches.set(branch.branch_id, branch);
    this.emit(EventType.BRANCH_CREATED, { ...p, branch });
    return ok(branch);
  }

  private _branchActivate(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    session_id: string;
    branch_id: string;
  }): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    const branch = state?.branches.get(p.branch_id);
    if (!state || !session || !branch || branch.session_id !== p.session_id) {
      return fail(`branch not found: ${p.branch_id}`);
    }
    if (state.activeSessionId !== p.session_id) return fail("activate the session first");
    if (branch.lifecycle === "deleted") return fail("cannot activate deleted branch");
    session.active_branch_id = branch.branch_id;
    this.emit(EventType.BRANCH_ACTIVATED, { ...p, branch });
    return ok(branch);
  }

  private _branchList(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    session_id: string;
  }): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    if (!state || !session) return fail(`session not found: ${p.session_id}`);
    return ok({
      session_id: p.session_id,
      // active_branch_id 是 Session 域必填不变量，与运行态 active 指针无关
      active_branch_id: session.active_branch_id,
      branches: [...state.branches.values()].filter(
        (item) => item.session_id === p.session_id && item.lifecycle !== "deleted",
      ),
    });
  }

  private _branchLifecycle(
    p: {
      project_id: string;
      agent_id: string;
      agent_name: string;
      session_id: string;
      branch_id: string;
    },
    action: "archived" | "deleted",
  ): CommandOutcome {
    const state = this._actionState(p);
    const session = state?.sessions.get(p.session_id);
    const branch = state?.branches.get(p.branch_id);
    if (!state || !session || !branch) return fail(`branch not found: ${p.branch_id}`);
    if (state.activeSessionId === p.session_id && session.active_branch_id === p.branch_id) {
      return fail(`cannot ${action} active branch`);
    }
    branch.lifecycle = action;
    this.emit(action === "archived" ? EventType.BRANCH_ARCHIVED : EventType.BRANCH_DELETED, p);
    return ok({ session_id: p.session_id, branch_id: p.branch_id, [action]: true });
  }

  private _chainHistory(p: {
    project_id: string;
    agent_id: string;
    agent_name: string;
    session_id: string;
    branch_id: string;
    limit: number;
  }): CommandOutcome {
    const state = this._actionState(p);
    const branch = state?.branches.get(p.branch_id);
    if (!state || !branch || branch.session_id !== p.session_id) return fail("branch not found");
    const history: ActionNode[] = [];
    let node = state.nodes.get(branch.head_node_id);
    while (node) {
      history.push(node);
      node = node.parent_id ? state.nodes.get(node.parent_id) : undefined;
    }
    history.reverse();
    // 与 Core/Subject 回执形状对齐（Subject units/ledger.py 组装同名字段）
    return ok({
      ...p,
      nodes: p.limit > 0 ? history.slice(-p.limit) : history,
      active_session_id: state.activeSessionId,
    });
  }

  spawnAgent(
    projectId: string,
    input: {
      name: string;
      templateId: string;
      templateRevision: number;
      start: boolean;
      agentId?: string;
    },
  ): CommandOutcome {
    // mock 内部仍需一份 config 来造 Session/快照；它由模板「填充一次」，
    // 不再从请求体读，也不再写回项目 Agent 记录。
    const config: AgentConfigPayload = AgentConfigPayloadSchema.parse({
      name: input.name,
      ...(input.agentId ? { agent_id: input.agentId } : {}),
      endpoint_name: "mock-model",
      system_prompt: "You are a mock Agent created from a template.",
    });
    if (!config.name.trim()) return fail("invalid_request");
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return fail("resource_archived");
    if (input.start && project.status !== "active") return fail("project_not_active");
    if (project.agents.some((agent) => agent.name === config.name)) {
      return fail("agent_name_exists");
    }
    const agentId = config.agent_id || uuid();
    const key = agentKey(projectId, agentId);
    if (this.agents.has(key)) return fail("agent_identity_conflict");
    const normalizedConfig = { ...config, agent_id: agentId };
    const record: AgentRecord = {
      projectId,
      agentId,
      name: config.name,
      config: normalizedConfig,
      // 模板「填充一次」：Agent 的能力来自模板成员，不从请求体读。
      // mock 只有一份内置模板，它授予 send_message（互发消息检查用）与 read_file。
      abilities: MOCK_TEMPLATE_ABILITIES.map((ability) => ({ ...ability })),
      runtimeState: input.start ? "running" : "stopped",
      incarnationId: input.start ? uuid() : null,
    };
    this.agents.set(key, record);
    const actionState: AgentActionState = {
      projectId,
      agentId,
      activeSessionId: "",
      sessions: new Map(),
      branches: new Map(),
      nodes: new Map(),
    };
    if (input.start) {
      const session = this._newSession(actionState, config.name, {
        systemPrompt: config.system_prompt,
      });
      actionState.activeSessionId = session.session_id;
      // 演示数据：每个 Agent 附带一个归档 Session（含归档 Branch），
      // 供 UI 验证 lifecycle 展示与"非活跃 Session 仍有域 active Branch"。
      const archivedSession = this._newSession(actionState, config.name, {
        name: "Archived worklog",
        systemPrompt: config.system_prompt,
      });
      archivedSession.lifecycle = "archived";
      const archivedBranch = [...actionState.branches.values()].find(
        (item) => item.session_id === archivedSession.session_id,
      );
      if (archivedBranch) archivedBranch.lifecycle = "archived";
    }
    this.actionStates.set(key, actionState);
    project.agents.push({
      agent_id: agentId,
      project_id: projectId,
      name: config.name,
      presentation: { title: config.name, description: "", tags: [], icon: "", deprecated: false },
      origin: { template_id: input.templateId, revision: input.templateRevision },
      desired_state: input.start ? "running" : "stopped",
      runtime_status: record.runtimeState === "running" ? "running" : "stopped",
      last_error: "",
      created_at: isoNow(),
      updated_at: isoNow(),
    });
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_AGENT_ADDED, {
      project,
      agent_name: config.name,
      agent_id: agentId,
    });
    if (input.start) this.emit(EventType.AGENT_SPAWNED, this._agentEvent(record));
    if (input.start && this._simulateChains) this._startChainSimulation(key);
    return ok({ ...this._agentEvent(record), runtime_state: record.runtimeState });
  }

  terminateAgent(projectId: string, agentId: string, name: string): CommandOutcome {
    const key = agentKey(projectId, agentId);
    const record = this.agents.get(key);
    if (!record) return fail("agent_not_found");
    if (record.name !== name) return fail("agent_identity_mismatch");
    record.runtimeState = "stopped";
    const project = this.projects.get(projectId);
    if (project) {
      this._setAgentSpecRuntime(project, agentId, "stopped");
      project.version += 1;
      project.updated_at = isoNow();
    }
    this._stopChainSimulation(key);
    this.emit(EventType.AGENT_TERMINATED, this._agentEvent(record));
    record.incarnationId = null;
    this.emit(EventType.ABILITY_TREE_CHANGED, {
      project_id: projectId,
      agent_id: agentId,
      reason: "runtime",
    });
    return ok(this._agentEvent(record));
  }

  private _listAgents(projectId: string): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return fail("resource_archived");
    return ok({
      agents: this._agentsForProject(projectId)
        .filter((agent) => agent.runtimeState === "running")
        .map((agent) => ({
          project_id: projectId,
          agent_id: agent.agentId,
          name: agent.name,
          config: agent.config,
          runtime_state: agent.runtimeState,
          runtime_status: agent.runtimeState,
        })),
    });
  }

  private _startChainSimulation(key: string): void {
    if (!this._cumulativeUsage.has(key))
      this._cumulativeUsage.set(key, { input: 0, output: 0, cache_read: 0 });
    const timer = setInterval(() => {
      const agent = this.agents.get(key);
      if (agent?.runtimeState !== "running") {
        this._stopChainSimulation(key);
        return;
      }
      const state = this.actionStates.get(key);
      if (!state) return;
      const session = state.sessions.get(state.activeSessionId);
      if (!session) return;
      const iteration = session.iteration_count + 1;
      let branch = state.branches.get(session.active_branch_id);
      if (!branch) return;
      // 占用模拟：8192 预算下 2000 + (iteration % 8) * 900 锯齿爬升，触顶回落模拟 compact 重置
      const usageBudget = 8192;
      const usageThreshold = 0.8;
      const usageOccupied = 2000 + ((iteration - 1) % 8) * 900;
      this.emit(EventType.CONTEXT_USAGE_UPDATED, {
        project_id: state.projectId,
        agent_id: state.agentId,
        agent_name: agent.name,
        phase: "pre_call",
        occupied_tokens: usageOccupied,
        basis: "anchor",
        budget_tokens: usageBudget,
        budget_source: "declared",
        compact_threshold: usageThreshold,
        real_input_tokens: null,
        real_output_tokens: null,
        real_cache_read_tokens: null,
        real_cache_write_tokens: null,
        compaction: null,
        iteration: iteration,
      });
      if (iteration % 7 === 0) {
        const retry: BranchInfoPayload = {
          branch_id: uuid(),
          session_id: session.session_id,
          name: `retry-${Math.floor(iteration / 7)}`,
          head_node_id: branch.head_node_id,
          lifecycle: "open",
          parent_branch_id: branch.branch_id,
          fork_point_node_id: branch.head_node_id,
          created_at: isoNow(),
          metadata: { is_rollback: true, error: `simulated failure at iteration ${iteration}` },
        };
        state.branches.set(retry.branch_id, retry);
        session.active_branch_id = retry.branch_id;
        branch = retry;
        this.emit(EventType.BRANCH_CREATED, {
          project_id: state.projectId,
          agent_id: state.agentId,
          agent_name: agent.name,
          session_id: session.session_id,
          branch: retry,
        });
        this.emit(EventType.BRANCH_ACTIVATED, {
          project_id: state.projectId,
          agent_id: state.agentId,
          agent_name: agent.name,
          session_id: session.session_id,
          branch: retry,
        });
      }
      const node: ActionNode = {
        id: uuid(),
        parent_id: branch.head_node_id,
        agent_name: agent.name,
        timestamp: isoNow(),
        iteration,
        ability_names: ["think"],
        agent_state: {},
        messages_delta: [
          {
            role: "ai",
            content_blocks: [
              { type: "text", text: `${agent.name} iteration ${iteration}: working...` },
            ],
            metadata: {},
          },
        ],
        messages_snapshot: null,
        is_snapshot: false,
        action_results: [],
        metadata: iteration % 7 === 0 ? { retry_after_failure: true } : {},
        session_id: session.session_id,
        created_on_branch_id: branch.branch_id,
      };
      branch.head_node_id = node.id;
      state.nodes.set(node.id, node);
      session.iteration_count = iteration;
      this.emit(EventType.ACTION_CHAIN_UPDATED, {
        project_id: state.projectId,
        agent_id: state.agentId,
        agent_name: agent.name,
        node,
        branch: { ...branch },
      });
      this.emit(EventType.CONTEXT_USAGE_UPDATED, {
        project_id: state.projectId,
        agent_id: state.agentId,
        agent_name: agent.name,
        phase: "post_call",
        occupied_tokens: usageOccupied + 400,
        basis: "real",
        budget_tokens: usageBudget,
        budget_source: "declared",
        compact_threshold: usageThreshold,
        real_input_tokens: usageOccupied + 400,
        real_output_tokens: 180,
        // 模拟归一化口径：总输入中约七成命中缓存读
        real_cache_read_tokens: Math.floor((usageOccupied + 400) * 0.7),
        real_cache_write_tokens: 384,
        compaction: {
          occupied_tokens: usageOccupied + 400,
          threshold: usageThreshold,
          needs_compaction: usageOccupied + 400 >= usageBudget * usageThreshold,
        },
        iteration: iteration,
      });
      // 累计用量同步累加（与真实 Core record_real_usage 口径一致）
      const cumulative = this._cumulativeUsage.get(key);
      if (cumulative != null) {
        cumulative.input += usageOccupied + 400;
        cumulative.output += 180;
        cumulative.cache_read += Math.floor((usageOccupied + 400) * 0.7);
      }
    }, this._chainIntervalMs);
    this._chainTimers.set(key, timer);
  }

  private _stopChainSimulation(key: string): void {
    const timer = this._chainTimers.get(key);
    if (timer != null) clearInterval(timer);
    this._chainTimers.delete(key);
  }

  private _agentsForProject(projectId: string): AgentRecord[] {
    return [...this.agents.values()].filter((agent) => agent.projectId === projectId);
  }

  private _setAgentSpecRuntime(
    project: ProjectInfoPayload,
    agentId: string,
    status: "running" | "stopped",
  ): void {
    const spec = project.agents.find((agent) => agent.agent_id === agentId);
    if (!spec) return;
    spec.runtime_status = status;
    spec.last_error = "";
  }

  private _agentEvent(agent: AgentRecord): Record<string, unknown> {
    return {
      project_id: agent.projectId,
      agent_id: agent.agentId,
      name: agent.name,
      config: agent.config,
      incarnation_id: agent.incarnationId ?? "",
      recovery_mode: "",
    };
  }

  // ─── HITL 单路径（core 侧，不模拟 notary） ───

  /** 广播 HITL_REQUEST 并挂起审批；超时（hitlTimeoutMs，默认 30s）自动拒绝。 */
  triggerHitl(
    agentName: string,
    abilityName: string,
    toolArgs: Record<string, unknown> = {},
    context: Record<string, unknown> = {},
    projectId?: string,
    shellRemember?: HITLRequestPayload["shell_remember"],
  ): HITLRequestPayload {
    const matches = [...this.agents.values()].filter(
      (agent) =>
        agent.runtimeState === "running" &&
        agent.name === agentName &&
        (projectId === undefined || agent.projectId === projectId),
    );
    if (matches.length !== 1) {
      throw new Error(`agent scope is ambiguous or missing: ${agentName}`);
    }
    const agent = matches[0];
    const promiseId = `hitl-${uuid().replace(/-/g, "").slice(0, 12)}`;
    const sessionId = this.actionStates.get(
      agentKey(agent.projectId, agent.agentId),
    )?.activeSessionId;
    const createdAt = isoNow();
    const deadlineAt = new Date(Date.now() + this._hitlTimeoutMs).toISOString();
    const payload: HITLRequestPayload = {
      promise_id: promiseId,
      turn_id: `mock-turn-${promiseId}`,
      agent_id: agent.agentId,
      project_id: agent.projectId,
      agent_name: agentName,
      ability_name: abilityName,
      tool_args: toolArgs,
      context,
      reason: "Mock HITL request (mock-server development link)",
      ...(shellRemember === undefined ? {} : { shell_remember: shellRemember }),
    };
    this.pendingHitl.set(payload.promise_id, {
      payload,
      sessionId,
      createdAt,
      deadlineAt,
      status: "pending",
      reason: null,
    });
    if (this._roomTurnExamples && payload.turn_id) {
      const room = [...this.rooms.values()].find(
        (room) =>
          room.project_id === agent.projectId &&
          room.name === context.room &&
          room.members.some((member) => member.subject === agent.agentId),
      );
      if (room) this.seedRoomTurnExamples(room.room_id, agent.agentId, payload.turn_id);
    }
    const timer = setTimeout(() => {
      const pending = this.pendingHitl.get(payload.promise_id);
      if (pending && pending.status === "pending") {
        this._settleHitl(pending, "timeout", "hitl timeout");
      }
      this._hitlTimers.delete(payload.promise_id);
    }, this._hitlTimeoutMs);
    this._hitlTimers.set(payload.promise_id, timer);
    this.emit(EventType.HITL_REQUEST, payload as unknown as Record<string, unknown>);
    return payload;
  }

  resolveHitl(promiseId: string, approved: boolean, reason: string | null): CommandOutcome {
    const pending = this.pendingHitl.get(promiseId);
    if (pending?.status !== "pending") {
      return fail(`unknown hitl request: ${promiseId}`);
    }
    this._settleHitl(pending, approved ? "approved" : "rejected", reason);
    const timer = this._hitlTimers.get(promiseId);
    if (timer != null) clearTimeout(timer);
    this._hitlTimers.delete(promiseId);
    return ok({ promise_id: promiseId, status: pending.status, reason });
  }

  private _settleHitl(
    pending: PendingHitl,
    status: "approved" | "rejected" | "timeout",
    reason: string | null,
  ) {
    pending.status = status === "timeout" ? "rejected" : status;
    pending.reason = reason;
    const { payload } = pending;
    this.emit(EventType.HITL_RESOLVED, {
      project_id: payload.project_id,
      agent_id: payload.agent_id,
      promise_id: payload.promise_id,
      agent_name: payload.agent_name,
      ability_name: payload.ability_name,
      turn_id: payload.turn_id ?? null,
      status,
    });
  }
}
