import type { CommandResultPayload, ServerMessage } from "@ghrah/protocol";
import { CommandType, EventType, SystemType, type WebSocketLike } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { connectStores } from "./bind.js";
import { ObserverClient } from "./client.js";
import { createFrameBatcher, createSyncBatcher, type FrameBatcher } from "./frame-batcher.js";
import type { ChainTarget } from "./scope.js";
import { useActionChainsStore } from "./stores/action-chains.js";
import { useAgentsStore } from "./stores/agents.js";
import { useBranchesStore } from "./stores/branches.js";
import { useChangesStore } from "./stores/changes.js";
import { useChatStore } from "./stores/chat.js";
import { useConnectionStore } from "./stores/connection.js";
import { useContextUsageStore } from "./stores/context-usage.js";
import { useHitlStore } from "./stores/hitl.js";
import { useManifestsStore } from "./stores/manifests.js";
import { useProjectsStore } from "./stores/projects.js";
import { useRoomsStore } from "./stores/rooms.js";
import { useSessionsStore } from "./stores/sessions.js";
import { usePluginsStore } from "./stores/plugins.js";
import { useTasksStore } from "./stores/tasks.js";

const WS_OPEN = 1;

interface MockWebSocket extends WebSocketLike {
  sent: string[];
}

interface ClientInternals {
  _dispatch: (msgType: string, message: ServerMessage) => void;
  _notifyReconnecting: () => void;
  _notifyReconnected: () => void;
  _notifyDisconnected: () => void;
}

function createMockWs(): MockWebSocket {
  const ws: MockWebSocket = {
    readyState: WS_OPEN,
    sent: [],
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    send(_data: string) {},
    close() {},
  };
  return ws;
}

function makeMsg(
  type: string,
  payload: Record<string, unknown>,
  extra?: Partial<ServerMessage>,
): ServerMessage {
  if (type === EventType.ACTION_CHAIN_UPDATED) {
    const agentName = String(payload.agent_name ?? "agent-1");
    payload = {
      project_id: "p1",
      agent_id: `id-${agentName}`,
      cluster_id: "c1",
      ...payload,
      node: {
        session_id: "s1",
        created_on_branch_id: "b1",
        ...(payload.node as Record<string, unknown> | undefined),
      },
    };
  }
  if (type === EventType.HITL_REQUEST || type === EventType.HITL_RESOLVED) {
    payload = { project_id: "p1", agent_id: "a1", cluster_id: "c1", ...payload };
  }
  if (type === SystemType.COMMAND_RESULT && payload.original_command === CommandType.LIST_AGENTS) {
    const data = payload.data as { agents?: Array<Record<string, unknown>> } | undefined;
    if (Array.isArray(data?.agents)) {
      data.agents = data.agents.map((agent) => ({
        project_id: "p1",
        agent_id:
          agent.agent_id ??
          (agent.config as Record<string, unknown> | undefined)?.agent_id ??
          `id-${String(agent.name)}`,
        runtime_state: "running",
        ...agent,
      }));
    }
  }
  return { type, payload, ...extra };
}

function chainTarget(agentName: string, overrides: Partial<ChainTarget> = {}): ChainTarget {
  return {
    projectId: "p1",
    agentId: `id-${agentName}`,
    agentName,
    sessionId: "s1",
    branchId: "b1",
    ...overrides,
  };
}

function internals(c: ObserverClient): ClientInternals {
  return c as unknown as ClientInternals;
}

const DEFAULT_CONFIG = {
  name: "",
  description: "",
  system_prompt: "",
  max_iterations: 10,
  endpoint_name: null,
} as const;

describe("connectStores", () => {
  let client: ObserverClient;
  let mockWs: MockWebSocket;
  let disconnect: () => void;

  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    mockWs = createMockWs();
    client = new ObserverClient("ws://localhost:4111/ws", "observer", {
      wsFactory: () => mockWs,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (skipSyncInitialState) {
      skipSyncInitialState.mockRestore();
      skipSyncInitialState = null;
    }
    if (disconnect) disconnect();
    if (client.connected) {
      client.disconnect();
    }
  });

  let skipSyncInitialState: ReturnType<typeof vi.spyOn<any, any>> | null = null;

  async function connectClient(
    opts: { skipSync?: boolean; batcher?: FrameBatcher; tsHalfProvider?: () => unknown[] } = {},
  ) {
    const { skipSync = true } = opts;
    if (skipSync) {
      skipSyncInitialState = vi
        .spyOn(client as any, "_syncInitialState")
        .mockResolvedValue(undefined);
    }
    disconnect = connectStores(client, {
      batcher: opts.batcher ?? createSyncBatcher(),
      ...(opts.tsHalfProvider ? { tsHalfProvider: opts.tsHalfProvider as () => never[] } : {}),
    });
    const connectPromise = client.connect();
    mockWs.onopen!();
    await connectPromise;
  }

  describe("connection state", () => {
    it("sets connection to connected on connect", async () => {
      await connectClient();
      expect(useConnectionStore().state).toBe("connected");
    });

    it("sets connection to reconnecting on reconnect", () => {
      const store = useConnectionStore();
      disconnect = connectStores(client, { batcher: createSyncBatcher() });
      client.onReconnecting(() => store.setReconnecting());
      internals(client)._notifyReconnecting();
      expect(store.state).toBe("reconnecting");
    });

    it("sets connection to disconnected", () => {
      const store = useConnectionStore();
      client.onDisconnected(() => store.setDisconnected());
      internals(client)._notifyDisconnected();
      expect(store.state).toBe("disconnected");
    });
  });

  describe("plugin negotiation and task attribution sync", () => {
    const tsHalf = [{ plugin_id: "p1", version: "0.1.0", provides: [] as string[] }];

    it("sends plugin_negotiate + task_dump once on connect (A10 连接半边)", async () => {
      const negotiate = vi.spyOn(client, "pluginNegotiate").mockResolvedValue({
        request_id: "neg",
        success: true,
        data: {},
      } as never);
      const dump = vi.spyOn(client, "taskDump").mockResolvedValue({
        request_id: "dump",
        success: true,
        data: { tasks: [], claims: [], evidence: [] },
      } as never);
      await connectClient({ tsHalfProvider: () => tsHalf });
      expect(negotiate).toHaveBeenCalledTimes(1);
      expect(negotiate).toHaveBeenCalledWith(tsHalf);
      expect(dump).toHaveBeenCalledTimes(1);
      expect(dump).toHaveBeenCalledWith({});
    });

    it("re-sends on reconnect", async () => {
      const negotiate = vi.spyOn(client, "pluginNegotiate").mockResolvedValue({
        request_id: "neg",
        success: true,
        data: {},
      } as never);
      vi.spyOn(client, "taskDump").mockResolvedValue({
        request_id: "dump",
        success: true,
        data: { tasks: [], claims: [], evidence: [] },
      } as never);
      await connectClient();
      internals(client)._notifyReconnected();
      expect(negotiate).toHaveBeenCalledTimes(2);
    });

    it("plugin_negotiated event triggers re-negotiation (A10 变更半边)", async () => {
      const negotiate = vi.spyOn(client, "pluginNegotiate").mockResolvedValue({
        request_id: "neg",
        success: true,
        data: {},
      } as never);
      vi.spyOn(client, "taskDump").mockResolvedValue({
        request_id: "dump",
        success: true,
        data: { tasks: [], claims: [], evidence: [] },
      } as never);
      await connectClient();
      internals(client)._dispatch(
        EventType.PLUGIN_NEGOTIATED,
        makeMsg(EventType.PLUGIN_NEGOTIATED, { changed: ["p1"] }),
      );
      expect(negotiate).toHaveBeenCalledTimes(2);
    });

    it("routes PLUGIN_NEGOTIATE result into plugins store", async () => {
      await connectClient();
      const result = {
        matched: [{ plugin_id: "p1", version: "0.1.0", provides: [], instances: [] }],
        python_only: [],
        ts_only: [],
        version_conflicts: [],
        missing_capabilities: [],
        instances: {},
      };
      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "neg",
          success: true,
          original_command: CommandType.PLUGIN_NEGOTIATE,
          data: result,
        }),
      );
      expect(usePluginsStore().matchedById.get("p1")?.version).toBe("0.1.0");
    });

    it("routes TASK_DUMP result into tasks store", async () => {
      await connectClient();
      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "dump",
          success: true,
          original_command: CommandType.TASK_DUMP,
          data: {
            tasks: [{ task_id: "t1", project_id: "p1", title: "x" }],
            claims: [],
            evidence: [],
          },
        }),
      );
      expect(useTasksStore().tasks.get("t1")?.title).toBe("x");
    });

    it("task claim events upsert claims (TASK_DELIVERED)", async () => {
      await connectClient();
      internals(client)._dispatch(
        EventType.TASK_DELIVERED,
        makeMsg(EventType.TASK_DELIVERED, {
          task: { task_id: "t1", project_id: "p1", title: "x" },
          claim: {
            claim_id: "c1",
            task_id: "t1",
            claimant_type: "agent",
            claimant_id: "a1",
            state: "submitted",
            evidence: [],
            checks: [],
          },
        }),
      );
      const tasks = useTasksStore();
      expect(tasks.claims.get("c1")?.state).toBe("submitted");
      expect(tasks.tasks.get("t1")?.title).toBe("x");
    });
  });

  describe("agent events", () => {
    it("adds agent on agent_spawned", async () => {
      await connectClient();
      const store = useAgentsStore();

      internals(client)._dispatch(
        EventType.AGENT_SPAWNED,
        makeMsg(EventType.AGENT_SPAWNED, {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          incarnation_id: "i1",
          recovery_mode: "fresh",
          name: "agent-1",
          config: { ...DEFAULT_CONFIG, name: "agent-1", system_prompt: "test" },
        }),
      );

      expect(store.getAgent({ projectId: "p1", agentId: "a1" })?.status).toBe("active");
    });

    it("marks agent terminated on agent_terminated", async () => {
      await connectClient();
      const store = useAgentsStore();
      const target = chainTarget("agent-1", { agentId: "a1" });
      store.onAgentSpawned({
        name: "agent-1",
        agent_id: "a1",
        project_id: "p1",
        cluster_id: "c1",
        incarnation_id: "i1",
        recovery_mode: "",
        config: { ...DEFAULT_CONFIG, name: "agent-1" },
      });
      useActionChainsStore().setChain(target, [{ id: "n1" } as never]);

      internals(client)._dispatch(
        EventType.AGENT_TERMINATED,
        makeMsg(EventType.AGENT_TERMINATED, {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          incarnation_id: "i1",
          name: "agent-1",
        }),
      );

      expect(store.getAgent({ projectId: "p1", agentId: "a1" })?.status).toBe("terminated");
      expect(useActionChainsStore().getChain(target)).toHaveLength(0);
    });
  });

  describe("room events", () => {
    function makeRoom(roomId: string, overrides: Record<string, unknown> = {}) {
      return {
        room_id: roomId,
        project_id: "p1",
        name: roomId,
        status: "active",
        members: [],
        seq_watermark: 0,
        version: 1,
        created_at: "",
        updated_at: "",
        ...overrides,
      };
    }

    function makeLogEntry(roomId: string, overrides: Record<string, unknown> = {}) {
      return {
        id: `${roomId}-e1`,
        room_id: roomId,
        seq: 1,
        author: "agent-1",
        author_type: "agent",
        timestamp: 1750000000,
        data: { message: "hi" },
        ...overrides,
      };
    }

    it("room_created / room_updated upsert rooms store", async () => {
      await connectClient();
      const store = useRoomsStore();

      internals(client)._dispatch(
        EventType.ROOM_CREATED,
        makeMsg(EventType.ROOM_CREATED, { room: makeRoom("r1") }),
      );
      expect(store.rooms.has("r1")).toBe(true);

      internals(client)._dispatch(
        EventType.ROOM_UPDATED,
        makeMsg(EventType.ROOM_UPDATED, { room: makeRoom("r1", { name: "renamed" }) }),
      );
      expect(store.rooms.get("r1")?.name).toBe("renamed");
    });

    it("room_deleted removes room", async () => {
      await connectClient();
      const store = useRoomsStore();
      store.onRoomCreated({ room: makeRoom("r1") as never });

      internals(client)._dispatch(
        EventType.ROOM_DELETED,
        makeMsg(EventType.ROOM_DELETED, { room_id: "r1", project_id: "p1" }),
      );
      expect(store.rooms.has("r1")).toBe(false);
    });

    it("archive clears active Room state and restore accepts an unarchived stopped Project", async () => {
      await connectClient();
      const store = useRoomsStore();
      const chatStore = useChatStore();
      useProjectsStore().setProjectsFromList([
        {
          project_id: "p1",
          name: "p1",
          status: "stopped",
          archived_at: null,
        } as never,
      ]);
      store.onRoomCreated({ room: makeRoom("r1") as never });
      store.setActiveRoom("r1");
      store.setRoomLog("r1", [makeLogEntry("r1") as never]);
      chatStore.addPendingEntry({
        projectId: "p1",
        to: "r1",
        content: "pending",
        agentName: "",
        roomId: "r1",
      });

      internals(client)._dispatch(
        EventType.ROOM_ARCHIVED,
        makeMsg(EventType.ROOM_ARCHIVED, { room: makeRoom("r1", { status: "archived" }) }),
      );

      expect(store.activeRooms.has("r1")).toBe(false);
      expect(store.archivedRooms.has("r1")).toBe(true);
      expect(store.logs.has("r1")).toBe(false);
      expect(store.activeRoomId).toBeNull();
      expect(chatStore.allEntries).toHaveLength(0);

      internals(client)._dispatch(
        EventType.ROOM_RESTORED,
        makeMsg(EventType.ROOM_RESTORED, { room: makeRoom("r1") }),
      );
      expect(store.activeRooms.has("r1")).toBe(true);
      expect(store.archivedRooms.has("r1")).toBe(false);
    });

    it("room_member_joined / room_member_left upsert envelope room", async () => {
      await connectClient();
      const store = useRoomsStore();
      store.onRoomCreated({ room: makeRoom("r1") as never });

      internals(client)._dispatch(
        EventType.ROOM_MEMBER_JOINED,
        makeMsg(EventType.ROOM_MEMBER_JOINED, {
          room: makeRoom("r1", {
            members: [{ subject: "agent-1", subject_type: "agent", joined_at: "" }],
          }),
          member: { subject: "agent-1", subject_type: "agent", joined_at: "" },
        }),
      );
      expect(store.rooms.get("r1")?.members).toHaveLength(1);

      internals(client)._dispatch(
        EventType.ROOM_MEMBER_LEFT,
        makeMsg(EventType.ROOM_MEMBER_LEFT, {
          room: makeRoom("r1", { members: [] }),
          subject: "agent-1",
        }),
      );
      expect(store.rooms.get("r1")?.members).toHaveLength(0);
    });

    it("room_log_appended appends to rooms store and dedups replay", async () => {
      await connectClient();
      const store = useRoomsStore();

      const msg = makeMsg(EventType.ROOM_LOG_APPENDED, { entry: makeLogEntry("r1") });
      internals(client)._dispatch(EventType.ROOM_LOG_APPENDED, msg);
      internals(client)._dispatch(EventType.ROOM_LOG_APPENDED, msg);

      expect(store.logs.get("r1")).toHaveLength(1);
    });

    it("room_log_appended confirms matching human pending in chat store", async () => {
      await connectClient();
      const chatStore = useChatStore();
      useRoomsStore().onRoomCreated({ room: makeRoom("r1") as never });
      chatStore.addPendingEntry({
        projectId: "p1",
        to: "r1",
        content: "hello",
        agentName: "",
        roomId: "r1",
      });
      expect(chatStore.allEntries).toHaveLength(1);

      internals(client)._dispatch(
        EventType.ROOM_LOG_APPENDED,
        makeMsg(EventType.ROOM_LOG_APPENDED, {
          entry: makeLogEntry("r1", {
            author: "user",
            author_type: "human",
            data: { message: "hello" },
          }),
        }),
      );

      expect(chatStore.allEntries).toHaveLength(0);
    });

    it("action_chain_updated no longer feeds chat store", async () => {
      await connectClient();
      const chatStore = useChatStore();

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          agent_name: "agent-1",
          node: {
            id: "node-1",
            agent_name: "agent-1",
            ability_names: ["conversation"],
            messages_delta: [
              { role: "ai", content_blocks: [{ type: "text", text: "Hello" }], metadata: {} },
            ],
          },
        }),
      );

      expect(chatStore.allEntries).toHaveLength(0);
    });
  });

  describe("project events", () => {
    function makeProject(projectId: string, overrides: Record<string, unknown> = {}) {
      return {
        project_id: projectId,
        name: projectId,
        manifest_ref: "",
        cluster_ids: [],
        workspaces: [],
        agents: [],
        task_ids: [],
        status: "active",
        recovery: "resume",
        version: 1,
        created_at: "",
        updated_at: "",
        ...overrides,
      };
    }

    it("project_created / project_paused upsert projects store", async () => {
      await connectClient();
      const store = useProjectsStore();

      internals(client)._dispatch(
        EventType.PROJECT_CREATED,
        makeMsg(EventType.PROJECT_CREATED, { project: makeProject("p1") }),
      );
      expect(store.projects.has("p1")).toBe(true);

      internals(client)._dispatch(
        EventType.PROJECT_PAUSED,
        makeMsg(EventType.PROJECT_PAUSED, { project: makeProject("p1", { status: "paused" }) }),
      );
      expect(store.projects.get("p1")?.status).toBe("paused");
    });

    it("project_agent_added upserts with agent_name envelope", async () => {
      await connectClient();
      const store = useProjectsStore();
      store.onProjectEvent({ project: makeProject("p1") as never });

      internals(client)._dispatch(
        EventType.PROJECT_AGENT_ADDED,
        makeMsg(EventType.PROJECT_AGENT_ADDED, {
          project: makeProject("p1", {
            agents: [
              {
                name: "agent-1",
                cluster_id: "c1",
                manifest_ref: "",
                instance_manifest_path: "",
                system_prompt: "",
                path_grants: [],
              },
            ],
          }),
          agent_name: "agent-1",
        }),
      );
      expect(store.projects.get("p1")?.agents).toHaveLength(1);
    });

    it("project_restored resyncs definitions and action summaries while Agents stay stopped", async () => {
      await connectClient();
      const restoredProject = makeProject("p1", { status: "stopped", archived_at: null });
      const session = {
        project_id: "p1",
        agent_id: "a1",
        cluster_id: "c1",
        agent_name: "same",
        session_id: "s1",
        root_node_id: "root",
        active_branch_id: "b1",
        state: "active",
        system_prompt: "",
        created_at: "",
        metadata: {},
        message_count: 0,
        iteration_count: 0,
      };
      const branch = {
        branch_id: "b1",
        session_id: "s1",
        name: "main",
        head_node_id: "root",
        created_at: "",
        metadata: {},
      };
      vi.spyOn(client, "listAgents").mockImplementation(async () => {
        internals(client)._dispatch(
          SystemType.COMMAND_RESULT,
          makeMsg(SystemType.COMMAND_RESULT, {
            request_id: "restore-agents",
            success: true,
            original_command: CommandType.LIST_AGENTS,
            data: {
              agents: [
                {
                  project_id: "p1",
                  agent_id: "a1",
                  name: "same",
                  runtime_state: "running",
                },
              ],
            },
          }),
        );
        return { request_id: "restore-agents", success: true, data: { agents: [] } } as never;
      });
      const listRooms = vi.spyOn(client, "listRooms").mockResolvedValue({
        request_id: "restore-rooms",
        success: true,
        data: { rooms: [] },
      } as never);
      vi.spyOn(client, "listSessions").mockResolvedValue({
        request_id: "restore-sessions",
        success: true,
        data: { sessions: [session] },
      } as never);
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "restore-branches",
        success: true,
        data: { branches: [branch] },
      } as never);
      const history = vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "restore-history",
        success: true,
        data: { nodes: [], active_session_id: "s1" },
      } as never);

      internals(client)._dispatch(
        EventType.PROJECT_RESTORED,
        makeMsg(EventType.PROJECT_RESTORED, { project: restoredProject }),
      );

      await vi.waitFor(() => expect(history).toHaveBeenCalled());
      expect(listRooms).toHaveBeenCalledWith("p1", "active");
      expect(useAgentsStore().getAgent({ projectId: "p1", agentId: "a1" })?.status).toBe(
        "terminated",
      );
      expect(
        useSessionsStore().sessionsForAgent(chainTarget("same", { agentId: "a1" })),
      ).toHaveLength(1);
      expect(
        useBranchesStore().branchesForSession({
          projectId: "p1",
          agentId: "a1",
          agentName: "same",
          sessionId: "s1",
        }),
      ).toHaveLength(1);
    });

    it("project_deleted clears all p1-derived state without relying on Room events", async () => {
      await connectClient();
      const projectsStore = useProjectsStore();
      const agentsStore = useAgentsStore();
      const roomsStore = useRoomsStore();
      const sessionsStore = useSessionsStore();
      const branchesStore = useBranchesStore();
      const chainsStore = useActionChainsStore();
      const p1 = chainTarget("same", { agentId: "a1" });
      const p2 = { ...p1, projectId: "p2", agentId: "a2" };
      const agentList = (projectId: string, agentId: string) => [
        {
          project_id: projectId,
          agent_id: agentId,
          name: "same",
          runtime_state: "running",
        },
      ];
      projectsStore.setProjectsFromList([makeProject("p1"), makeProject("p2")] as never);
      agentsStore.replaceProjectAgents("p1", agentList("p1", "a1"), {
        status: "active",
        archived_at: null,
      });
      agentsStore.replaceProjectAgents("p2", agentList("p2", "a2"), {
        status: "active",
        archived_at: null,
      });
      const makeRoom = (roomId: string, projectId: string) => ({
        room_id: roomId,
        project_id: projectId,
        name: roomId,
        status: "active" as const,
        members: [],
        seq_watermark: 0,
        version: 1,
        created_at: "",
        updated_at: "",
        archived_at: null,
      });
      roomsStore.replaceProjectRooms("p1", [makeRoom("r1", "p1")], "active");
      roomsStore.replaceProjectRooms("p2", [makeRoom("r2", "p2")], "active");
      sessionsStore.replaceAgentSessions(p1, [
        {
          project_id: "p1",
          agent_id: "a1",
          agent_name: "same",
          session_id: "s1",
          root_node_id: "root",
          active_branch_id: "b1",
        } as never,
      ]);
      branchesStore.replaceSessionBranches(p1, [
        {
          branch_id: "b1",
          session_id: "s1",
          name: "main",
          head_node_id: "root",
          lifecycle: "open",
        } as never,
      ]);
      chainsStore.setChain(p1, [{ id: "n1" } as never]);
      chainsStore.setChain(p2, [{ id: "n2" } as never]);
      useChangesStore().onActionChainNode(p1, {
        id: "n1",
        action_results: [
          {
            ability_name: "write_file",
            action_result: { outcome: "success", data: { file_path: "/p1" } },
          },
        ],
      } as never);
      useHitlStore().onHitlRequest({
        promise_id: "hitl-p1",
        project_id: "p1",
        agent_id: "a1",
        agent_name: "same",
        ability_name: "write_file",
        tool_args: {},
        context: {},
      } as never);
      useChatStore().addPendingEntry({
        projectId: "p1",
        to: "r1",
        content: "pending",
        agentName: "",
        roomId: "r1",
      });
      useTasksStore().setTasksFromList([
        { task_id: "t1", project_id: "p1" } as never,
        { task_id: "t2", project_id: "p2" } as never,
      ]);

      internals(client)._dispatch(
        EventType.PROJECT_DELETED,
        makeMsg(EventType.PROJECT_DELETED, { project: makeProject("p1") }),
      );

      expect(projectsStore.projects.has("p1")).toBe(false);
      expect(agentsStore.agentsForProject("p1")).toHaveLength(0);
      expect(agentsStore.agentsForProject("p2")).toHaveLength(1);
      expect(roomsStore.roomsForProject("p1")).toHaveLength(0);
      expect(roomsStore.roomsForProject("p2")).toHaveLength(1);
      expect(sessionsStore.sessionsForAgent(p1)).toHaveLength(0);
      expect(branchesStore.branchesForSession(p1)).toHaveLength(0);
      expect(chainsStore.getChain(p1)).toHaveLength(0);
      expect(chainsStore.getChain(p2)).toHaveLength(1);
      expect(useChangesStore().changes).toHaveLength(0);
      expect(useHitlStore().requests).toHaveLength(0);
      expect(useChatStore().allEntries).toHaveLength(0);
      expect(useTasksStore().tasks.has("t1")).toBe(false);
      expect(useTasksStore().tasks.has("t2")).toBe(true);
    });

    it("project_deleted removes project", async () => {
      await connectClient();
      const store = useProjectsStore();
      store.onProjectEvent({ project: makeProject("p1") as never });

      internals(client)._dispatch(
        EventType.PROJECT_DELETED,
        makeMsg(EventType.PROJECT_DELETED, { project: makeProject("p1") }),
      );
      expect(store.projects.has("p1")).toBe(false);
    });
  });

  describe("session and branch events", () => {
    const sessionInfo = {
      project_id: "p1",
      agent_id: "a1",
      agent_name: "agent-1",
      session_id: "s1",
      name: "Session 1",
      root_node_id: "root",
      active_branch_id: "b1",
      lifecycle: "open",
      system_prompt: "",
      created_at: "",
      metadata: {},
      message_count: 0,
      iteration_count: 0,
    };
    const branchInfo = {
      branch_id: "b1",
      session_id: "s1",
      name: "main",
      head_node_id: "root",
      lifecycle: "open",
      created_at: "",
      metadata: {},
    };

    it("projects active Session and clears its Branches and chains when archived", async () => {
      await connectClient();
      const sessionsStore = useSessionsStore();
      const branchesStore = useBranchesStore();
      const chainsStore = useActionChainsStore();
      const target = chainTarget("agent-1", { agentId: "a1" });

      internals(client)._dispatch(
        EventType.SESSION_ACTIVATED,
        makeMsg(EventType.SESSION_ACTIVATED, {
          project_id: "p1",
          agent_id: "a1",
          agent_name: "agent-1",
          session: sessionInfo,
        }),
      );
      branchesStore.replaceSessionBranches(target, [branchInfo]);
      chainsStore.setChain(target, [{ id: "n1" } as never]);
      expect(sessionsStore.activeSessionId(target)).toBe("s1");

      internals(client)._dispatch(
        EventType.SESSION_ARCHIVED,
        makeMsg(EventType.SESSION_ARCHIVED, {
          project_id: "p1",
          agent_id: "a1",
          agent_name: "agent-1",
          session_id: "s1",
        }),
      );

      expect(sessionsStore.sessionsForAgent(target)).toHaveLength(0);
      expect(sessionsStore.activeSessionId(target)).toBeNull();
      expect(branchesStore.branchesForSession(target)).toHaveLength(0);
      expect(chainsStore.getChain(target)).toHaveLength(0);
    });

    it("projects active Branch and clears only its chain when deleted", async () => {
      await connectClient();
      const branchesStore = useBranchesStore();
      const chainsStore = useActionChainsStore();
      const target = chainTarget("agent-1", { agentId: "a1" });

      internals(client)._dispatch(
        EventType.BRANCH_ACTIVATED,
        makeMsg(EventType.BRANCH_ACTIVATED, {
          project_id: "p1",
          agent_id: "a1",
          agent_name: "agent-1",
          branch: branchInfo,
        }),
      );
      chainsStore.setChain(target, [{ id: "n1" } as never]);
      expect(branchesStore.activeBranchId(target)).toBe("b1");

      internals(client)._dispatch(
        EventType.BRANCH_DELETED,
        makeMsg(EventType.BRANCH_DELETED, {
          project_id: "p1",
          agent_id: "a1",
          agent_name: "agent-1",
          session_id: "s1",
          branch_id: "b1",
        }),
      );

      expect(branchesStore.branchesForSession(target)).toHaveLength(0);
      expect(branchesStore.activeBranchId(target)).toBeNull();
      expect(chainsStore.getChain(target)).toHaveLength(0);
    });
  });

  describe("task events", () => {
    function makeTask(taskId: string, overrides: Record<string, unknown> = {}) {
      return {
        task_id: taskId,
        project_id: "p1",
        title: taskId,
        description: "",
        status: "pending",
        priority: "normal",
        dependencies: [],
        created_at: "",
        updated_at: "",
        metadata: {},
        ...overrides,
      };
    }

    it("task_created upserts, task_deleted removes", async () => {
      await connectClient();
      const store = useTasksStore();

      internals(client)._dispatch(
        EventType.TASK_CREATED,
        makeMsg(EventType.TASK_CREATED, { task: makeTask("t1") }),
      );
      expect(store.tasks.has("t1")).toBe(true);

      internals(client)._dispatch(
        EventType.TASK_UPDATED,
        makeMsg(EventType.TASK_UPDATED, {
          task: makeTask("t1", { status: "in_progress" }),
          previous_status: "pending",
        }),
      );
      expect(store.tasks.get("t1")?.status).toBe("in_progress");

      internals(client)._dispatch(
        EventType.TASK_DELETED,
        makeMsg(EventType.TASK_DELETED, { task: makeTask("t1"), reason: "cleanup" }),
      );
      expect(store.tasks.has("t1")).toBe(false);
    });
  });

  describe("hitl events", () => {
    it("adds hitl request on hitl_request", async () => {
      await connectClient();
      const store = useHitlStore();

      internals(client)._dispatch(
        EventType.HITL_REQUEST,
        makeMsg(EventType.HITL_REQUEST, {
          promise_id: "p1",
          agent_name: "agent-1",
          ability_name: "write_file",
          tool_args: { file_path: "/tmp/test.txt" },
          context: {},
        }),
      );

      expect(store.requests).toHaveLength(1);
      expect(store.requests[0].promiseId).toBe("p1");
      expect(store.requests[0].abilityName).toBe("write_file");
    });

    it("removes hitl request on hitl_resolved", async () => {
      await connectClient();
      const store = useHitlStore();

      internals(client)._dispatch(
        EventType.HITL_REQUEST,
        makeMsg(EventType.HITL_REQUEST, {
          promise_id: "p1",
          agent_name: "agent-1",
          ability_name: "write_file",
          tool_args: { file_path: "/tmp/test.txt" },
          context: {},
        }),
      );
      expect(store.requests).toHaveLength(1);

      internals(client)._dispatch(
        EventType.HITL_RESOLVED,
        makeMsg(EventType.HITL_RESOLVED, {
          promise_id: "p1",
          agent_name: "agent-1",
          ability_name: "write_file",
          tool_call_id: "call-1",
          status: "timeout",
        }),
      );
      expect(store.requests).toHaveLength(0);
    });
  });

  describe("action chain events", () => {
    it("adds chain node on action_chain_updated", async () => {
      await connectClient();
      const store = useActionChainsStore();

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          agent_name: "agent-1",
          node: {
            id: "node-1",
            ability_names: ["read_file"],
            action_results: [
              {
                ability_name: "read_file",
                action_result: { outcome: "success", data: { file_path: "/tmp/test.txt" } },
              },
            ],
          },
        }),
      );

      expect(store.getChain(chainTarget("agent-1"))).toHaveLength(1);
    });
  });

  describe("context usage events", () => {
    it("stores latest usage on context_usage_updated", async () => {
      await connectClient();
      const store = useContextUsageStore();

      internals(client)._dispatch(
        EventType.CONTEXT_USAGE_UPDATED,
        makeMsg(EventType.CONTEXT_USAGE_UPDATED, {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          agent_name: "agent-1",
          phase: "post_call",
          occupied_tokens: 4096,
          basis: "real",
          budget_tokens: 8192,
          real_input_tokens: 4096,
          real_output_tokens: 200,
        }),
      );

      const display = store.usageFor({ projectId: "p1", agentId: "a1" });
      expect(display).not.toBeNull();
      expect(display?.mode).toBe("ratio");
      expect(display?.percent).toBe(50);
    });

    it("clears derived usage on agent_terminated", async () => {
      await connectClient();
      const usage = useContextUsageStore();
      const agents = useAgentsStore();
      agents.onAgentSpawned({
        name: "agent-1",
        agent_id: "a1",
        project_id: "p1",
        cluster_id: "c1",
        incarnation_id: "i1",
        recovery_mode: "",
        config: { ...DEFAULT_CONFIG, name: "agent-1" },
      });
      usage.onContextUsageUpdated(
        {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          agent_name: "agent-1",
          phase: "post_call",
          occupied_tokens: 100,
          basis: "real",
          budget_tokens: 0,
          real_input_tokens: 100,
          real_output_tokens: 10,
        },
        1,
      );
      expect(usage.usageFor({ projectId: "p1", agentId: "a1" })).not.toBeNull();

      internals(client)._dispatch(
        EventType.AGENT_TERMINATED,
        makeMsg(EventType.AGENT_TERMINATED, {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          incarnation_id: "i1",
          name: "agent-1",
        }),
      );

      expect(usage.usageFor({ projectId: "p1", agentId: "a1" })).toBeNull();
    });
  });

  describe("changes events (node projection)", () => {
    it("adds file change on action_chain_updated for write_file", async () => {
      await connectClient();
      const store = useChangesStore();

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          agent_name: "agent-1",
          node: {
            id: "node-1",
            agent_name: "agent-1",
            action_results: [
              {
                ability_name: "write_file",
                action_result: { outcome: "success", data: { file_path: "/tmp/test.txt" } },
              },
            ],
          },
        }),
      );

      expect(store.changes).toHaveLength(1);
      expect(store.changes[0].filePath).toBe("/tmp/test.txt");
      expect(store.changes[0].abilityName).toBe("write_file");
    });

    it("ignores non-file-change abilities (no entry)", async () => {
      await connectClient();
      const store = useChangesStore();

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          agent_name: "agent-1",
          node: {
            id: "node-2",
            agent_name: "agent-1",
            action_results: [
              { ability_name: "read_file", action_result: { outcome: "success", data: {} } },
            ],
          },
        }),
      );

      expect(store.changes).toHaveLength(0);
    });

    it("ABILITY_RESULT no longer feeds changes store (join removed, zero residual)", async () => {
      await connectClient();
      const store = useChangesStore();

      internals(client)._dispatch(
        EventType.ABILITY_RESULT,
        makeMsg(EventType.ABILITY_RESULT, {
          request_id: "r1",
          agent_name: "agent-1",
          ability_name: "write_file",
          success: true,
          result: "OK",
        }),
      );

      expect(store.changes).toHaveLength(0);
    });

    it("reconnect replay dedup: same ACTION_CHAIN_UPDATED node dispatched twice = single entries", async () => {
      await connectClient();
      const chainStore = useActionChainsStore();
      const changesStore = useChangesStore();

      const node = {
        id: "node-r1",
        agent_name: "agent-1",
        ability_names: ["write_file", "conversation"],
        messages_delta: [
          { role: "ai", content_blocks: [{ type: "text", text: "reply" }], metadata: {} },
        ],
        action_results: [
          {
            ability_name: "write_file",
            action_result: { outcome: "success", data: { file_path: "/x" } },
          },
        ],
      };

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-1", node }),
      );
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-1", node }),
      );

      expect(chainStore.getChain(chainTarget("agent-1"))).toHaveLength(1);
      expect(changesStore.changes).toHaveLength(1);
    });

    it("reconnect replay dedup: same ROOM_LOG_APPENDED entry dispatched twice = single log entry", async () => {
      await connectClient();
      const roomsStore = useRoomsStore();

      const entry = {
        id: "e1",
        room_id: "r1",
        seq: 1,
        author: "agent-1",
        author_type: "agent",
        timestamp: 1750000000,
        data: { message: "hi" },
      };

      internals(client)._dispatch(
        EventType.ROOM_LOG_APPENDED,
        makeMsg(EventType.ROOM_LOG_APPENDED, { entry }),
      );
      internals(client)._dispatch(
        EventType.ROOM_LOG_APPENDED,
        makeMsg(EventType.ROOM_LOG_APPENDED, { entry }),
      );

      expect(roomsStore.logs.get("r1")).toHaveLength(1);
    });

    it("reconnect replay dedup across multiple agents: chain buckets both deduped", async () => {
      await connectClient();
      const chainStore = useActionChainsStore();

      const nodeA = {
        id: "node-a",
        agent_name: "agent-A",
        ability_names: ["conversation"],
        messages_delta: [
          { role: "ai", content_blocks: [{ type: "text", text: "from-A" }], metadata: {} },
        ],
      };
      const nodeB = {
        id: "node-b",
        agent_name: "agent-B",
        ability_names: ["conversation"],
        messages_delta: [
          { role: "ai", content_blocks: [{ type: "text", text: "from-B" }], metadata: {} },
        ],
      };

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-A", node: nodeA }),
      );
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-B", node: nodeB }),
      );
      // 重连 replay：相同节点再次派发
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-A", node: nodeA }),
      );
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, { agent_name: "agent-B", node: nodeB }),
      );

      // chain 分桶：每 agent 1 条
      expect(chainStore.getChain(chainTarget("agent-A"))).toHaveLength(1);
      expect(chainStore.getChain(chainTarget("agent-B"))).toHaveLength(1);
    });
  });

  describe("command_result routing", () => {
    it("sets agents on list_agents command_result", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.LIST_AGENTS,
          data: {
            agents: [
              { name: "agent-1", config: { ...DEFAULT_CONFIG, name: "agent-1" } },
              { name: "agent-2", config: { ...DEFAULT_CONFIG, name: "agent-2" } },
            ],
          },
        }),
      );

      expect(agentsStore.agents.size).toBe(2);
      expect(agentsStore.getAgent({ projectId: "p1", agentId: "id-agent-1" })).toBeDefined();
      expect(agentsStore.getAgent({ projectId: "p1", agentId: "id-agent-2" })).toBeDefined();
    });

    it("uses request context to clear only the requested Project on an empty agent list", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();
      const sessionsStore = useSessionsStore();
      const chainsStore = useActionChainsStore();
      const p1 = chainTarget("same", { agentId: "a1" });
      const p2 = { ...p1, projectId: "p2", agentId: "a2" };
      const activeProject = { status: "active" as const, archived_at: null };
      agentsStore.replaceProjectAgents(
        "p1",
        [{ project_id: "p1", agent_id: "a1", name: "same", runtime_state: "running" }],
        activeProject,
      );
      agentsStore.replaceProjectAgents(
        "p2",
        [{ project_id: "p2", agent_id: "a2", name: "same", runtime_state: "running" }],
        activeProject,
      );
      sessionsStore.replaceAgentSessions(p1, [
        {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          agent_name: "same",
          name: "Session 1",
          session_id: "s1",
          root_node_id: "root",
          active_branch_id: "b1",
          lifecycle: "open",
          system_prompt: "",
          created_at: "",
          metadata: {},
          message_count: 0,
          iteration_count: 0,
        },
      ]);
      chainsStore.setChain(p1, [{ id: "n1" } as never]);
      chainsStore.setChain(p2, [{ id: "n2" } as never]);
      vi.spyOn(client, "getRequestContext").mockReturnValue({
        command: CommandType.LIST_AGENTS,
        payload: { project_id: "p1" },
      });

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "empty-p1",
          success: true,
          data: { agents: [] },
        }),
      );

      expect(agentsStore.agentsForProject("p1")).toHaveLength(0);
      expect(agentsStore.agentsForProject("p2")).toHaveLength(1);
      expect(sessionsStore.sessionsForAgent(p1)).toHaveLength(0);
      expect(chainsStore.getChain(p1)).toHaveLength(0);
      expect(chainsStore.getChain(p2)).toHaveLength(1);
    });

    it("uses request context to replace only the requested active Room bucket with empty", async () => {
      await connectClient();
      const roomsStore = useRoomsStore();
      const room = (roomId: string, projectId: string, status: "active" | "archived") => ({
        room_id: roomId,
        project_id: projectId,
        name: roomId,
        status,
        members: [],
        seq_watermark: 0,
        version: 1,
        created_at: "",
        updated_at: "",
        archived_at: status === "archived" ? "2026-09-09" : null,
      });
      roomsStore.replaceProjectRooms("p1", [room("r1", "p1", "active")], "active");
      roomsStore.replaceProjectRooms("p2", [room("r2", "p2", "active")], "active");
      roomsStore.replaceProjectRooms("p1", [room("ra", "p1", "archived")], "archived");
      roomsStore.setRoomLog("r1", [
        {
          id: "e1",
          room_id: "r1",
          seq: 1,
          author: "user",
          author_type: "human",
          timestamp: 1,
          data: { message: "pending" },
        },
      ]);
      useChatStore().addPendingEntry({
        projectId: "p1",
        to: "r1",
        content: "pending",
        agentName: "",
        roomId: "r1",
      });
      vi.spyOn(client, "getRequestContext").mockReturnValue({
        command: CommandType.ROOM_LIST,
        payload: { project_id: "p1", status: "active" },
      });

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "empty-rooms-p1",
          success: true,
          data: { rooms: [] },
        }),
      );

      expect(roomsStore.roomsForProject("p1", "active")).toHaveLength(0);
      expect(roomsStore.roomsForProject("p2", "active")).toHaveLength(1);
      expect(roomsStore.roomsForProject("p1", "archived")).toHaveLength(1);
      expect(roomsStore.logs.has("r1")).toBe(false);
      expect(useChatStore().allEntries).toHaveLength(0);
    });

    it("F2: list_agents command_result triggers chain history initial sync", async () => {
      await connectClient();
      const chainStore = useActionChainsStore();
      const nodeA = { id: "na", ability_names: ["conversation"] };
      const nodeB = { id: "nb", ability_names: ["send"] };

      useProjectsStore().setProjectsFromList([
        {
          project_id: "p1",
          name: "project",
          description: "",
          project_root_locator: "",
          manifest_ref: "",
          cluster_ids: [],
          workspaces: [],
          agents: [
            {
              name: "agent-1",
              agent_id: "a1",
              cluster_id: "c1",
              manifest_ref: "",
              instance_manifest_path: "",
              system_prompt: "",
              path_grants: [],
              desired_state: "running",
              runtime_status: "running",
              runtime_error: "",
            },
            {
              name: "agent-2",
              agent_id: "a2",
              cluster_id: "c1",
              manifest_ref: "",
              instance_manifest_path: "",
              system_prompt: "",
              path_grants: [],
              desired_state: "running",
              runtime_status: "running",
              runtime_error: "",
            },
          ],
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
          created_at: "",
          updated_at: "",
          archived_at: null,
          deleted_at: null,
        },
      ]);
      vi.spyOn(client, "listSessions").mockImplementation((target) =>
        Promise.resolve({
          request_id: "s",
          success: true,
          data: { sessions: [{ session_id: `s-${target.agentName}` }] },
        } as Awaited<ReturnType<typeof client.listSessions>>),
      );
      vi.spyOn(client, "listBranches").mockImplementation((target) =>
        Promise.resolve({
          request_id: "b",
          success: true,
          data: { branches: [{ branch_id: `b-${target.agentName}` }] },
        } as Awaited<ReturnType<typeof client.listBranches>>),
      );

      const spy = vi.spyOn(client, "getChainHistory").mockImplementation((target) => {
        const nodes = target.agentName === "agent-1" ? [nodeA] : [nodeB];
        return Promise.resolve({
          request_id: "r",
          success: true,
          original_command: CommandType.GET_CHAIN_HISTORY,
          data: { nodes },
        } as Awaited<ReturnType<typeof client.getChainHistory>>);
      });
      const usageSpy = vi.spyOn(client, "getAgentInfo").mockResolvedValue({
        request_id: "u",
        success: true,
        data: { context_usage: null },
      } as Awaited<ReturnType<typeof client.getAgentInfo>>);

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.LIST_AGENTS,
          data: {
            agents: [
              {
                name: "agent-1",
                agent_id: "a1",
                config: { ...DEFAULT_CONFIG, name: "agent-1", agent_id: "a1" },
              },
              {
                name: "agent-2",
                agent_id: "a2",
                config: { ...DEFAULT_CONFIG, name: "agent-2", agent_id: "a2" },
              },
            ],
          },
        }),
      );

      // fire-and-forget：等待两个 getChainHistory 完成
      await vi.waitFor(() => {
        expect(spy).toHaveBeenCalledWith({
          agentName: "agent-1",
          projectId: "p1",
          agentId: "a1",
          sessionId: "s-agent-1",
          branchId: "b-agent-1",
        });
        expect(spy).toHaveBeenCalledWith({
          agentName: "agent-2",
          projectId: "p1",
          agentId: "a2",
          sessionId: "s-agent-2",
          branchId: "b-agent-2",
        });
      });
      // 用量恢复读通量与链同步并行发起
      await vi.waitFor(() => {
        expect(usageSpy).toHaveBeenCalledWith({
          projectId: "p1",
          agentId: "a1",
          agentName: "agent-1",
        });
        expect(usageSpy).toHaveBeenCalledWith({
          projectId: "p1",
          agentId: "a2",
          agentName: "agent-2",
        });
      });
      const firstTarget = chainTarget("agent-1", {
        agentId: "a1",
        sessionId: "s-agent-1",
        branchId: "b-agent-1",
      });
      const secondTarget = chainTarget("agent-2", {
        agentId: "a2",
        sessionId: "s-agent-2",
        branchId: "b-agent-2",
      });
      expect(chainStore.getChain(firstTarget)).toHaveLength(1);
      expect(chainStore.getChain(firstTarget)[0].id).toBe("na");
      expect(chainStore.getChain(secondTarget)[0].id).toBe("nb");
    });

    it("F2: initial chain sync waits for complete project and agent scope", async () => {
      await connectClient();
      const spy = vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "r",
        success: true,
        original_command: CommandType.GET_CHAIN_HISTORY,
        data: { nodes: [] },
      } as Awaited<ReturnType<typeof client.getChainHistory>>);

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.LIST_AGENTS,
          data: {
            agents: [
              {
                name: "shuoxi",
                agent_id: "stable-shuoxi",
                config: { ...DEFAULT_CONFIG, name: "shuoxi", agent_id: "stable-shuoxi" },
              },
            ],
          },
        }),
      );

      await Promise.resolve();
      expect(spy).not.toHaveBeenCalled();
    });

    it("sets rooms on room_list command_result", async () => {
      await connectClient();
      const roomsStore = useRoomsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.ROOM_LIST,
          data: {
            rooms: [
              {
                room_id: "r1",
                project_id: "p1",
                name: "general",
                status: "active",
                members: [],
                seq_watermark: 0,
                version: 1,
                created_at: "",
                updated_at: "",
              },
            ],
            count: 1,
          },
        }),
      );

      expect(roomsStore.rooms.size).toBe(1);
      expect(roomsStore.rooms.get("r1")?.name).toBe("general");
    });

    it("sets projects on project_list command_result", async () => {
      await connectClient();
      const projectsStore = useProjectsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.PROJECT_LIST,
          data: {
            projects: [
              {
                project_id: "p1",
                name: "proj",
                manifest_ref: "",
                cluster_ids: [],
                workspaces: [],
                agents: [],
                task_ids: [],
                status: "active",
                recovery: "resume",
                version: 1,
                created_at: "",
                updated_at: "",
              },
            ],
            count: 1,
          },
        }),
      );

      expect(projectsStore.projects.size).toBe(1);
      expect(projectsStore.projects.get("p1")?.name).toBe("proj");
    });

    it("sets room log on room_get_log command_result (room_id from entries)", async () => {
      await connectClient();
      const roomsStore = useRoomsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.ROOM_GET_LOG,
          data: {
            entries: [
              {
                id: "e2",
                room_id: "r1",
                seq: 2,
                author: "agent-1",
                author_type: "agent",
                timestamp: 1750000001,
                data: { message: "second" },
              },
              {
                id: "e1",
                room_id: "r1",
                seq: 1,
                author: "user",
                author_type: "human",
                timestamp: 1750000000,
                data: { message: "first" },
              },
            ],
            count: 2,
          },
        }),
      );

      expect(roomsStore.logs.get("r1")?.map((e) => e.seq)).toEqual([1, 2]);
    });

    it("ignores non-agents command_result", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          data: { some_other: "value" },
        }),
      );

      expect(agentsStore.agents.size).toBe(0);
    });

    it("restores usage snapshot on get_agent_info command_result", async () => {
      await connectClient();
      const usage = useContextUsageStore();
      vi.spyOn(client, "getRequestContext").mockReturnValue({
        command: CommandType.GET_AGENT_INFO,
        payload: { project_id: "p1", agent_id: "a1", name: "agent-1" },
      });

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "restore-usage",
          success: true,
          data: {
            name: "agent-1",
            context_usage: {
              phase: "post_call",
              occupied_tokens: 4096,
              basis: "anchor",
              budget_tokens: 8192,
              budget_source: "declared",
              compact_threshold: 0.8,
              real_input_tokens: 4096,
              real_output_tokens: 200,
              real_cache_read_tokens: 3200,
              real_cache_write_tokens: 128,
              cumulative_input_tokens: 12000,
              cumulative_output_tokens: 600,
              cumulative_cache_read_tokens: 9000,
            },
          },
        }),
      );

      const display = usage.usageFor({ projectId: "p1", agentId: "a1" });
      expect(display).not.toBeNull();
      expect(display?.mode).toBe("ratio");
      expect(display?.percent).toBe(50);
      expect(display?.realOutputTokens).toBe(200);
      expect(display?.cumulativeInputTokens).toBe(12000);
      expect(display?.cumulativeOutputTokens).toBe(600);
      expect(display?.cumulativeCacheReadTokens).toBe(9000);
    });

    it("skips usage restore when context_usage is null", async () => {
      await connectClient();
      const usage = useContextUsageStore();
      usage.onContextUsageUpdated(
        {
          project_id: "p1",
          agent_id: "a1",
          cluster_id: "c1",
          agent_name: "agent-1",
          phase: "post_call",
          occupied_tokens: 100,
          basis: "real",
          budget_tokens: 0,
          real_input_tokens: 100,
          real_output_tokens: 10,
        },
        1,
      );
      vi.spyOn(client, "getRequestContext").mockReturnValue({
        command: CommandType.GET_AGENT_INFO,
        payload: { project_id: "p1", agent_id: "a1", name: "agent-1" },
      });

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "restore-null",
          success: true,
          data: { name: "agent-1", context_usage: null },
        }),
      );

      // 免挂载/无记录回执不覆盖既有投影
      expect(usage.usageFor({ projectId: "p1", agentId: "a1" })?.realInputTokens).toBe(100);
    });

    it("ignores failed command_result", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: false,
          data: { agents: [] },
          error: "some error",
        }),
      );

      expect(agentsStore.agents.size).toBe(0);
    });

    it("does not pollute agents store with manifest_list_agents result", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          original_command: CommandType.MANIFEST_LIST_AGENTS,
          data: {
            agents: [
              {
                full_name: "my_project.dev_agent",
                namespace: "my_project",
                name: "dev_agent",
                title: "Dev Agent",
                description: "A dev agent",
                tags: [],
                endpoint_name: "gpt-4o",
                system_prompt: "You are a dev agent.",
                ability_refs: [],
                max_iterations: 10,
              },
            ],
          },
        }),
      );

      expect(agentsStore.agents.size).toBe(0);
    });

    it("ignores command_result without original_command", async () => {
      await connectClient();
      const agentsStore = useAgentsStore();

      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "r1",
          success: true,
          data: {
            agents: [{ name: "agent-1", config: { ...DEFAULT_CONFIG, name: "agent-1" } }],
          },
        }),
      );

      expect(agentsStore.agents.size).toBe(0);
    });
  });

  describe("disconnect", () => {
    it("disconnect function stops event routing", async () => {
      await connectClient();
      const roomsStore = useRoomsStore();

      disconnect();

      internals(client)._dispatch(
        EventType.ROOM_LOG_APPENDED,
        makeMsg(EventType.ROOM_LOG_APPENDED, {
          entry: {
            id: "e1",
            room_id: "r1",
            seq: 1,
            author: "agent-1",
            author_type: "agent",
            timestamp: 1750000000,
            data: { message: "hi" },
          },
        }),
      );

      expect(roomsStore.logs.size).toBe(0);
    });
  });

  describe("manifest agent sync on connect", () => {
    function mockSyncMethods(c: ObserverClient) {
      vi.spyOn(c, "listAgents").mockResolvedValue({
        request_id: "r0",
        success: true,
        data: { agents: [] },
      } satisfies CommandResultPayload);
      const listManifestSpy = vi.spyOn(c, "listManifestAgents").mockResolvedValue({
        request_id: "r1",
        success: true,
        data: { agents: [] },
      } satisfies CommandResultPayload);
      const listProjectsSpy = vi.spyOn(c, "listProjects").mockResolvedValue({
        request_id: "r2",
        success: true,
        data: { projects: [{ project_id: "p1" }] },
      } satisfies CommandResultPayload);
      const listRoomsSpy = vi.spyOn(c, "listRooms").mockResolvedValue({
        request_id: "r3",
        success: true,
        data: { rooms: [] },
      } satisfies CommandResultPayload);
      return { listManifestSpy, listProjectsSpy, listRoomsSpy };
    }

    it("runs _syncInitialState (listManifestAgents + listProjects + listRooms) on connect", async () => {
      const { listManifestSpy, listProjectsSpy, listRoomsSpy } = mockSyncMethods(client);

      await connectClient({ skipSync: false });

      // _syncInitialState is the real sync path (not the onConnected callback,
      // which only sets connection state).
      expect(listManifestSpy).toHaveBeenCalled();
      expect(listProjectsSpy).toHaveBeenCalled();
      expect(listRoomsSpy).toHaveBeenCalled();
    });

    it("handles listManifestAgents failure gracefully", async () => {
      // 走真实 _syncInitialState 吞错路径（skipSync:false），listManifestAgents 的
      // mockRejectedValue 实际触发并被 try/catch 吞掉，connect 仍成功、store 为空。
      const { listProjectsSpy, listRoomsSpy } = mockSyncMethods(client);
      vi.spyOn(client, "listManifestAgents").mockRejectedValue(new Error("connection failed"));

      await connectClient({ skipSync: false });

      // 后续同步项不受前项失败影响
      expect(listProjectsSpy).toHaveBeenCalled();
      expect(listRoomsSpy).toHaveBeenCalled();
      const manifestsStore = useManifestsStore();
      expect(manifestsStore.agents.size).toBe(0);
    });
  });

  // ── 性能红线 4：高频事件合帧分发 ──
  describe("high-frequency frame batching", () => {
    function makeManualBatcher(): { batcher: FrameBatcher; drain: () => void } {
      const pending: Array<() => void> = [];
      const batcher = createFrameBatcher((flush) => {
        pending.push(flush);
      });
      return {
        batcher,
        drain: () => {
          const fns = pending.splice(0, pending.length);
          for (const fn of fns) fn();
        },
      };
    }

    it("coalesces a burst of room_log_appended into one flush (order preserved)", async () => {
      const manual = makeManualBatcher();
      await connectClient({ batcher: manual.batcher });
      const roomsStore = useRoomsStore();
      roomsStore.replaceProjectRooms(
        "p1",
        [
          {
            room_id: "r1",
            project_id: "p1",
            name: "arch",
            members: [],
          } as never,
        ],
        "active",
      );

      for (let i = 1; i <= 20; i++) {
        internals(client)._dispatch(
          EventType.ROOM_LOG_APPENDED,
          makeMsg(EventType.ROOM_LOG_APPENDED, {
            entry: {
              id: `e${i}`,
              room_id: "r1",
              seq: i,
              author: "agent-1",
              author_type: "agent",
              timestamp: 1750000000 + i,
              data: { message: `m${i}` },
            },
          }),
        );
      }
      // 未 flush 前 store 不更新（合帧：一条不漏但只渲染一帧）
      expect(roomsStore.logs.get("r1")).toBeUndefined();

      manual.drain();

      const log = roomsStore.logs.get("r1") ?? [];
      expect(log).toHaveLength(20);
      // FIFO 顺序保持（seq 递增）
      expect(log.map((e) => e.seq)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
    });

    it("coalesces action_chain_updated bursts per frame", async () => {
      const manual = makeManualBatcher();
      await connectClient({ batcher: manual.batcher });
      const chainsStore = useActionChainsStore();

      for (let i = 0; i < 10; i++) {
        internals(client)._dispatch(
          EventType.ACTION_CHAIN_UPDATED,
          makeMsg(EventType.ACTION_CHAIN_UPDATED, {
            agent_name: "agent-1",
            node: { id: `n${i}`, parent_id: null, timestamp: `2026-08-25T00:00:${i}Z` },
          }),
        );
      }
      expect(chainsStore.getChain(chainTarget("agent-1"))).toHaveLength(0);
      manual.drain();
      expect(chainsStore.getChain(chainTarget("agent-1"))).toHaveLength(10);
    });

    it("unbind cancels pending batched events (no store writes after disconnect)", async () => {
      const manual = makeManualBatcher();
      await connectClient({ batcher: manual.batcher });
      const roomsStore = useRoomsStore();

      internals(client)._dispatch(
        EventType.ROOM_LOG_APPENDED,
        makeMsg(EventType.ROOM_LOG_APPENDED, {
          entry: {
            id: "e1",
            room_id: "r1",
            seq: 1,
            author: "a",
            author_type: "agent",
            timestamp: 1750000000,
            data: {},
          },
        }),
      );
      disconnect();
      manual.drain();
      expect(roomsStore.logs.size).toBe(0);
    });

    it("setRoomLog union-merges with entries already landed this frame", () => {
      const roomsStore = useRoomsStore();
      roomsStore.setRoomLog("r1", [
        {
          id: "e6",
          room_id: "r1",
          seq: 6,
          author: "a",
          author_type: "agent",
          timestamp: 6,
          data: {},
        },
      ]);
      // get_log 回执只覆盖 1-5：并集保留已见 6
      roomsStore.setRoomLog("r1", [
        {
          id: "e1",
          room_id: "r1",
          seq: 1,
          author: "a",
          author_type: "agent",
          timestamp: 1,
          data: {},
        },
      ]);
      const log = roomsStore.logs.get("r1") ?? [];
      expect(log.map((e) => e.seq)).toEqual([1, 6]);
    });
  });

  describe("session/branch sync ordering", () => {
    function agentTarget() {
      return { projectId: "p1", agentId: "id-agent-1", agentName: "agent-1" };
    }

    function dispatchListAgents() {
      internals(client)._dispatch(
        SystemType.COMMAND_RESULT,
        makeMsg(SystemType.COMMAND_RESULT, {
          request_id: "sync-agents",
          success: true,
          original_command: CommandType.LIST_AGENTS,
          data: {
            agents: [{ project_id: "p1", agent_id: "id-agent-1", name: "agent-1" }],
          },
        }),
      );
    }

    it("late session list does not revert event-advanced active pointer", async () => {
      await connectClient();
      const sessionsStore = useSessionsStore();
      const agent = agentTarget();

      let resolveList: (value: unknown) => void = () => {};
      const gate = new Promise((resolve) => {
        resolveList = resolve;
      });
      vi.spyOn(client, "listSessions").mockImplementation(
        () =>
          gate.then(() => ({
            request_id: "late",
            success: true,
            data: { sessions: [{ session_id: "s1" }], active_session_id: "s1" },
          })) as never,
      );
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "b",
        success: true,
        data: { branches: [] },
      } as never);
      vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        data: { nodes: [] },
      } as never);

      dispatchListAgents();
      await vi.waitFor(() => expect(client.listSessions).toHaveBeenCalled());
      // list 已发出、结果未返回：事件激活 s2（revision 前进）
      internals(client)._dispatch(
        EventType.SESSION_ACTIVATED,
        makeMsg(EventType.SESSION_ACTIVATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          session: {
            session_id: "s2",
            agent_name: "agent-1",
            root_node_id: "root-2",
            active_branch_id: "b2",
          },
        }),
      );
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");

      // 晚到的 list（基线更旧）不得把 active 指针回退到 s1
      resolveList(undefined);
      await vi.waitFor(() => expect(client.listSessions).toHaveBeenCalled());
      await Promise.resolve();
      await Promise.resolve();
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");
    });

    it("late branch list does not revert event-advanced head", async () => {
      await connectClient();
      const branchesStore = useBranchesStore();
      const chainsStore = useActionChainsStore();
      const agent = agentTarget();
      const session = { ...agent, sessionId: "s1" };

      vi.spyOn(client, "listSessions").mockResolvedValue({
        request_id: "s",
        success: true,
        data: { sessions: [{ session_id: "s1", active_branch_id: "b1" }], active_session_id: "s1" },
      } as never);
      let resolveBranches: (value: unknown) => void = () => {};
      const gate = new Promise((resolve) => {
        resolveBranches = resolve;
      });
      vi.spyOn(client, "listBranches").mockImplementation(
        () =>
          gate.then(() => ({
            request_id: "late-branches",
            success: true,
            data: {
              branches: [{ branch_id: "b1", session_id: "s1", name: "main", head_node_id: "n1" }],
              active_branch_id: "b1",
            },
          })) as never,
      );
      vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        data: { nodes: [] },
      } as never);

      dispatchListAgents();
      await vi.waitFor(() => expect(client.listBranches).toHaveBeenCalled());
      // branch list 已发出（基线已捕获）后：commit 事件把 b1 Head 权威推进到 n2
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          node: { id: "n2", parent_id: "n1", session_id: "s1", created_on_branch_id: "b1" },
          branch: {
            branch_id: "b1",
            session_id: "s1",
            name: "main",
            head_node_id: "n2",
          },
        }),
      );
      // 晚到的 branch list（含旧 head n1）不得回退 Head
      resolveBranches(undefined);
      await vi.waitFor(() => expect(client.listBranches).toHaveBeenCalled());
      await Promise.resolve();
      await Promise.resolve();
      const branch = branchesStore.getBranch({ ...session, branchId: "b1" });
      expect(branch?.info.head_node_id).toBe("n2");
      // 节点也被事件合并
      expect(chainsStore.getChain({ ...session, branchId: "b1" }).map((n) => n.id)).toContain("n2");
    });

    it("dedupes concurrent history requests for the same chain", async () => {
      await connectClient();
      vi.spyOn(client, "listSessions").mockResolvedValue({
        request_id: "s",
        success: true,
        data: { sessions: [{ session_id: "s1", active_branch_id: "b1" }], active_session_id: "s1" },
      } as never);
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "b",
        success: true,
        data: {
          branches: [{ branch_id: "b1", session_id: "s1", name: "main", head_node_id: "n1" }],
          active_branch_id: "b1",
        },
      } as never);
      const history = vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        data: { nodes: [] },
      } as never);

      // 两轮同步同时发起（第二触发的 Agent 列表相同）
      dispatchListAgents();
      dispatchListAgents();
      await vi.waitFor(() => expect(history.mock.calls.length).toBeGreaterThan(0));
      await Promise.resolve();
      await Promise.resolve();
      // 第二轮 generation 更新：旧结果丢弃；同 chain key 去重后只应有一次底层请求
      expect(history).toHaveBeenCalledTimes(1);
    });

    it("in-flight reuse never applies a snapshot older than the caller baseline (P1-1)", async () => {
      await connectClient();
      const sessionsStore = useSessionsStore();
      const agent = agentTarget();

      // 第一轮 listSessions 挂起（服务器尚未返回）
      let resolveFirst: (value: unknown) => void = () => {};
      const firstGate = new Promise((resolve) => {
        resolveFirst = resolve;
      });
      let call = 0;
      vi.spyOn(client, "listSessions").mockImplementation(() => {
        call += 1;
        const response =
          call === 1
            ? firstGate.then(() => ({
                request_id: "first",
                success: true,
                // 旧快照：仅 s1、active=s1（早于 s2 激活事件）
                data: { sessions: [{ session_id: "s1" }], active_session_id: "s1" },
              }))
            : Promise.resolve({
                request_id: `second-${call}`,
                success: true,
                // 新快照：服务器已含 s2（发起于第二轮 baseline 之后）
                data: {
                  sessions: [{ session_id: "s1" }, { session_id: "s2" }],
                  active_session_id: "s2",
                },
              });
        return response as never;
      });
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "b",
        success: true,
        data: { branches: [] },
      } as never);
      vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        data: { nodes: [] },
      } as never);

      // 第一轮同步发起（baseline=0），listSessions 在途
      dispatchListAgents();
      await vi.waitFor(() => expect(client.listSessions).toHaveBeenCalledTimes(1));

      // 事件先落地：s2 激活（active revision 前进到 >0）
      internals(client)._dispatch(
        EventType.SESSION_ACTIVATED,
        makeMsg(EventType.SESSION_ACTIVATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          session: {
            session_id: "s2",
            agent_name: "agent-1",
            root_node_id: "root-2",
            active_branch_id: "b2",
          },
        }),
      );
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");

      // 第二轮同步：baseline 已含 s2 激活的 revision；dedupe 命中第一轮在途，
      // 但消费的响应必须发起于自身 baseline 之后——不得复用旧快照回退 s2。
      // 旧实现复用第一轮 Promise（s1-only 旧快照）：s2 将从列表消失且 active 回退 s1。
      dispatchListAgents();
      await Promise.resolve();
      // 释放第一轮的旧快照：其结果对第二轮而言早于自身发起，不允许覆盖
      resolveFirst(undefined);
      await vi.waitFor(() => expect(client.listSessions).toHaveBeenCalledTimes(2));
      await vi.waitFor(() =>
        expect(sessionsStore.sessionsForAgent(agent).map((s) => s.info.session_id)).toEqual([
          "s1",
          "s2",
        ]),
      );
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");
    });

    it("chain history receipt does not write the runtime active pointer (P1-2)", async () => {
      await connectClient();
      const sessionsStore = useSessionsStore();
      const agent = agentTarget();

      vi.spyOn(client, "listSessions").mockResolvedValue({
        request_id: "s",
        success: true,
        data: { sessions: [{ session_id: "s1", active_branch_id: "b1" }], active_session_id: "s1" },
      } as never);
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "b",
        success: true,
        data: {
          branches: [{ branch_id: "b1", session_id: "s1", name: "main", head_node_id: "n1" }],
          active_branch_id: "b1",
        },
      } as never);
      vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        // 恶意/兼容冗余字段：history 回执声称 active 是别的 session
        data: { nodes: [], active_session_id: "s-other" },
      } as never);

      dispatchListAgents();
      await vi.waitFor(() => expect(client.getChainHistory).toHaveBeenCalled());
      await Promise.resolve();
      await Promise.resolve();
      // history 回执不得直写 active 指针；初始同步后 active 仍为 list 回执的 s1
      expect(sessionsStore.activeSessionId(agent)).toBe("s1");
    });

    it("session_list_result event does not revert event-advanced state (M3)", async () => {
      await connectClient();
      const sessionsStore = useSessionsStore();
      const agent = agentTarget();

      // 事件先落地：s2 激活（active 指针 + revision 前进）
      internals(client)._dispatch(
        EventType.SESSION_ACTIVATED,
        makeMsg(EventType.SESSION_ACTIVATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          session: {
            session_id: "s2",
            agent_name: "agent-1",
            root_node_id: "root-2",
            active_branch_id: "b2",
          },
        }),
      );
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");

      // 推送携带旧快照（仅 s1、active=s1）：revision 门须拒绝回退
      internals(client)._dispatch(
        EventType.SESSION_LIST_RESULT,
        makeMsg(EventType.SESSION_LIST_RESULT, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          active_session_id: "s1",
          sessions: [
            {
              session_id: "s1",
              agent_name: "agent-1",
              root_node_id: "root-1",
              active_branch_id: "b1",
            },
          ],
        }),
      );
      await Promise.resolve();
      expect(sessionsStore.activeSessionId(agent)).toBe("s2");
    });

    it("branch_list_result event does not revert event-advanced head (M3)", async () => {
      await connectClient();
      const branchesStore = useBranchesStore();
      const agent = agentTarget();
      const session = { ...agent, sessionId: "s1" };

      // 事件先落地：commit 事件把 b1 Head 权威推进到 n2
      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          node: { id: "n2", parent_id: "n1", session_id: "s1", created_on_branch_id: "b1" },
          branch: { branch_id: "b1", session_id: "s1", name: "main", head_node_id: "n2" },
        }),
      );
      expect(branchesStore.getBranch({ ...session, branchId: "b1" })?.info.head_node_id).toBe("n2");

      // 推送携带旧快照（head=n1）：revision 门须拒绝回退
      internals(client)._dispatch(
        EventType.BRANCH_LIST_RESULT,
        makeMsg(EventType.BRANCH_LIST_RESULT, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          session_id: "s1",
          active_branch_id: "b1",
          branches: [{ branch_id: "b1", session_id: "s1", name: "main", head_node_id: "n1" }],
        }),
      );
      await Promise.resolve();
      expect(branchesStore.getBranch({ ...session, branchId: "b1" })?.info.head_node_id).toBe("n2");
    });

    it("reconnect replays agent action sync with a fresh generation (M3b)", async () => {
      await connectClient();
      useProjectsStore().setProjectsFromList([
        { project_id: "p1", name: "p1", status: "active", archived_at: null } as never,
      ]);
      const listAgents = vi.spyOn(client, "listAgents").mockImplementation(() => {
        // 模拟真实回执路径：COMMAND_RESULT dispatch 由服务器消息驱动
        internals(client)._dispatch(
          SystemType.COMMAND_RESULT,
          makeMsg(SystemType.COMMAND_RESULT, {
            request_id: "reconnect-agents",
            success: true,
            original_command: CommandType.LIST_AGENTS,
            data: { agents: [{ project_id: "p1", agent_id: "id-agent-1", name: "agent-1" }] },
          }),
        );
        return Promise.resolve({ success: true, data: {} } as never);
      });
      vi.spyOn(client, "listSessions").mockResolvedValue({
        request_id: "s",
        success: true,
        data: { sessions: [{ session_id: "s1", active_branch_id: "b1" }], active_session_id: "s1" },
      } as never);
      vi.spyOn(client, "listBranches").mockResolvedValue({
        request_id: "b",
        success: true,
        data: { branches: [] },
      } as never);
      vi.spyOn(client, "getChainHistory").mockResolvedValue({
        request_id: "h",
        success: true,
        data: { nodes: [] },
      } as never);

      internals(client)._notifyReconnected();
      // 重连须对 operable 项目重放 listAgents（→ syncAgentActionState 全链重同步）
      await vi.waitFor(() => expect(listAgents).toHaveBeenCalledWith("p1"));
      await vi.waitFor(() => expect(client.listSessions).toHaveBeenCalled());
    });

    it("stale branch snapshot does not revert event-advanced head (U3)", async () => {
      await connectClient();
      const branchesStore = useBranchesStore();
      const agent = agentTarget();
      const session = { ...agent, sessionId: "s1" };

      const commit = (id: string, iteration: number, headNodeId: string) =>
        internals(client)._dispatch(
          EventType.ACTION_CHAIN_UPDATED,
          makeMsg(EventType.ACTION_CHAIN_UPDATED, {
            project_id: "p1",
            agent_id: "id-agent-1",
            agent_name: "agent-1",
            node: {
              id,
              parent_id: "n0",
              session_id: "s1",
              created_on_branch_id: "b1",
              iteration,
              timestamp: "2026-09-19T00:00:00+00:00",
            },
            branch: { branch_id: "b1", session_id: "s1", name: "main", head_node_id: headNodeId },
          }),
        );

      // 新 commit（iteration 5）推进 Head 到 n5
      commit("n5", 5, "n5");
      expect(branchesStore.getBranch({ ...session, branchId: "b1" })?.info.head_node_id).toBe("n5");

      // 乱序/replay 旧快照（iteration 2、head=n2）不得回退 Head
      commit("n2", 2, "n2");
      expect(branchesStore.getBranch({ ...session, branchId: "b1" })?.info.head_node_id).toBe("n5");

      // 更新 commit（iteration 7）正常前移
      commit("n7", 7, "n7");
      expect(branchesStore.getBranch({ ...session, branchId: "b1" })?.info.head_node_id).toBe("n7");
    });

    it("branch snapshot mismatching node bucket does not advance head (U3b)", async () => {
      await connectClient();
      const branchesStore = useBranchesStore();
      const agent = agentTarget();
      const session = { ...agent, sessionId: "s1" };

      internals(client)._dispatch(
        EventType.ACTION_CHAIN_UPDATED,
        makeMsg(EventType.ACTION_CHAIN_UPDATED, {
          project_id: "p1",
          agent_id: "id-agent-1",
          agent_name: "agent-1",
          node: {
            id: "n1",
            parent_id: null,
            session_id: "s1",
            created_on_branch_id: "b1",
          },
          // 异常事件：branch 快照指向别的 branch，与 node 归桶不一致
          branch: { branch_id: "b-other", session_id: "s1", name: "x", head_node_id: "n1" },
        }),
      );

      // n1 仍按 node 归桶合并进 b1，但 b-other 不得被异常快照创建/推进
      expect(branchesStore.getBranch({ ...session, branchId: "b-other" })).toBeUndefined();
    });
  });
});
