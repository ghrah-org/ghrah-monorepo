// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  ActionChainUpdatedPayload,
  ActionNode,
  AgentSpawnedPayload,
  AgentTerminatedPayload,
  BranchEventPayload,
  BranchInfoPayload,
  BranchLifecyclePayload,
  BranchListResultPayload,
  CommandResultPayload,
  ContextUsageUpdatedPayload,
  HITLRequestPayload,
  HITLResolvedPayload,
  ManifestAbilityEventPayload,
  ManifestAgentEventPayload,
  PluginCrashedPayload,
  PluginLifecyclePayload,
  PluginNegotiateResultPayload,
  ProjectAgentEventPayload,
  ProjectEventPayload,
  ProjectInfoPayload,
  RoomDeletedEventPayload,
  RoomEventPayload,
  RoomInfoPayload,
  RoomLogEntryPayload,
  RoomLogEventPayload,
  RoomMemberEventPayload,
  ServerMessage,
  SessionActivatedPayload,
  SessionArchivedPayload,
  SessionCreatedPayload,
  SessionDeletedPayload,
  SessionInfoPayload,
  SessionListResultPayload,
  TaskClaimEventPayload,
  TaskClaimListResultPayload,
  TaskDumpResultPayload,
  TaskEventPayload,
  TsHalfReport,
} from "@ghrah/protocol";
import { CommandType, EventType, SystemType } from "@ghrah/protocol";
import { clearAgentDerived, clearProjectDerived, clearRoomDerived } from "./clear-derived.js";
import type { ObserverClient } from "./client.js";
import { createFrameBatcher, type FrameBatcher } from "./frame-batcher.js";
import {
  type AgentTarget,
  agentKey,
  type ChainTarget,
  chainKey,
  type SessionTarget,
  sessionKey,
} from "./scope.js";
import { useActionChainsStore } from "./stores/action-chains.js";
import { type AgentListItem, useAgentsStore } from "./stores/agents.js";
import { useBranchesStore } from "./stores/branches.js";
import { useChangesStore } from "./stores/changes.js";
import { useChatStore } from "./stores/chat.js";
import { useConnectionStore } from "./stores/connection.js";
import { useContextUsageStore } from "./stores/context-usage.js";
import { useHitlStore } from "./stores/hitl.js";
import { useManifestsStore } from "./stores/manifests.js";
import { usePluginsStore } from "./stores/plugins.js";
import { useProjectsStore } from "./stores/projects.js";
import { useRoomsStore } from "./stores/rooms.js";
import { useSessionsStore } from "./stores/sessions.js";
import { useTasksStore } from "./stores/tasks.js";

export interface ConnectStoresOptions {
  /** High-volume event batcher; defaults to requestAnimationFrame/microtask batching. */
  batcher?: FrameBatcher;
  /**
   * 宿主已装配的 TS 半清单（协商上报用）。必须在连接前就绪（快照语义）：
   * 连接后补装不会重发协商。observer-core 不知道插件目录/URL/import map。
   */
  tsHalfProvider?: () => TsHalfReport[];
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function boolField(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function connectStores(
  client: ObserverClient,
  options: ConnectStoresOptions = {},
): () => void {
  const connection = useConnectionStore();
  const agents = useAgentsStore();
  const chains = useActionChainsStore();
  const sessions = useSessionsStore();
  const branches = useBranchesStore();
  const hitl = useHitlStore();
  const chat = useChatStore();
  const changes = useChangesStore();
  const contextUsage = useContextUsageStore();
  const manifests = useManifestsStore();
  const rooms = useRoomsStore();
  const projects = useProjectsStore();
  const tasks = useTasksStore();
  const plugins = usePluginsStore();
  const batcher = options.batcher ?? createFrameBatcher();

  /** 连接级协商 + 归因读通量（A10 连接半边；onConnected/onReconnected 互斥触发）。 */
  function syncPluginAndTaskState() {
    void client
      .pluginNegotiate(options.tsHalfProvider ? options.tsHalfProvider() : [])
      .then(() => undefined)
      .catch(() => undefined);
    void client
      .taskDump({})
      .then(() => undefined)
      .catch(() => undefined);
  }

  client.onConnected(() => {
    connection.setConnected();
    syncPluginAndTaskState();
  });
  client.onDisconnected(() => connection.setDisconnected());
  client.onReconnecting(() => connection.setReconnecting());
  client.onReconnected(() => {
    connection.setConnected();
    syncPluginAndTaskState();
    // 重连即触发重同步（S3.1/M3b）：断线窗口内的事件已丢失，须从权威读通量
    // 重建投影。对全部已知 operable 项目重放 listAgents → syncAgentActionState
    // 链（后者 nextGeneration 使旧轮次结果全部逻辑过期）。
    for (const [projectId] of projects.projects) {
      if (!projects.isProjectOperable(projectId)) continue;
      void client
        .listAgents(projectId)
        .then(() => undefined)
        .catch(() => undefined);
    }
  });

  client.on(EventType.AGENT_SPAWNED, (msg: ServerMessage) => {
    const payload = msg.payload as AgentSpawnedPayload;
    const project = projects.projects.get(payload.project_id);
    if (project?.archived_at != null) return;
    agents.onAgentSpawned(payload, project);
  });
  client.on(EventType.AGENT_TERMINATED, (msg: ServerMessage) => {
    const target = agents.onAgentTerminated(msg.payload as AgentTerminatedPayload);
    if (target) clearAgentDerived(target);
  });

  client.on(EventType.ACTION_CHAIN_UPDATED, (msg: ServerMessage) => {
    const payload = msg.payload as ActionChainUpdatedPayload;
    const node = payload.node;
    if (!node) return;
    batcher.add(() => {
      // U3b：branch 快照必须与 node 归桶一致——异常事件拒绝推进 Head，
      // 防止把 Head 写到 node 实际不归属的桶。
      const branchSnapshotConsistent =
        payload.branch == null || payload.branch.branch_id === node.created_on_branch_id;
      if (!chains.onActionChainUpdated(payload)) return;
      const target: ChainTarget = {
        projectId: payload.project_id,
        agentId: payload.agent_id,
        agentName: payload.agent_name,
        sessionId: node.session_id,
        branchId: node.created_on_branch_id,
      };
      changes.onActionChainNode(target, node);
      // commit 事件携带的权威 Branch 快照：只前移 Head（单调校验 + 不回退）。
      if (branchSnapshotConsistent && payload.branch?.branch_id) {
        branches.advanceHead(target, payload.branch, node);
      }
    });
  });

  client.on(EventType.HITL_REQUEST, (msg: ServerMessage) =>
    hitl.onHitlRequest(msg.payload as HITLRequestPayload),
  );

  client.on(EventType.HITL_RESOLVED, (msg: ServerMessage) => {
    const payload = msg.payload as HITLResolvedPayload;
    hitl.removeRequest(payload.promise_id);
  });

  client.on(EventType.CONTEXT_USAGE_UPDATED, (msg: ServerMessage) =>
    batcher.add(() =>
      contextUsage.onContextUsageUpdated(
        msg.payload as ContextUsageUpdatedPayload,
        msg.timestamp ?? null,
      ),
    ),
  );

  client.on(SystemType.COMMAND_RESULT, (msg: ServerMessage) => {
    const payload = msg.payload as CommandResultPayload;
    if (!payload.success || !payload.data) return;
    const data = payload.data as Record<string, unknown>;
    const context = client.getRequestContext(payload.request_id);
    const command =
      context?.command ??
      stringField((payload as unknown as Record<string, unknown>).original_command);

    if (command === CommandType.LIST_AGENTS && Array.isArray(data.agents)) {
      const list = data.agents as AgentListItem[];
      const projectId =
        stringField(context?.payload.project_id) ?? stringField(list[0]?.project_id);
      if (!projectId) return;
      const removed = agents.replaceProjectAgents(
        projectId,
        list,
        projects.projects.get(projectId),
      );
      for (const target of removed) clearAgentDerived(target);
      void syncAgentActionState(
        agents.agentsForProject(projectId).map(({ projectId, agentId, agentName }) => ({
          projectId,
          agentId,
          agentName,
        })),
      );
    }

    if (command === CommandType.ROOM_LIST && Array.isArray(data.rooms)) {
      const list = data.rooms as RoomInfoPayload[];
      const projectId =
        stringField(context?.payload.project_id) ?? stringField(list[0]?.project_id);
      const requestedStatus = stringField(context?.payload.status);
      const status =
        requestedStatus === "archived" || requestedStatus === "active"
          ? requestedStatus
          : (list[0]?.status ?? "active");
      if (projectId) {
        const removed = rooms.replaceProjectRooms(projectId, list, status);
        for (const target of removed) clearRoomDerived(target, false);
      }
    }

    if (command === CommandType.ROOM_GET_LOG && Array.isArray(data.entries)) {
      const entries = data.entries as RoomLogEntryPayload[];
      const roomId =
        stringField(context?.payload.room_id) ??
        stringField(data.room_id) ??
        stringField(entries[0]?.room_id);
      if (roomId) rooms.setRoomLog(roomId, entries);
    }

    if (command === CommandType.GET_AGENT_INFO && data.context_usage != null) {
      const usage = data.context_usage as Record<string, unknown>;
      const target = {
        projectId: stringField(context?.payload.project_id) ?? stringField(data.project_id),
        agentId: stringField(context?.payload.agent_id) ?? stringField(data.agent_id),
        agentName: stringField(context?.payload.name) ?? stringField(data.agent_name),
      };
      const input = usage.real_input_tokens;
      if (target.projectId && target.agentId && typeof input === "number") {
        contextUsage.restoreSnapshot(
          {
            project_id: target.projectId,
            agent_id: target.agentId,
            cluster_id: "",
            agent_name: target.agentName ?? "",
            phase: "post_call",
            occupied_tokens:
              typeof usage.occupied_tokens === "number" ? usage.occupied_tokens : null,
            basis: stringField(usage.basis) ?? "anchor",
            budget_tokens: typeof usage.budget_tokens === "number" ? usage.budget_tokens : 0,
            budget_source: stringField(usage.budget_source),
            compact_threshold:
              typeof usage.compact_threshold === "number" ? usage.compact_threshold : null,
            real_input_tokens: input,
            real_output_tokens:
              typeof usage.real_output_tokens === "number" ? usage.real_output_tokens : null,
            real_cache_read_tokens:
              typeof usage.real_cache_read_tokens === "number"
                ? usage.real_cache_read_tokens
                : null,
            real_cache_write_tokens:
              typeof usage.real_cache_write_tokens === "number"
                ? usage.real_cache_write_tokens
                : null,
            compaction: null,
            iteration: null,
          },
          {
            input:
              typeof usage.cumulative_input_tokens === "number"
                ? usage.cumulative_input_tokens
                : input,
            output:
              typeof usage.cumulative_output_tokens === "number"
                ? usage.cumulative_output_tokens
                : 0,
            cache_read:
              typeof usage.cumulative_cache_read_tokens === "number"
                ? usage.cumulative_cache_read_tokens
                : 0,
          },
          msg.timestamp ?? null,
        );
      }
    }

    if (command === CommandType.PROJECT_LIST && Array.isArray(data.projects)) {
      const list = data.projects as ProjectInfoPayload[];
      const archived = boolField(context?.payload.archived) ?? false;
      const previous = new Set(
        (archived ? projects.archivedProjectList : projects.projectList).map(
          (project) => project.project_id,
        ),
      );
      projects.replaceProjects(list, archived);
      const retained = new Set(
        list
          .filter((project) => (project.archived_at != null) === archived)
          .map((project) => project.project_id),
      );
      for (const projectId of previous) {
        if (!retained.has(projectId)) clearProjectDerived(projectId);
      }
    }

    // 插件协商回执 → plugins store（权威结果，覆盖旧投影）。
    if (command === CommandType.PLUGIN_NEGOTIATE) {
      plugins.setNegotiationResult(data as unknown as PluginNegotiateResultPayload);
    }

    // task_dump 读通量 → tasks 归因投影重建（claims/evidence 全量替换）。
    if (command === CommandType.TASK_DUMP) {
      tasks.replaceFromDump(data as unknown as TaskDumpResultPayload);
    }

    // task_list_claims 查询回执 → claims 合并入 store（upsert，不触碰 tasks 投影）。
    if (command === CommandType.TASK_LIST_CLAIMS && Array.isArray(data.claims)) {
      const list = data as unknown as TaskClaimListResultPayload;
      for (const claim of list.claims) tasks.upsertClaim(claim);
    }
  });

  client.on(EventType.AGENT_ERROR, () => {
    // A dedicated runtime error projection can consume this later.
  });

  client.on(EventType.MANIFEST_ABILITY_CREATED, (msg: ServerMessage) =>
    manifests.onManifestAbilityCreated(msg.payload as ManifestAbilityEventPayload),
  );
  client.on(EventType.MANIFEST_ABILITY_UPDATED, (msg: ServerMessage) =>
    manifests.onManifestAbilityInvalidated(msg.payload as ManifestAbilityEventPayload),
  );
  client.on(EventType.MANIFEST_ABILITY_DELETED, (msg: ServerMessage) =>
    manifests.onManifestAbilityDeleted(msg.payload as ManifestAbilityEventPayload),
  );
  client.on(EventType.MANIFEST_AGENT_CREATED, (msg: ServerMessage) =>
    manifests.onManifestAgentCreated(msg.payload as ManifestAgentEventPayload),
  );
  client.on(EventType.MANIFEST_AGENT_UPDATED, (msg: ServerMessage) =>
    manifests.onManifestAgentInvalidated(msg.payload as ManifestAgentEventPayload),
  );
  client.on(EventType.MANIFEST_AGENT_DELETED, (msg: ServerMessage) =>
    manifests.onManifestAgentDeleted(msg.payload as ManifestAgentEventPayload),
  );

  client.on(EventType.ROOM_CREATED, (msg: ServerMessage) =>
    rooms.onRoomCreated(msg.payload as RoomEventPayload),
  );
  client.on(EventType.ROOM_UPDATED, (msg: ServerMessage) =>
    rooms.onRoomUpdated(msg.payload as RoomEventPayload),
  );
  client.on(EventType.ROOM_ARCHIVED, (msg: ServerMessage) => {
    const payload = msg.payload as RoomEventPayload;
    rooms.onRoomArchived(payload);
    clearRoomDerived({ projectId: payload.room.project_id, roomId: payload.room.room_id }, false);
  });
  client.on(EventType.ROOM_RESTORED, (msg: ServerMessage) => {
    const payload = msg.payload as RoomEventPayload;
    rooms.onRoomRestored(payload, projects.isProjectOperable(payload.room.project_id));
  });
  client.on(EventType.ROOM_DELETED, (msg: ServerMessage) => {
    const payload = msg.payload as RoomDeletedEventPayload;
    clearRoomDerived({ projectId: payload.project_id, roomId: payload.room_id });
  });
  client.on(EventType.ROOM_MEMBER_JOINED, (msg: ServerMessage) =>
    rooms.onRoomMemberJoined(msg.payload as RoomMemberEventPayload),
  );
  client.on(EventType.ROOM_MEMBER_LEFT, (msg: ServerMessage) =>
    rooms.onRoomMemberLeft(msg.payload as RoomMemberEventPayload),
  );
  client.on(EventType.ROOM_LOG_APPENDED, (msg: ServerMessage) => {
    const payload = msg.payload as RoomLogEventPayload;
    if (!payload.entry) return;
    batcher.add(() => {
      const room =
        rooms.activeRooms.get(payload.entry.room_id) ??
        rooms.archivedRooms.get(payload.entry.room_id);
      rooms.onRoomLogAppended(payload);
      if (room) chat.onRoomLogAppended(payload.entry, room.project_id);
    });
  });

  const ordinaryProjectEvent = (msg: ServerMessage) => {
    const payload = msg.payload as ProjectEventPayload | ProjectAgentEventPayload;
    projects.onProjectEvent(payload);
    agents.applyProjectRuntimeState(payload.project);
    if (msg.type === EventType.PROJECT_RESUMED) void syncProject(payload.project.project_id);
  };
  client.on(EventType.PROJECT_CREATED, ordinaryProjectEvent);
  client.on(EventType.PROJECT_UPDATED, ordinaryProjectEvent);
  client.on(EventType.PROJECT_PAUSED, ordinaryProjectEvent);
  client.on(EventType.PROJECT_RESUMED, ordinaryProjectEvent);
  client.on(EventType.PROJECT_STOPPED, ordinaryProjectEvent);
  client.on(EventType.PROJECT_RECOVERY_SET, ordinaryProjectEvent);
  client.on(EventType.PROJECT_AGENT_ADDED, (msg: ServerMessage) => {
    const payload = msg.payload as ProjectAgentEventPayload;
    projects.onProjectEvent(payload);
    void syncProject(payload.project.project_id);
  });
  client.on(EventType.PROJECT_AGENT_REMOVED, (msg: ServerMessage) => {
    const payload = msg.payload as ProjectAgentEventPayload;
    projects.onProjectEvent(payload);
    if (!payload.agent_id) return;
    const target: AgentTarget = {
      projectId: payload.project.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
    };
    agents.removeAgent(target);
    clearAgentDerived(target);
  });
  client.on(EventType.PROJECT_ARCHIVED, (msg: ServerMessage) => {
    const payload = msg.payload as ProjectEventPayload;
    projects.onProjectEvent(payload);
    clearProjectDerived(payload.project.project_id);
  });
  client.on(EventType.PROJECT_RESTORED, (msg: ServerMessage) => {
    const payload = msg.payload as ProjectEventPayload;
    projects.onProjectEvent(payload);
    clearProjectDerived(payload.project.project_id);
    void syncProject(payload.project.project_id);
  });
  client.on(EventType.PROJECT_DELETED, (msg: ServerMessage) => {
    const payload = msg.payload as ProjectEventPayload;
    clearProjectDerived(payload.project.project_id);
    projects.onProjectDeleted(payload);
  });

  client.on(EventType.SESSION_CREATED, (msg: ServerMessage) =>
    sessions.onSessionCreated(msg.payload as SessionCreatedPayload),
  );
  client.on(EventType.SESSION_ACTIVATED, (msg: ServerMessage) =>
    sessions.onSessionActivated(msg.payload as SessionActivatedPayload),
  );
  const removeSession = (msg: ServerMessage) => {
    const target = sessions.removeSession(
      msg.payload as SessionArchivedPayload | SessionDeletedPayload,
    );
    if (!target) return;
    branches.clearSession(target);
    chains.clearSession(target);
  };
  client.on(EventType.SESSION_ARCHIVED, removeSession);
  client.on(EventType.SESSION_DELETED, removeSession);
  client.on(EventType.SESSION_LIST_RESULT, (msg: ServerMessage) => {
    const payload = msg.payload as SessionListResultPayload;
    if (!payload.project_id || !payload.agent_id || !payload.agent_name) return;
    const target: AgentTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
    };
    // 推送路径（无请求发起时刻）：事件已落地状态优先，推送只补缺失实体
    // 与未覆盖指针——防离散事件先到、推送旧快照晚到时回退（M3）。
    sessions.mergePushedAgentSessions(target, payload.sessions, payload.active_session_id || null);
  });

  client.on(EventType.BRANCH_CREATED, (msg: ServerMessage) =>
    branches.onBranchCreated(msg.payload as BranchEventPayload),
  );
  client.on(EventType.BRANCH_ACTIVATED, (msg: ServerMessage) =>
    branches.onBranchActivated(msg.payload as BranchEventPayload),
  );
  const removeBranch = (msg: ServerMessage) => {
    const target = branches.removeBranch(msg.payload as BranchLifecyclePayload);
    if (target) chains.clearChain(target);
  };
  client.on(EventType.BRANCH_ARCHIVED, removeBranch);
  client.on(EventType.BRANCH_DELETED, removeBranch);
  client.on(EventType.BRANCH_LIST_RESULT, (msg: ServerMessage) => {
    const payload = msg.payload as BranchListResultPayload;
    if (!payload.project_id || !payload.agent_id || !payload.agent_name || !payload.session_id) {
      return;
    }
    const target: SessionTarget = {
      projectId: payload.project_id,
      agentId: payload.agent_id,
      agentName: payload.agent_name,
      sessionId: payload.session_id,
    };
    // 同 SESSION_LIST_RESULT：事件已落地状态优先，推送只补缺失（M3）。
    branches.mergePushedSessionBranches(target, payload.branches, payload.active_branch_id || null);
  });

  const taskEventTypes = [
    EventType.TASK_CREATED,
    EventType.TASK_UPDATED,
    EventType.TASK_ASSIGNED,
    EventType.TASK_STARTED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
    EventType.TASK_CANCELED,
    EventType.TASK_BLOCKED,
    EventType.TASK_DELETED,
  ] as const;
  for (const eventType of taskEventTypes) {
    client.on(eventType, (msg: ServerMessage) =>
      tasks.onTaskEvent(eventType, msg.payload as TaskEventPayload),
    );
  }

  // 归因切面：TASK_DELIVERED/VERIFIED/REJECTED（task + claim 信封）。
  for (const eventType of [
    EventType.TASK_DELIVERED,
    EventType.TASK_VERIFIED,
    EventType.TASK_REJECTED,
  ]) {
    client.on(eventType, (msg: ServerMessage) =>
      tasks.onClaimEvent(eventType, msg.payload as TaskClaimEventPayload),
    );
  }

  // 插件域事件：lifecycle/crashed 落 plugins store；negotiated → 重发协商（A10 变更半边）。
  client.on(EventType.PLUGIN_ENABLED, (msg: ServerMessage) =>
    plugins.onLifecycleEvent(msg.payload as PluginLifecyclePayload),
  );
  client.on(EventType.PLUGIN_DISABLED, (msg: ServerMessage) =>
    plugins.onLifecycleEvent(msg.payload as PluginLifecyclePayload),
  );
  client.on(EventType.PLUGIN_CRASHED, (msg: ServerMessage) =>
    plugins.onCrashed(msg.payload as PluginCrashedPayload),
  );
  client.on(EventType.PLUGIN_NEGOTIATED, (_msg: ServerMessage) => {
    void client
      .pluginNegotiate(options.tsHalfProvider ? options.tsHalfProvider() : [])
      .then(() => undefined)
      .catch(() => undefined);
  });

  const eventTypes = [
    EventType.AGENT_SPAWNED,
    EventType.AGENT_TERMINATED,
    EventType.ACTION_CHAIN_UPDATED,
    EventType.CONTEXT_USAGE_UPDATED,
    EventType.HITL_REQUEST,
    EventType.AGENT_ERROR,
    EventType.MANIFEST_ABILITY_CREATED,
    EventType.MANIFEST_ABILITY_UPDATED,
    EventType.MANIFEST_ABILITY_DELETED,
    EventType.MANIFEST_AGENT_CREATED,
    EventType.MANIFEST_AGENT_UPDATED,
    EventType.MANIFEST_AGENT_DELETED,
    EventType.ROOM_CREATED,
    EventType.ROOM_UPDATED,
    EventType.ROOM_ARCHIVED,
    EventType.ROOM_RESTORED,
    EventType.ROOM_DELETED,
    EventType.ROOM_MEMBER_JOINED,
    EventType.ROOM_MEMBER_LEFT,
    EventType.ROOM_LOG_APPENDED,
    EventType.PROJECT_CREATED,
    EventType.PROJECT_UPDATED,
    EventType.PROJECT_ARCHIVED,
    EventType.PROJECT_RESTORED,
    EventType.PROJECT_PAUSED,
    EventType.PROJECT_RESUMED,
    EventType.PROJECT_STOPPED,
    EventType.PROJECT_RECOVERY_SET,
    EventType.PROJECT_AGENT_ADDED,
    EventType.PROJECT_AGENT_REMOVED,
    EventType.PROJECT_DELETED,
    EventType.SESSION_CREATED,
    EventType.SESSION_ACTIVATED,
    EventType.SESSION_ARCHIVED,
    EventType.SESSION_DELETED,
    EventType.SESSION_LIST_RESULT,
    EventType.BRANCH_CREATED,
    EventType.BRANCH_ACTIVATED,
    EventType.BRANCH_ARCHIVED,
    EventType.BRANCH_DELETED,
    EventType.BRANCH_LIST_RESULT,
    ...taskEventTypes,
  ] as const;

  async function syncProject(projectId: string) {
    if (!projects.isProjectOperable(projectId)) return;
    await Promise.all([
      client.listAgents(projectId).catch(() => undefined),
      client.listRooms(projectId, "active").catch(() => undefined),
    ]);
  }

  /** 每 Agent 单调递增同步 generation；新同步轮次使旧结果逻辑过期。 */
  const syncGenerations = new Map<string, number>();
  /** in-flight 串行化尾指针：resource key → 最近一次排队中的请求。断线时清空。 */
  const inFlight = new Map<string, Promise<unknown>>();

  function nextGeneration(agentTarget: AgentTarget): number {
    const key = agentKey(agentTarget);
    const next = (syncGenerations.get(key) ?? 0) + 1;
    syncGenerations.set(key, next);
    return next;
  }

  function isStale(agentTarget: AgentTarget, generation: number): boolean {
    return (syncGenerations.get(agentKey(agentTarget)) ?? 0) !== generation;
  }

  /**
   * 同键请求串行化（S7 P1-1）：命中在途请求时等待其完成后再发起自己的新请求，
   * 保证每个调用方消费的响应都发起于自身 baseline 捕获之后（"响应 ≥ baseline"
   * 不变式）；同时在任意时刻同键至多只有一个底层请求在途。
   */
  function dedupe<T>(key: string, run: () => Promise<T>): Promise<T> {
    const tail = inFlight.get(key);
    const next = (tail ? tail.catch(() => undefined) : Promise.resolve()).then(run);
    inFlight.set(key, next);
    next
      .finally(() => {
        if (inFlight.get(key) === next) inFlight.delete(key);
      })
      .catch(() => undefined);
    return next;
  }

  const onDisconnectedForSync = () => inFlight.clear();
  client.onDisconnected(onDisconnectedForSync);

  async function syncAgentActionState(agentList: AgentTarget[]) {
    await Promise.all(
      agentList.map(async (agentTarget) => {
        const generation = nextGeneration(agentTarget);
        const sessionBaseline = sessions.captureBaseline(agentTarget);
        try {
          // 用量恢复读通量：与三层链并行拉取（权威在 Core，事件只做实时投影）
          void dedupe(`usage:${agentKey(agentTarget)}`, () =>
            client.getAgentInfo(agentTarget),
          ).catch(() => undefined);
          const sessionResult = await dedupe(`sessions:${agentKey(agentTarget)}`, () =>
            client.listSessions(agentTarget),
          );
          if (isStale(agentTarget, generation)) return;
          const sessionData = sessionResult.data as Record<string, unknown> | null;
          if (!sessionResult.success || !Array.isArray(sessionData?.sessions)) return;
          const oldSessions = sessions.sessionsForAgent(agentTarget);
          const sessionList = sessionData.sessions as SessionInfoPayload[];
          sessions.replaceAgentSessions(agentTarget, sessionList, {
            explicitActiveSessionId: stringField(sessionData.active_session_id),
            baseline: sessionBaseline,
          });
          const sessionIds = new Set(sessionList.map((session) => session.session_id));
          for (const stale of oldSessions) {
            if (sessionIds.has(stale.target.sessionId)) continue;
            branches.clearSession(stale.target);
            chains.clearSession(stale.target);
          }

          await Promise.all(
            sessionList.map(async (sessionInfo) => {
              const sessionTarget: SessionTarget = {
                ...agentTarget,
                sessionId: sessionInfo.session_id,
              };
              const branchBaseline = branches.captureBaseline(sessionTarget);
              const branchResult = await dedupe(`branches:${sessionKey(sessionTarget)}`, () =>
                client.listBranches(sessionTarget),
              );
              if (isStale(agentTarget, generation)) return;
              const branchData = branchResult.data as Record<string, unknown> | null;
              if (!branchResult.success || !Array.isArray(branchData?.branches)) return;
              const oldBranches = branches.branchesForSession(sessionTarget);
              const branchList = branchData.branches as BranchInfoPayload[];
              branches.replaceSessionBranches(sessionTarget, branchList, {
                explicitActiveBranchId:
                  stringField(branchData.active_branch_id) ?? sessionInfo.active_branch_id,
                baseline: branchBaseline,
              });
              const branchIds = new Set(branchList.map((branch) => branch.branch_id));
              for (const stale of oldBranches) {
                if (!branchIds.has(stale.target.branchId)) chains.clearChain(stale.target);
              }

              await Promise.all(
                branchList.map(async (branchInfo) => {
                  const target: ChainTarget = {
                    ...sessionTarget,
                    branchId: branchInfo.branch_id,
                  };
                  const history = await dedupe(`history:${chainKey(target)}`, () =>
                    client.getChainHistory(target),
                  );
                  if (isStale(agentTarget, generation)) return;
                  const historyData = history.data as Record<string, unknown> | null;
                  if (!history.success || !Array.isArray(historyData?.nodes)) return;
                  // history 只合并不可变 node；不覆盖 Session/Branch 快照，
                  // 也不触碰 active 指针（S7 P1-2：active 唯一写路径是
                  // session_list 回执的 revision 门 + session_activated 事件）。
                  chains.mergeHistory(target, historyData.nodes as ActionNode[]);
                }),
              );
            }),
          );
        } catch {
          // One Agent read failure does not invalidate already projected state.
        }
      }),
    );
  }

  return () => {
    for (const eventType of eventTypes) client.off(eventType);
    client.off(SystemType.COMMAND_RESULT);
    client.clearLifecycleCallbacks();
    batcher.cancel();
    inFlight.clear();
    syncGenerations.clear();
  };
}
