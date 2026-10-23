import {
  type AbilityDefinitionPayload,
  type AgentConfigPayload,
  ClientType,
  type CommandResultPayload,
  CommandType,
  generateRequestId,
  type RoomSubjectType,
  ServerClient,
  type ServerMessage,
  type SubscribePayload,
  type TaskClaimListPayload,
  type TaskDumpPayload,
  type TaskPriority,
  type TsHalfReport,
} from "@ghrah/protocol";
import type { AgentTarget, ChainTarget, SessionTarget } from "./scope.js";

export type { AgentTarget, ChainTarget, SessionTarget } from "./scope.js";

export interface CreateProjectOptions {
  description?: string;
  manifestRef?: string | null;
  projectRootLocator?: string;
  writableWorkspaces?: Array<{
    locator: string;
    name?: string;
    role?: string | null;
    defaultForAgents?: boolean;
  }>;
}

export interface RoomLifecycleTarget {
  roomId: string;
  expectedVersion: number;
}

export interface ProjectLifecycleTarget {
  projectId: string;
  expectedVersion: number;
}

export interface ProjectDeleteTarget extends ProjectLifecycleTarget {
  cascadeRooms?: boolean;
}

export interface ListProjectsOptions {
  status?: string | null;
  archived?: boolean | null;
}

export interface ObserverRequestContext {
  command: string;
  payload: Readonly<Record<string, unknown>>;
}

export class ObserverClient extends ServerClient {
  private readonly _requestContexts = new Map<string, ObserverRequestContext>();

  override async request(message: ServerMessage, timeout = 30_000): Promise<CommandResultPayload> {
    const requestId = message.request_id ?? generateRequestId();
    message.request_id = requestId;
    this._requestContexts.set(requestId, {
      command: message.type,
      payload: { ...message.payload },
    });
    try {
      return await super.request(message, timeout);
    } finally {
      this._requestContexts.delete(requestId);
    }
  }

  /** Return local request scope while its command-result is being dispatched. */
  getRequestContext(requestId: string): ObserverRequestContext | undefined {
    return this._requestContexts.get(requestId);
  }

  async subscribe(agentNames?: string[] | null, eventTypes?: string[] | null): Promise<void> {
    const payload: SubscribePayload = {};
    if (agentNames != null) payload.agent_names = agentNames;
    if (eventTypes != null) payload.event_types = eventTypes;

    this._subscriptions.push(payload);

    const msg: ServerMessage = {
      type: CommandType.SUBSCRIBE,
      payload: payload as Record<string, unknown>,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    await this.send(msg);
  }

  async unsubscribe(agentNames?: string[] | null, eventTypes?: string[] | null): Promise<void> {
    const payload: Record<string, unknown> = {};
    if (agentNames != null) payload["agent_names"] = agentNames;
    if (eventTypes != null) payload["event_types"] = eventTypes;

    this._subscriptions = this._subscriptions.filter(
      (s) =>
        !(
          (agentNames == null || arraysEqual(s.agent_names, agentNames)) &&
          (eventTypes == null || arraysEqual(s.event_types, eventTypes))
        ),
    );

    const msg: ServerMessage = {
      type: CommandType.UNSUBSCRIBE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    await this.send(msg);
  }

  async spawnAgent(
    projectId: string,
    config: AgentConfigPayload,
    abilities?: AbilityDefinitionPayload[] | null,
    manifestRef?: string | null,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { project_id: projectId, config };
    if (abilities != null) payload["abilities"] = abilities;
    if (manifestRef != null) payload["manifest_ref"] = manifestRef;

    const msg: ServerMessage = {
      type: CommandType.SPAWN_AGENT,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async terminateAgent(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.TERMINATE_AGENT,
      payload: {
        project_id: target.projectId,
        agent_id: target.agentId,
        name: target.agentName,
      },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async agentCompactContext(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.AGENT_COMPACT_CONTEXT,
      payload: agentTargetPayload(target),
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  /** agent_reset：wire 载荷恰 {project_id, agent_id}（稳定寻址，无 agent_name）。 */
  async agentReset(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.AGENT_RESET,
      payload: {
        project_id: target.projectId,
        agent_id: target.agentId,
      },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async sendMessage(
    target: AgentTarget,
    content: string,
    sender = "user",
    options: { timeout?: number | null; metadata?: Record<string, unknown> | null } = {},
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {
      project_id: target.projectId,
      agent_id: target.agentId,
      target: target.agentName,
      content,
      sender,
    };
    if (options.timeout != null) payload.timeout = options.timeout;
    if (options.metadata != null) payload.metadata = options.metadata;
    const msg: ServerMessage = {
      type: CommandType.SEND_MESSAGE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 60_000);
  }

  /** 在源 agent 的原子执行上下文中调用 send_message，正文和投递由 MM 持久化。 */
  async sendAgentMessage(
    source: AgentTarget,
    target: AgentTarget,
    content: string,
    fireAndForget: boolean,
  ): Promise<CommandResultPayload> {
    if (source.projectId !== target.projectId) throw new Error("Cannot send across Projects");
    if (!source.agentId || !target.agentId || source.agentId === target.agentId) {
      throw new Error("Choose two distinct agents with stable IDs");
    }
    if (!content.trim()) throw new Error("Message content is required");
    const result = await this.sendMessage(source, content, "user", {
      metadata: {
        execute_ability: {
          name: "send_message",
          args: { target: target.agentId, content, fire_and_forget: fireAndForget },
          call_id: generateRequestId(),
        },
      },
    });
    if (!result.success) return result;
    // 外层回执成功仅代表命令完成，仍需检查能力的真实结果。
    const action = JSON.parse(String((result.data as { content?: unknown } | null)?.content)) as {
      outcome: string;
      data: Record<string, unknown>;
    };
    if (
      !action ||
      typeof action.data !== "object" ||
      action.data === null ||
      !["success", "failure"].includes(action.outcome)
    ) {
      throw new Error("Invalid send_message ability result");
    }
    return {
      ...result,
      success: action.outcome === "success",
      data: action.data,
      error: action.outcome === "success" ? null : String(action.data.error ?? "Send failed"),
    };
  }

  async broadcastMessage(
    projectId: string,
    content: string,
    sender = "user",
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.BROADCAST_MESSAGE,
      payload: { project_id: projectId, content, sender },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listAgents(projectId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.LIST_AGENTS,
      payload: { project_id: projectId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async healthCheck(): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.HEALTH_CHECK,
      payload: {},
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async sendHitlResponse(
    promiseId: string,
    approved: boolean,
    reason?: string,
  ): Promise<CommandResultPayload | null> {
    const payload: Record<string, unknown> = { promise_id: promiseId, approved };
    if (reason != null) payload["reason"] = reason;

    const msg: ServerMessage = {
      type: CommandType.HITL_RESPONSE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    // request 而非 fire-and-forget：handler 报错（promise 过期等）需回执感知
    return this.request(msg, 10_000);
  }

  async getAgentInfo(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.GET_AGENT_INFO,
      payload: {
        project_id: target.projectId,
        agent_id: target.agentId,
        name: target.agentName,
      },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getChainHistory(target: ChainTarget, limit?: number): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {
      ...agentTargetPayload(target),
      session_id: target.sessionId,
      branch_id: target.branchId,
    };
    if (limit != null) payload["limit"] = limit;

    const msg: ServerMessage = {
      type: CommandType.GET_CHAIN_HISTORY,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listSessions(target: AgentTarget): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.SESSION_LIST, target, {});
  }

  async createSession(
    target: AgentTarget,
    options: {
      name?: string;
      originSessionId?: string;
      originNodeId?: string;
      systemPrompt?: string;
      metadata?: Record<string, unknown>;
    } = {},
  ): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.SESSION_CREATE, target, {
      name: options.name,
      origin_session_id: options.originSessionId,
      origin_node_id: options.originNodeId,
      system_prompt: options.systemPrompt,
      metadata: options.metadata ?? {},
    });
  }

  async activateSession(target: SessionTarget): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.SESSION_ACTIVATE, target, {
      session_id: target.sessionId,
    });
  }

  async archiveSession(target: SessionTarget): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.SESSION_ARCHIVE, target, {
      session_id: target.sessionId,
    });
  }

  async deleteSession(target: SessionTarget): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.SESSION_DELETE, target, {
      session_id: target.sessionId,
    });
  }

  async listBranches(target: SessionTarget): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.BRANCH_LIST, target, {
      session_id: target.sessionId,
    });
  }

  async createBranch(
    target: SessionTarget,
    name: string,
    options: {
      fromNodeId?: string;
      parentBranchId?: string;
      metadata?: Record<string, unknown>;
    } = {},
  ): Promise<CommandResultPayload> {
    return this._agentContextRequest(CommandType.BRANCH_CREATE, target, {
      session_id: target.sessionId,
      name,
      from_node_id: options.fromNodeId,
      parent_branch_id: options.parentBranchId,
      metadata: options.metadata ?? {},
    });
  }

  async activateBranch(target: ChainTarget): Promise<CommandResultPayload> {
    return this._branchLifecycleRequest(CommandType.BRANCH_ACTIVATE, target);
  }

  async archiveBranch(target: ChainTarget): Promise<CommandResultPayload> {
    return this._branchLifecycleRequest(CommandType.BRANCH_ARCHIVE, target);
  }

  async deleteBranch(target: ChainTarget): Promise<CommandResultPayload> {
    return this._branchLifecycleRequest(CommandType.BRANCH_DELETE, target);
  }

  private _branchLifecycleRequest(
    command: CommandType,
    target: ChainTarget,
  ): Promise<CommandResultPayload> {
    return this._agentContextRequest(command, target, {
      session_id: target.sessionId,
      branch_id: target.branchId,
    });
  }

  private _agentContextRequest(
    command: CommandType,
    target: AgentTarget,
    extra: Record<string, unknown>,
  ): Promise<CommandResultPayload> {
    return this.request(
      {
        type: command,
        payload: {
          ...agentTargetPayload(target),
          ...extra,
        },
        request_id: generateRequestId(),
        client_type: ClientType.OBSERVER,
      },
      30_000,
    );
  }

  async initCluster(
    clusterId: string,
    config?: Record<string, unknown>,
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.INIT_CLUSTER,
      payload: { cluster_id: clusterId, config: config ?? {} },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async shutdownCluster(clusterId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.SHUTDOWN_CLUSTER,
      payload: { cluster_id: clusterId, config: {} },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async clusterStatus(clusterId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.CLUSTER_STATUS,
      payload: { cluster_id: clusterId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listClusters(): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.LIST_CLUSTERS,
      payload: {},
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async createWorkspace(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.CREATE_WORKSPACE,
      payload: agentTargetPayload(target),
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async destroyWorkspace(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.DESTROY_WORKSPACE,
      payload: agentTargetPayload(target),
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async workspaceStatus(target: AgentTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.WORKSPACE_STATUS,
      payload: agentTargetPayload(target),
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── Persist 操作 ──

  async persistSaveNode(
    key: string,
    data: Record<string, unknown>,
    namespace = "default",
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PERSIST_SAVE_NODE,
      payload: { key, data, namespace },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async persistLoadNode(key: string, namespace = "default"): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PERSIST_LOAD_NODE,
      payload: { key, namespace },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async persistDeleteChain(key: string, namespace = "default"): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PERSIST_DELETE_CHAIN,
      payload: { key, namespace },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async persistListAgents(namespace = "default", prefix?: string): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { namespace };
    if (prefix != null) payload["prefix"] = prefix;

    const msg: ServerMessage = {
      type: CommandType.PERSIST_LIST_AGENTS,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── 能力管理 + 委托 ──

  async registerAbility(
    target: AgentTarget,
    ability: AbilityDefinitionPayload,
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.REGISTER_ABILITY,
      payload: { ...agentTargetPayload(target), ability },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async unregisterAbility(target: AgentTarget, abilityName: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.UNREGISTER_ABILITY,
      payload: { ...agentTargetPayload(target), ability_name: abilityName },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async delegate(
    fromAgent: AgentTarget,
    toAgent: AgentTarget,
    content: string,
    timeout?: number | null,
  ): Promise<CommandResultPayload> {
    if (fromAgent.projectId !== toAgent.projectId) {
      throw new Error("Cannot delegate across Projects");
    }
    const payload: Record<string, unknown> = {
      project_id: fromAgent.projectId,
      from_agent_id: fromAgent.agentId,
      to_agent_id: toAgent.agentId,
      from_agent: fromAgent.agentName,
      to_agent: toAgent.agentName,
      content,
    };
    if (timeout != null) payload.timeout = timeout;
    const msg: ServerMessage = {
      type: CommandType.DELEGATE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── Manifest CRUD ──

  async listManifestAbilities(namespace?: string | null): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (namespace != null) payload["namespace"] = namespace;

    const msg: ServerMessage = {
      type: CommandType.MANIFEST_LIST_ABILITIES,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getAbility(fullName: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.MANIFEST_GET_ABILITY,
      payload: { full_name: fullName },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async putAbility(
    fullName: string,
    content: string,
    overwrite?: boolean,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { full_name: fullName, content };
    if (overwrite != null) payload["overwrite"] = overwrite;

    const msg: ServerMessage = {
      type: CommandType.MANIFEST_PUT_ABILITY,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async deleteAbility(fullName: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.MANIFEST_DELETE_ABILITY,
      payload: { full_name: fullName },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listManifestAgents(namespace?: string | null): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (namespace != null) payload["namespace"] = namespace;

    const msg: ServerMessage = {
      type: CommandType.MANIFEST_LIST_AGENTS,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getAgent(fullName: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.MANIFEST_GET_AGENT,
      payload: { full_name: fullName },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async putAgent(
    fullName: string,
    content: string,
    overwrite?: boolean,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { full_name: fullName, content };
    if (overwrite != null) payload["overwrite"] = overwrite;

    const msg: ServerMessage = {
      type: CommandType.MANIFEST_PUT_AGENT,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async deleteAgent(fullName: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.MANIFEST_DELETE_AGENT,
      payload: { full_name: fullName },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async resolveAgent(
    agentFullName: string,
    runtimeName?: string | null,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { agent_full_name: agentFullName };
    if (runtimeName != null) payload["runtime_name"] = runtimeName;

    const msg: ServerMessage = {
      type: CommandType.MANIFEST_RESOLVE_AGENT,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async validateManifest(content: string, manifestType: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.MANIFEST_VALIDATE,
      payload: { content, manifest_type: manifestType },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── Room ──

  async createRoom(projectId: string, name: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_CREATE,
      payload: { project_id: projectId, name },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listRooms(
    projectId?: string | null,
    status?: string | null,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (projectId != null) payload["project_id"] = projectId;
    if (status != null) payload["status"] = status;

    const msg: ServerMessage = {
      type: CommandType.ROOM_LIST,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getRoom(roomId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_GET,
      payload: { room_id: roomId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async updateRoom(
    roomId: string,
    name?: string | null,
    expectedVersion?: number | null,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { room_id: roomId };
    if (name != null) payload["name"] = name;
    if (expectedVersion != null) payload["expected_version"] = expectedVersion;

    const msg: ServerMessage = {
      type: CommandType.ROOM_UPDATE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async archiveRoom(target: RoomLifecycleTarget): Promise<CommandResultPayload> {
    return this._roomLifecycle(CommandType.ROOM_ARCHIVE, target);
  }

  async restoreRoom(target: RoomLifecycleTarget): Promise<CommandResultPayload> {
    return this._roomLifecycle(CommandType.ROOM_RESTORE, target);
  }

  async deleteRoom(target: RoomLifecycleTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_DELETE,
      payload: roomLifecyclePayload(target),
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  private _roomLifecycle(
    command: CommandType,
    target: RoomLifecycleTarget,
  ): Promise<CommandResultPayload> {
    return this.request(
      {
        type: command,
        payload: roomLifecyclePayload(target),
        request_id: generateRequestId(),
        client_type: ClientType.OBSERVER,
      },
      30_000,
    );
  }

  async joinRoom(
    roomId: string,
    subject: string,
    subjectType: RoomSubjectType,
    subjectName?: string,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {
      room_id: roomId,
      subject,
      subject_type: subjectType,
    };
    if (subjectName != null) payload.subject_name = subjectName;
    const msg: ServerMessage = {
      type: CommandType.ROOM_JOIN,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async leaveRoom(roomId: string, subject: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_LEAVE,
      payload: { room_id: roomId, subject },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getRoomMembers(roomId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_GET_MEMBERS,
      payload: { room_id: roomId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getRoomLog(
    roomId: string,
    sinceSeq?: number | null,
    limit?: number,
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { room_id: roomId };
    if (sinceSeq != null) payload["since_seq"] = sinceSeq;
    if (limit != null) payload["limit"] = limit;

    const msg: ServerMessage = {
      type: CommandType.ROOM_GET_LOG,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async roomSend(
    roomId: string,
    data: Record<string, unknown>,
    author = "user",
    authorType: RoomSubjectType = "human",
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.ROOM_SEND,
      payload: { room_id: roomId, author, author_type: authorType, data },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── Project ──

  async createProject(name: string, options: CreateProjectOptions): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { name };
    if (options.description) payload["description"] = options.description;
    if (options.manifestRef != null) payload["manifest_ref"] = options.manifestRef;
    if (options.projectRootLocator) {
      payload["project_root_locator"] = options.projectRootLocator;
    }
    if (options.writableWorkspaces !== undefined) {
      payload["writable_workspaces"] = options.writableWorkspaces.map((workspace) => ({
        locator: workspace.locator,
        ...(workspace.name ? { name: workspace.name } : {}),
        ...(workspace.role !== undefined ? { role: workspace.role } : {}),
        ...(workspace.defaultForAgents !== undefined
          ? { default_for_agents: workspace.defaultForAgents }
          : {}),
      }));
    }
    const msg: ServerMessage = {
      type: CommandType.PROJECT_CREATE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listProjects(options: ListProjectsOptions = {}): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (options.status !== undefined) payload.status = options.status;
    if (options.archived !== undefined) payload.archived = options.archived;

    const msg: ServerMessage = {
      type: CommandType.PROJECT_LIST,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getProject(projectId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PROJECT_GET,
      payload: { project_id: projectId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async updateProject(
    projectId: string,
    fields: {
      name?: string | null;
      description?: string | null;
      manifestRef?: string | null;
      expectedVersion?: number | null;
    },
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { project_id: projectId };
    if (fields.name != null) payload["name"] = fields.name;
    if (fields.description != null) payload["description"] = fields.description;
    if (fields.manifestRef != null) payload["manifest_ref"] = fields.manifestRef;
    if (fields.expectedVersion != null) payload["expected_version"] = fields.expectedVersion;

    const msg: ServerMessage = {
      type: CommandType.PROJECT_UPDATE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async deleteProject(target: ProjectDeleteTarget): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PROJECT_DELETE,
      payload: {
        ...projectLifecyclePayload(target),
        cascade_rooms: target.cascadeRooms ?? false,
      },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async archiveProject(target: ProjectLifecycleTarget): Promise<CommandResultPayload> {
    return this._projectLifecycle(CommandType.PROJECT_ARCHIVE, target);
  }

  async restoreProject(target: ProjectLifecycleTarget): Promise<CommandResultPayload> {
    return this._projectLifecycle(CommandType.PROJECT_RESTORE, target);
  }

  private _projectLifecycle(
    command: CommandType,
    target: ProjectLifecycleTarget,
  ): Promise<CommandResultPayload> {
    return this.request(
      {
        type: command,
        payload: projectLifecyclePayload(target),
        request_id: generateRequestId(),
        client_type: ClientType.OBSERVER,
      },
      30_000,
    );
  }

  // ── Task ──

  async createTask(
    title: string,
    projectId: string,
    opts?: {
      description?: string;
      agentId?: string | null;
      agentName?: string | null;
      priority?: TaskPriority;
      parentId?: string | null;
    },
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = { title, project_id: projectId };
    if (opts?.description != null) payload["description"] = opts.description;
    if (opts?.agentId != null) payload["agent_id"] = opts.agentId;
    if (opts?.agentName != null) payload["agent_name"] = opts.agentName;
    if (opts?.priority != null) payload["priority"] = opts.priority;
    if (opts?.parentId != null) payload["parent_id"] = opts.parentId;

    const msg: ServerMessage = {
      type: CommandType.TASK_CREATE,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async listTasks(filter?: {
    projectId?: string | null;
    agentId?: string | null;
    agentName?: string | null;
    status?: string | string[] | null;
    limit?: number;
  }): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (filter?.projectId != null) payload["project_id"] = filter.projectId;
    if (filter?.agentId != null) payload["agent_id"] = filter.agentId;
    if (filter?.agentName != null) payload["agent_name"] = filter.agentName;
    if (filter?.status != null) payload["status"] = filter.status;
    if (filter?.limit != null) payload["limit"] = filter.limit;

    const msg: ServerMessage = {
      type: CommandType.TASK_LIST,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  async getTask(taskId: string): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.TASK_GET,
      payload: { task_id: taskId },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── Plugin / Task 归因 ──

  /** 插件协商：上报宿主已装配的 TS 半清单，取回六字段协商结果。 */
  async pluginNegotiate(enabledTs: TsHalfReport[]): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.PLUGIN_NEGOTIATE,
      payload: { enabled_ts: enabledTs },
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  /** 任务归因快照：从权威读通量重建 claims/evidence 投影。 */
  async taskDump(
    payload: Partial<Pick<TaskDumpPayload, "project_id" | "include_deleted" | "limit">> = {},
  ): Promise<CommandResultPayload> {
    const msg: ServerMessage = {
      type: CommandType.TASK_DUMP,
      payload: payload as Record<string, unknown>,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  /** 任务归因查询：按 task/claimant/state 过滤 claims（含内嵌 evidence）。 */
  async taskListClaims(
    filter: Partial<Omit<TaskClaimListPayload, "limit">> & { limit?: number } = {},
  ): Promise<CommandResultPayload> {
    const payload: Record<string, unknown> = {};
    if (filter.task_id != null) payload["task_id"] = filter.task_id;
    if (filter.claimant_id != null) payload["claimant_id"] = filter.claimant_id;
    if (filter.state != null) payload["state"] = filter.state;
    if (filter.limit != null) payload["limit"] = filter.limit;
    const msg: ServerMessage = {
      type: CommandType.TASK_LIST_CLAIMS,
      payload,
      request_id: generateRequestId(),
      client_type: ClientType.OBSERVER,
    };
    return this.request(msg, 30_000);
  }

  // ── 内部方法 ──

  protected async _syncInitialState(): Promise<void> {
    let projectIds: string[] = [];
    try {
      const result = await this.listProjects({ archived: false });
      const projects = (result.data as { projects?: Array<{ project_id?: unknown }> } | null)
        ?.projects;
      projectIds = (projects ?? [])
        .map((project) => project.project_id)
        .filter((projectId): projectId is string => typeof projectId === "string");
    } catch {
      // 静默忽略初始同步失败
    }
    try {
      await this.listManifestAgents();
    } catch {
      // 静默忽略初始同步失败
    }
    await Promise.all(
      projectIds.flatMap((projectId) => [
        this.listAgents(projectId).catch(() => undefined),
        this.listRooms(projectId, "active").catch(() => undefined),
      ]),
    );
  }
}

function agentTargetPayload(target: AgentTarget): Record<string, unknown> {
  return {
    project_id: target.projectId,
    agent_id: target.agentId,
    agent_name: target.agentName,
  };
}

function roomLifecyclePayload(target: RoomLifecycleTarget): Record<string, unknown> {
  return { room_id: target.roomId, expected_version: target.expectedVersion };
}

function projectLifecyclePayload(target: ProjectLifecycleTarget): Record<string, unknown> {
  return { project_id: target.projectId, expected_version: target.expectedVersion };
}

function arraysEqual(a: string[] | null | undefined, b: string[]): boolean {
  if (a == null) return false;
  if (a.length !== b.length) return false;
  const sortedA = [...a].sort();
  const sortedB = [...b].sort();
  return sortedA.every((v, i) => v === sortedB[i]);
}
