import {
  type AbilityDefinitionPayload,
  type ActionNode,
  AgentCompactContextPayloadSchema,
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
  type HITLRequestPayload,
  HITLResponsePayloadSchema,
  ListAgentsPayloadSchema,
  PluginNegotiatePayloadSchema,
  type PythonHalfInfo,
  ProjectCreatePayloadSchema,
  ProjectDeletePayloadSchema,
  ProjectIdPayloadSchema,
  type ProjectInfoPayload,
  ProjectLifecyclePayloadSchema,
  ProjectListPayloadSchema,
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
  RoomSendPayloadSchema,
  RoomUpdatePayloadSchema,
  SessionActivatePayloadSchema,
  SessionArchivePayloadSchema,
  SessionCreatePayloadSchema,
  SessionDeletePayloadSchema,
  type SessionInfoPayload,
  SessionListPayloadSchema,
  SpawnAgentPayloadSchema,
  TaskClaimListPayloadSchema,
  type TaskClaimPayload,
  TaskCreatePayloadSchema,
  TaskDumpPayloadSchema,
  TaskIdPayloadSchema,
  type TaskInfoPayload,
  TaskListPayloadSchema,
  TerminateAgentPayloadSchema,
} from "@ghrah/protocol";
import { ZodError, type z } from "zod";

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
}

interface AgentRecord {
  projectId: string;
  agentId: string;
  clusterId: string;
  name: string;
  config: AgentConfigPayload;
  abilities: AbilityDefinitionPayload[];
  runtimeState: "running" | "stopped";
}

interface AgentActionState {
  projectId: string;
  agentId: string;
  clusterId: string;
  activeSessionId: string;
  sessions: Map<string, SessionInfoPayload>;
  branches: Map<string, BranchInfoPayload>;
  nodes: Map<string, ActionNode>;
}

export interface PendingHitl {
  payload: HITLRequestPayload;
  status: "pending" | "approved" | "rejected";
  reason: string | null;
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
  /** 场景声明的 Python 半清单（mock 协商数据源，非权威）。 */
  pythonHalves: PythonHalfInfo[] = [];
  /** 归因种子：claims（含内嵌 evidence 的冻结副本）与独立 evidence 表投影。 */
  readonly claims = new Map<string, TaskClaimPayload>();
  readonly evidence = new Map<string, TaskClaimPayload["evidence"][number]>();

  /** 由 server 注入：广播事件（带 seq_id 分配与订阅过滤）。 */
  emit: EmitFn = () => {};

  private readonly _hitlTimeoutMs: number;
  private readonly _chainIntervalMs: number;
  private readonly _simulateChains: boolean;
  private readonly _chainTimers = new Map<string, ReturnType<typeof setInterval>>();
  private readonly _chainIterations = new Map<string, number>();
  // 累计用量模拟（成本口径）：post_call 模拟时累加，链模拟停止后保留供 get_agent_info 恢复
  private readonly _cumulativeUsage = new Map<
    string,
    { input: number; output: number; cache_read: number }
  >();
  private readonly _hitlTimers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(options: MockStateOptions = {}) {
    this._hitlTimeoutMs = options.hitlTimeoutMs ?? 30_000;
    this._chainIntervalMs = options.chainIntervalMs ?? 2_000;
    this._simulateChains = options.simulateChains ?? false;
  }

  /** 清理所有定时器（chain 模拟 + HITL 超时）。 */
  dispose(): void {
    for (const timer of this._chainTimers.values()) clearInterval(timer);
    this._chainTimers.clear();
    for (const timer of this._hitlTimers.values()) clearTimeout(timer);
    this._hitlTimers.clear();
  }

  // ─── 命令路由 ───

  handleCommand(commandType: string, rawPayload: Record<string, unknown>): CommandOutcome {
    switch (commandType) {
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
        return this._parse(ProjectIdPayloadSchema, rawPayload, (p) =>
          this._projectTransition(p.project_id, "paused", EventType.PROJECT_PAUSED),
        );
      case CommandType.PROJECT_RESUME:
        return this._parse(ProjectIdPayloadSchema, rawPayload, (p) =>
          this._projectTransition(p.project_id, "active", EventType.PROJECT_RESUMED),
        );
      case CommandType.PROJECT_STOP:
        return this._parse(ProjectIdPayloadSchema, rawPayload, (p) =>
          this._projectTransition(p.project_id, "stopped", EventType.PROJECT_STOPPED),
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
        return this._parse(SpawnAgentPayloadSchema, rawPayload, (p) =>
          this.spawnAgent(p.project_id, p.config, p.abilities ?? [], p.cluster_id),
        );
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
    return ok({ room_id: p.room_id, entries, count: entries.length });
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
    return ok({ entry });
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
  }): CommandOutcome {
    const project: ProjectInfoPayload = {
      project_id: uuid(),
      name: p.name,
      description: p.description ?? "",
      project_root_locator: p.project_root_locator ?? "",
      manifest_ref: p.manifest_ref ?? "",
      cluster_ids: [],
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
    if (projectRooms.length > 0 && !cascadeRooms) return fail("project_has_rooms");
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
  ): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return fail("resource_archived");
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
    cluster_id?: string;
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
      cluster_id: state.clusterId,
      agent_name: p.agent_name,
      node,
      branch: { ...branch },
    });
    this.emit(EventType.CONTEXT_USAGE_UPDATED, {
      project_id: state.projectId,
      agent_id: state.agentId,
      cluster_id: state.clusterId,
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
      cluster_id: state.clusterId,
      agent_name: agent.name,
      session,
    });
    this.emit(EventType.SESSION_ACTIVATED, {
      project_id: p.project_id,
      agent_id: p.agent_id,
      cluster_id: state.clusterId,
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
      abilities: [],
      context_usage,
      project_id: p.project_id,
      agent_id: p.agent_id,
      cluster_id: agent.clusterId ?? "",
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
      cluster_id: state.clusterId,
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
    config: AgentConfigPayload,
    abilities: AbilityDefinitionPayload[] = [],
    requestedClusterId = "",
  ): CommandOutcome {
    const project = this.projects.get(projectId);
    if (!project) return fail(`project not found: ${projectId}`);
    if (project.archived_at != null) return fail("resource_archived");
    if (project.status !== "active") return fail("project_not_active");
    if (project.agents.some((agent) => agent.name === config.name)) {
      return fail("agent_name_exists");
    }
    const agentId = config.agent_id || uuid();
    const key = agentKey(projectId, agentId);
    if (this.agents.has(key)) return fail("agent_identity_conflict");
    const clusterId = requestedClusterId || `${projectId}:default`;
    const normalizedConfig = { ...config, agent_id: agentId };
    const record: AgentRecord = {
      projectId,
      agentId,
      clusterId,
      name: config.name,
      config: normalizedConfig,
      abilities,
      runtimeState: "running",
    };
    this.agents.set(key, record);
    const actionState: AgentActionState = {
      projectId,
      agentId,
      clusterId,
      activeSessionId: "",
      sessions: new Map(),
      branches: new Map(),
      nodes: new Map(),
    };
    const session = this._newSession(actionState, config.name, {
      systemPrompt: config.system_prompt,
    });
    actionState.activeSessionId = session.session_id;
    this.actionStates.set(key, actionState);
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
    project.agents.push({
      agent_id: agentId,
      name: config.name,
      cluster_id: clusterId,
      manifest_ref: "",
      instance_manifest_path: "",
      system_prompt: config.system_prompt,
      abilities: null,
      desired_state: "running",
      path_grants: [],
      runtime_status: "running",
      runtime_error: "",
    });
    if (!project.cluster_ids.includes(clusterId)) project.cluster_ids.push(clusterId);
    project.version += 1;
    project.updated_at = isoNow();
    this.emit(EventType.PROJECT_AGENT_ADDED, {
      project,
      agent_name: config.name,
      agent_id: agentId,
    });
    this.emit(EventType.AGENT_SPAWNED, this._agentEvent(record));
    if (this._simulateChains) this._startChainSimulation(key);
    return ok({ ...this._agentEvent(record), runtime_state: "running" });
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
          cluster_id: agent.clusterId,
          name: agent.name,
          config: agent.config,
          runtime_state: agent.runtimeState,
          runtime_status: agent.runtimeState,
        })),
    });
  }

  private _startChainSimulation(key: string): void {
    this._chainIterations.set(key, 0);
    this._cumulativeUsage.set(key, { input: 0, output: 0, cache_read: 0 });
    const timer = setInterval(() => {
      const agent = this.agents.get(key);
      if (agent?.runtimeState !== "running") {
        this._stopChainSimulation(key);
        return;
      }
      const iteration = (this._chainIterations.get(key) ?? 0) + 1;
      this._chainIterations.set(key, iteration);
      const state = this.actionStates.get(key);
      if (!state) return;
      const session = state.sessions.get(state.activeSessionId);
      if (!session) return;
      let branch = state.branches.get(session.active_branch_id);
      if (!branch) return;
      // 占用模拟：8192 预算下 2000 + (iteration % 8) * 900 锯齿爬升，触顶回落模拟 compact 重置
      const usageBudget = 8192;
      const usageThreshold = 0.8;
      const usageOccupied = 2000 + ((iteration - 1) % 8) * 900;
      this.emit(EventType.CONTEXT_USAGE_UPDATED, {
        project_id: state.projectId,
        agent_id: state.agentId,
        cluster_id: state.clusterId,
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
        cluster_id: state.clusterId,
        agent_name: agent.name,
        node,
        branch: { ...branch },
      });
      this.emit(EventType.CONTEXT_USAGE_UPDATED, {
        project_id: state.projectId,
        agent_id: state.agentId,
        cluster_id: state.clusterId,
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
    this._chainIterations.delete(key);
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
    spec.runtime_error = "";
  }

  private _agentEvent(agent: AgentRecord): Record<string, unknown> {
    return {
      project_id: agent.projectId,
      agent_id: agent.agentId,
      cluster_id: agent.clusterId,
      name: agent.name,
      config: agent.config,
      incarnation_id: "mock",
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
    const payload: HITLRequestPayload = {
      promise_id: `hitl-${uuid().replace(/-/g, "").slice(0, 12)}`,
      agent_id: agent.agentId,
      project_id: agent.projectId,
      cluster_id: agent.clusterId,
      agent_name: agentName,
      ability_name: abilityName,
      tool_args: toolArgs,
      context,
      reason: "Mock HITL request (mock-server development link)",
    };
    this.pendingHitl.set(payload.promise_id, { payload, status: "pending", reason: null });
    const timer = setTimeout(() => {
      const pending = this.pendingHitl.get(payload.promise_id);
      if (pending && pending.status === "pending") {
        pending.status = "rejected";
        pending.reason = "hitl timeout";
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
    pending.status = approved ? "approved" : "rejected";
    pending.reason = reason;
    const timer = this._hitlTimers.get(promiseId);
    if (timer != null) clearTimeout(timer);
    this._hitlTimers.delete(promiseId);
    return ok({ promise_id: promiseId, status: pending.status, reason });
  }
}
