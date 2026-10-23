import type { AbilityDefinitionPayload, CommandResultPayload } from "@ghrah/protocol";
import { ClientType, CommandType, type WebSocketLike } from "@ghrah/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ObserverClient } from "./client.js";

const WS_OPEN = 1;
const AGENT_TARGET = { projectId: "p1", agentId: "a1", agentName: "agent-1" } as const;

interface MockWebSocket extends WebSocketLike {
  sent: string[];
}

function createMockWs(): MockWebSocket {
  const ws: MockWebSocket = {
    readyState: WS_OPEN,
    sent: [],
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
    send(data: string) {
      ws.sent.push(data);
    },
    close() {},
  };
  return ws;
}

function createClient(mockWs: MockWebSocket): ObserverClient {
  return new ObserverClient("ws://localhost:4111/ws", "observer", {
    wsFactory: () => mockWs,
  });
}

let syncSpy: ReturnType<typeof vi.spyOn<any, any>> | null = null;

async function connectClient(client: ObserverClient, mockWs: MockWebSocket): Promise<void> {
  syncSpy = vi.spyOn(client as any, "_syncInitialState").mockResolvedValue(undefined);
  const connectPromise = client.connect();
  mockWs.onopen!();
  await connectPromise;
}

describe("ObserverClient", () => {
  let client: ObserverClient;
  let mockWs: MockWebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWs();
    client = createClient(mockWs);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (syncSpy) {
      syncSpy.mockRestore();
      syncSpy = null;
    }
    if (client.connected) {
      client.disconnect();
    }
  });

  describe("subscribe", () => {
    it("sends subscribe command with correct payload", async () => {
      await connectClient(client, mockWs);
      mockWs.sent.length = 0;

      await client.subscribe(["agent-1", "agent-2"], ["agent_response"]);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.SUBSCRIBE);
      expect(parsed.payload.agent_names).toEqual(["agent-1", "agent-2"]);
      expect(parsed.payload.event_types).toEqual(["agent_response"]);
    });

    it("sends subscribe with null agentNames to subscribe all", async () => {
      await connectClient(client, mockWs);
      mockWs.sent.length = 0;

      await client.subscribe(null, null);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.SUBSCRIBE);
      expect(parsed.payload.agent_names).toBeUndefined();
      expect(parsed.payload.event_types).toBeUndefined();
    });
  });

  describe("request context", () => {
    it("keeps the originating command scope available during result dispatch", async () => {
      await connectClient(client, mockWs);
      let contextDuringDispatch: ReturnType<ObserverClient["getRequestContext"]>;
      client.on("command_result", (message) => {
        const requestId = String((message.payload as { request_id?: string }).request_id ?? "");
        contextDuringDispatch = client.getRequestContext(requestId);
      });

      const resultPromise = client.listAgents("p-empty");
      const sent = JSON.parse(mockWs.sent.at(-1) ?? "{}") as {
        request_id: string;
      };
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: sent.request_id, success: true, data: { agents: [] } },
          request_id: sent.request_id,
        }),
      });

      await resultPromise;
      expect(contextDuringDispatch).toEqual({
        command: CommandType.LIST_AGENTS,
        payload: { project_id: "p-empty" },
      });
      expect(client.getRequestContext(sent.request_id)).toBeUndefined();
    });
  });

  describe("unsubscribe", () => {
    it("sends unsubscribe command with correct payload", async () => {
      await connectClient(client, mockWs);
      mockWs.sent.length = 0;

      await client.unsubscribe(["agent-1"], ["agent_response"]);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.UNSUBSCRIBE);
      expect(parsed.payload.agent_names).toEqual(["agent-1"]);
    });
  });

  describe("spawnAgent", () => {
    it("sends spawn_agent with config and abilities", async () => {
      await connectClient(client, mockWs);
      const ab: AbilityDefinitionPayload = { ability_type: "read_file", params: {} };
      const spawnPromise = client.spawnAgent(
        "p1",
        {
          name: "test-agent",
          description: "",
          system_prompt: "You are helpful",
          max_iterations: 10,
        },
        [ab],
      );

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.SPAWN_AGENT);
      expect(parsed.payload.project_id).toBe("p1");
      expect(parsed.payload.config.name).toBe("test-agent");
      expect(parsed.payload.abilities).toHaveLength(1);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      const result = await spawnPromise;
      expect(result.success).toBe(true);
    });
  });

  describe("sendMessage", () => {
    it("builds correct send_message payload", async () => {
      await connectClient(client, mockWs);
      const msgPromise = client.sendMessage(AGENT_TARGET, "Hello", "user", {
        timeout: 12,
        metadata: { room_id: "r1" },
      });

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.SEND_MESSAGE);
      expect(parsed.payload.target).toBe("agent-1");
      expect(parsed.payload.content).toBe("Hello");
      expect(parsed.payload.sender).toBe("user");
      expect(parsed.payload).toMatchObject({
        project_id: "p1",
        agent_id: "a1",
        timeout: 12,
        metadata: { room_id: "r1" },
      });

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(msgPromise).resolves.toBeDefined();
    });
  });

  describe("sendAgentMessage", () => {
    const target = { projectId: "p1", agentId: "a2", agentName: "worker" };

    it.each([true, false])("executes a real source ability (async=%s)", async (asyncMode) => {
      await connectClient(client, mockWs);
      const pending = client.sendAgentMessage(AGENT_TARGET, target, "verify", asyncMode);
      const sent = JSON.parse(mockWs.sent.at(-1)!);
      expect(sent.payload.agent_id).toBe("a1");
      expect(sent.payload.sender).toBe("user");
      expect(sent.payload.metadata.execute_ability).toMatchObject({
        name: "send_message",
        args: { target: "a2", content: "verify", fire_and_forget: asyncMode },
      });
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          request_id: sent.request_id,
          payload: {
            request_id: sent.request_id,
            success: true,
            data: {
              content: JSON.stringify({ outcome: "success", data: { message_id: "m1" } }),
            },
          },
        }),
      });
      await expect(pending).resolves.toMatchObject({ success: true, data: { message_id: "m1" } });
    });

    it("does not report ability failure as successful delivery", async () => {
      vi.spyOn(client, "sendMessage").mockResolvedValue({
        success: true,
        request_id: "r1",
        data: {
          content: JSON.stringify({ outcome: "failure", data: { error: "recipient offline" } }),
        },
      });
      await expect(
        client.sendAgentMessage(AGENT_TARGET, target, "verify", false),
      ).resolves.toMatchObject({ success: false, error: "recipient offline" });
    });

    it("rejects cross-project and self links before sending", async () => {
      const send = vi.spyOn(client, "sendMessage");
      await expect(
        client.sendAgentMessage(AGENT_TARGET, { ...target, projectId: "p2" }, "verify", false),
      ).rejects.toThrow("Projects");
      await expect(
        client.sendAgentMessage(AGENT_TARGET, AGENT_TARGET, "verify", false),
      ).rejects.toThrow("distinct");
      expect(send).not.toHaveBeenCalled();
    });
  });

  describe("getChainHistory", () => {
    it("builds correct get_chain_history payload", async () => {
      await connectClient(client, mockWs);
      const msgPromise = client.getChainHistory(
        {
          projectId: "p1",
          agentId: "a1",
          agentName: "planner",
          sessionId: "s1",
          branchId: "b1",
        },
        50,
      );

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.GET_CHAIN_HISTORY);
      expect(parsed.payload.agent_name).toBe("planner");
      expect(parsed.payload.session_id).toBe("s1");
      expect(parsed.payload.branch_id).toBe("b1");
      expect(parsed.payload.limit).toBe(50);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: { nodes: [] } },
          request_id: parsed.request_id,
        }),
      });

      await expect(msgPromise).resolves.toBeDefined();
    });

    it("includes stable agent and project ids when provided", async () => {
      await connectClient(client, mockWs);
      const msgPromise = client.getChainHistory({
        projectId: "p1",
        agentId: "stable-1",
        agentName: "planner",
        sessionId: "s1",
        branchId: "b1",
      });

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.payload).toMatchObject({
        agent_name: "planner",
        project_id: "p1",
        agent_id: "stable-1",
      });

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: { nodes: [] } },
          request_id: parsed.request_id,
        }),
      });

      await expect(msgPromise).resolves.toBeDefined();
    });
  });

  describe("Session and Branch context", () => {
    async function runRequest(promise: Promise<unknown>) {
      const parsed = JSON.parse(mockWs.sent[0]);
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });
      await expect(promise).resolves.toBeDefined();
      return parsed;
    }

    it("always sends the structured Agent/Session/Branch scope", async () => {
      await connectClient(client, mockWs);
      const sessionTarget = { ...AGENT_TARGET, sessionId: "s1" };
      const chainTarget = { ...sessionTarget, branchId: "b1" };

      const sessions = await runRequest(client.listSessions(AGENT_TARGET));
      expect(sessions.payload).toEqual({
        project_id: "p1",
        agent_id: "a1",
        agent_name: "agent-1",
      });

      mockWs.sent.length = 0;
      const branches = await runRequest(client.listBranches(sessionTarget));
      expect(branches.payload).toEqual({
        project_id: "p1",
        agent_id: "a1",
        agent_name: "agent-1",
        session_id: "s1",
      });

      mockWs.sent.length = 0;
      const activated = await runRequest(client.activateBranch(chainTarget));
      expect(activated.payload).toEqual({
        project_id: "p1",
        agent_id: "a1",
        agent_name: "agent-1",
        session_id: "s1",
        branch_id: "b1",
      });
    });
  });

  describe("agentReset", () => {
    it("sends agent_reset with stable scope only and links the receipt by request_id", async () => {
      await connectClient(client, mockWs);
      mockWs.sent.length = 0;

      const resetPromise = client.agentReset(AGENT_TARGET);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.AGENT_RESET);
      expect(parsed.type).toBe("agent_reset");
      expect(parsed.payload).toEqual({ project_id: "p1", agent_id: "a1" });
      expect(parsed.request_id).toBeTruthy();

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: {
            request_id: parsed.request_id,
            success: true,
            data: { session_id: "s2", branch_id: "b2", root_node_id: "n1" },
          },
          request_id: parsed.request_id,
        }),
      } as MessageEvent);
      const result = await resetPromise;
      expect(result?.success).toBe(true);
    });
  });

  describe("sendHitlResponse", () => {
    it("sends hitl_response and resolves on command_result receipt", async () => {
      await connectClient(client, mockWs);
      mockWs.sent.length = 0;

      const responsePromise = client.sendHitlResponse("p1", true, "approved");

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.HITL_RESPONSE);
      expect(parsed.payload.promise_id).toBe("p1");
      expect(parsed.payload.approved).toBe(true);
      expect(parsed.payload.reason).toBe("approved");

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: { resolved: true } },
          request_id: parsed.request_id,
        }),
      } as MessageEvent);
      const result = await responsePromise;
      expect(result?.success).toBe(true);
    });
  });

  describe("listAgents", () => {
    it("sends list_agents command", async () => {
      await connectClient(client, mockWs);
      const listPromise = client.listAgents("p1");

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.LIST_AGENTS);
      expect(parsed.client_type).toBe(ClientType.OBSERVER);
      expect(parsed.payload).toEqual({ project_id: "p1" });

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: { agents: [] } },
          request_id: parsed.request_id,
        }),
      });

      await expect(listPromise).resolves.toBeDefined();
    });
  });

  describe("persist methods", () => {
    it("persistSaveNode sends persist_save_node command", async () => {
      await connectClient(client, mockWs);
      const savePromise = client.persistSaveNode("config", { apiKey: "test" });

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.PERSIST_SAVE_NODE);
      expect(parsed.payload.key).toBe("config");
      expect(parsed.payload.data).toEqual({ apiKey: "test" });
      expect(parsed.payload.namespace).toBe("default");

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(savePromise).resolves.toBeDefined();
    });

    it("persistLoadNode sends persist_load_node command", async () => {
      await connectClient(client, mockWs);
      const loadPromise = client.persistLoadNode("config", "custom_ns");

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.PERSIST_LOAD_NODE);
      expect(parsed.payload.key).toBe("config");
      expect(parsed.payload.namespace).toBe("custom_ns");

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(loadPromise).resolves.toBeDefined();
    });
  });

  describe("registerAbility", () => {
    it("sends register_ability command", async () => {
      await connectClient(client, mockWs);
      const ability: AbilityDefinitionPayload = { ability_type: "write_file", params: {} };
      const regPromise = client.registerAbility(AGENT_TARGET, ability);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.REGISTER_ABILITY);
      expect(parsed.payload.agent_name).toBe("agent-1");
      expect(parsed.payload).toMatchObject({ project_id: "p1", agent_id: "a1" });
      expect(parsed.payload.ability.ability_type).toBe("write_file");

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(regPromise).resolves.toBeDefined();
    });
  });

  describe("delegate", () => {
    it("sends delegate command", async () => {
      await connectClient(client, mockWs);
      const delPromise = client.delegate(
        AGENT_TARGET,
        { projectId: "p1", agentId: "a2", agentName: "agent-2" },
        "Review this",
        15,
      );

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.DELEGATE);
      expect(parsed.payload.from_agent).toBe("agent-1");
      expect(parsed.payload.to_agent).toBe("agent-2");
      expect(parsed.payload.content).toBe("Review this");
      expect(parsed.payload).toMatchObject({
        project_id: "p1",
        from_agent_id: "a1",
        to_agent_id: "a2",
        timeout: 15,
      });

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(delPromise).resolves.toBeDefined();
    });
  });

  describe("workspace methods", () => {
    it("createWorkspace sends create_workspace command", async () => {
      await connectClient(client, mockWs);
      const wpPromise = client.createWorkspace(AGENT_TARGET);

      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe(CommandType.CREATE_WORKSPACE);
      expect(parsed.payload.agent_name).toBe("agent-1");
      expect(parsed.payload).toMatchObject({ project_id: "p1", agent_id: "a1" });

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });

      await expect(wpPromise).resolves.toBeDefined();
    });
  });

  describe("room methods", () => {
    /** 发起请求 → 断言构造 → 回 command_result 解除 pending。 */
    async function runRequest(promise: Promise<unknown>) {
      const parsed = JSON.parse(mockWs.sent[0]);
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });
      await expect(promise).resolves.toBeDefined();
      return parsed;
    }

    it("createRoom sends room_create", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.createRoom("p1", "general"));
      expect(parsed.type).toBe(CommandType.ROOM_CREATE);
      expect(parsed.payload).toEqual({ project_id: "p1", name: "general" });
    });

    it("listRooms sends room_list with optional filters", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.listRooms("p1", "active"));
      expect(parsed.type).toBe(CommandType.ROOM_LIST);
      expect(parsed.payload).toEqual({ project_id: "p1", status: "active" });

      mockWs.sent.length = 0;
      const bare = await runRequest(client.listRooms());
      expect(bare.payload).toEqual({});
    });

    it("getRoom sends room_get", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.getRoom("r1"));
      expect(parsed.type).toBe(CommandType.ROOM_GET);
      expect(parsed.payload).toEqual({ room_id: "r1" });
    });

    it("updateRoom sends room_update with expected_version", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.updateRoom("r1", "new-name", 3));
      expect(parsed.type).toBe(CommandType.ROOM_UPDATE);
      expect(parsed.payload).toEqual({ room_id: "r1", name: "new-name", expected_version: 3 });
    });

    it("archive/restore/delete Room send optimistic lifecycle payloads", async () => {
      await connectClient(client, mockWs);
      const target = { roomId: "r1", expectedVersion: 3 };
      const archived = await runRequest(client.archiveRoom(target));
      expect(archived.type).toBe(CommandType.ROOM_ARCHIVE);
      expect(archived.payload).toEqual({ room_id: "r1", expected_version: 3 });

      mockWs.sent.length = 0;
      const restored = await runRequest(client.restoreRoom(target));
      expect(restored.type).toBe(CommandType.ROOM_RESTORE);
      expect(restored.payload).toEqual({ room_id: "r1", expected_version: 3 });

      mockWs.sent.length = 0;
      const deleted = await runRequest(client.deleteRoom(target));
      expect(deleted.type).toBe(CommandType.ROOM_DELETE);
      expect(deleted.payload).toEqual({ room_id: "r1", expected_version: 3 });
    });

    it("joinRoom / leaveRoom send membership commands", async () => {
      await connectClient(client, mockWs);
      const join = await runRequest(client.joinRoom("r1", "a1", "agent", "agent-1"));
      expect(join.type).toBe(CommandType.ROOM_JOIN);
      expect(join.payload).toEqual({
        room_id: "r1",
        subject: "a1",
        subject_type: "agent",
        subject_name: "agent-1",
      });

      mockWs.sent.length = 0;
      const leave = await runRequest(client.leaveRoom("r1", "agent-1"));
      expect(leave.type).toBe(CommandType.ROOM_LEAVE);
      expect(leave.payload).toEqual({ room_id: "r1", subject: "agent-1" });
    });

    it("getRoomMembers sends room_get_members", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.getRoomMembers("r1"));
      expect(parsed.type).toBe(CommandType.ROOM_GET_MEMBERS);
      expect(parsed.payload).toEqual({ room_id: "r1" });
    });

    it("getRoomLog sends room_get_log with since_seq/limit", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.getRoomLog("r1", 5, 50));
      expect(parsed.type).toBe(CommandType.ROOM_GET_LOG);
      expect(parsed.payload).toEqual({ room_id: "r1", since_seq: 5, limit: 50 });
    });

    it("roomSend sends room_send with data map and default human author", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.roomSend("r1", { message: "hello" }));
      expect(parsed.type).toBe(CommandType.ROOM_SEND);
      expect(parsed.payload).toEqual({
        room_id: "r1",
        author: "user",
        author_type: "human",
        data: { message: "hello" },
      });
    });
  });

  describe("project methods", () => {
    async function runRequest(promise: Promise<unknown>) {
      const parsed = JSON.parse(mockWs.sent[0]);
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });
      await expect(promise).resolves.toBeDefined();
      return parsed;
    }

    it("createProject sends project_create", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(
        client.createProject("proj", {
          description: "Project description",
          manifestRef: "ns.ref",
          projectRootLocator: "/srv/ghrah/projects/proj",
          writableWorkspaces: [
            {
              locator: "/srv/projects/proj",
              name: "source",
              role: "default",
              defaultForAgents: true,
            },
          ],
        }),
      );
      expect(parsed.type).toBe(CommandType.PROJECT_CREATE);
      expect(parsed.payload).toEqual({
        name: "proj",
        description: "Project description",
        manifest_ref: "ns.ref",
        project_root_locator: "/srv/ghrah/projects/proj",
        writable_workspaces: [
          {
            locator: "/srv/projects/proj",
            name: "source",
            role: "default",
            default_for_agents: true,
          },
        ],
      });
    });

    it("createProject preserves an explicit empty writable workspace list", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(
        client.createProject("private-project", { writableWorkspaces: [] }),
      );
      expect(parsed.payload).toEqual({ name: "private-project", writable_workspaces: [] });
    });

    it("listProjects sends independent status and archived filters", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.listProjects({ status: "stopped", archived: true }));
      expect(parsed.type).toBe(CommandType.PROJECT_LIST);
      expect(parsed.payload).toEqual({ status: "stopped", archived: true });
    });

    it("getProject sends project_get", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.getProject("p1"));
      expect(parsed.type).toBe(CommandType.PROJECT_GET);
      expect(parsed.payload).toEqual({ project_id: "p1" });
    });

    it("updateProject sends project_update", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(
        client.updateProject("p1", { name: "renamed", expectedVersion: 2 }),
      );
      expect(parsed.type).toBe(CommandType.PROJECT_UPDATE);
      expect(parsed.payload).toEqual({ project_id: "p1", name: "renamed", expected_version: 2 });
    });

    it("archive/restore/delete Project send optimistic lifecycle payloads", async () => {
      await connectClient(client, mockWs);
      const target = { projectId: "p1", expectedVersion: 2 };
      const archived = await runRequest(client.archiveProject(target));
      expect(archived.type).toBe(CommandType.PROJECT_ARCHIVE);
      expect(archived.payload).toEqual({ project_id: "p1", expected_version: 2 });

      mockWs.sent.length = 0;
      const restored = await runRequest(client.restoreProject(target));
      expect(restored.type).toBe(CommandType.PROJECT_RESTORE);
      expect(restored.payload).toEqual({ project_id: "p1", expected_version: 2 });

      mockWs.sent.length = 0;
      const deleted = await runRequest(client.deleteProject({ ...target, cascadeRooms: true }));
      expect(deleted.type).toBe(CommandType.PROJECT_DELETE);
      expect(deleted.payload).toEqual({
        project_id: "p1",
        expected_version: 2,
        cascade_rooms: true,
      });
    });
  });

  describe("task methods", () => {
    async function runRequest(promise: Promise<unknown>) {
      const parsed = JSON.parse(mockWs.sent[0]);
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: parsed.request_id, success: true, data: {} },
          request_id: parsed.request_id,
        }),
      });
      await expect(promise).resolves.toBeDefined();
      return parsed;
    }

    it("createTask sends task_create with opts", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(
        client.createTask("do it", "p1", { agentName: "agent-1", priority: "high" }),
      );
      expect(parsed.type).toBe(CommandType.TASK_CREATE);
      expect(parsed.payload).toEqual({
        title: "do it",
        project_id: "p1",
        agent_name: "agent-1",
        priority: "high",
      });
    });

    it("listTasks sends task_list with filter", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.listTasks({ projectId: "p1", status: "pending" }));
      expect(parsed.type).toBe(CommandType.TASK_LIST);
      expect(parsed.payload).toEqual({ project_id: "p1", status: "pending" });
    });

    it("getTask sends task_get", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.getTask("t1"));
      expect(parsed.type).toBe(CommandType.TASK_GET);
      expect(parsed.payload).toEqual({ task_id: "t1" });
    });

    it("pluginNegotiate sends plugin_negotiate with enabled_ts", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(
        client.pluginNegotiate([
          {
            plugin_id: "task-commit-attribution",
            version: "0.1.0",
            provides: ["badge-renderer/git_commit"],
          },
        ]),
      );
      expect(parsed.type).toBe(CommandType.PLUGIN_NEGOTIATE);
      expect(parsed.payload).toEqual({
        enabled_ts: [
          {
            plugin_id: "task-commit-attribution",
            version: "0.1.0",
            provides: ["badge-renderer/git_commit"],
          },
        ],
      });
      expect(typeof parsed.request_id).toBe("string");
    });

    it("taskDump sends task_dump (full dump, no limit)", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.taskDump());
      expect(parsed.type).toBe(CommandType.TASK_DUMP);
      expect(parsed.payload).toEqual({});
    });

    it("taskListClaims sends task_list_claims with filter", async () => {
      await connectClient(client, mockWs);
      const parsed = await runRequest(client.taskListClaims({ task_id: "t1", state: "verified" }));
      expect(parsed.type).toBe(CommandType.TASK_LIST_CLAIMS);
      expect(parsed.payload).toEqual({ task_id: "t1", state: "verified" });
    });
  });

  describe("_syncInitialState", () => {
    it("calls listAgents, listManifestAgents, listProjects and listRooms", async () => {
      const listAgentsSpy = vi.spyOn(client, "listAgents").mockResolvedValue({
        request_id: "r0",
        success: true,
        data: { agents: [] },
      } satisfies CommandResultPayload);
      const listManifestSpy = vi.spyOn(client, "listManifestAgents").mockResolvedValue({
        request_id: "r1",
        success: true,
        data: { agents: [] },
      } satisfies CommandResultPayload);
      const listProjectsSpy = vi.spyOn(client, "listProjects").mockResolvedValue({
        request_id: "r2",
        success: true,
        data: { projects: [{ project_id: "p1" }] },
      } satisfies CommandResultPayload);
      const listRoomsSpy = vi.spyOn(client, "listRooms").mockResolvedValue({
        request_id: "r3",
        success: true,
        data: { rooms: [] },
      } satisfies CommandResultPayload);

      await (client as any)._syncInitialState();

      expect(listAgentsSpy).toHaveBeenCalledOnce();
      expect(listAgentsSpy).toHaveBeenCalledWith("p1");
      expect(listManifestSpy).toHaveBeenCalledOnce();
      expect(listProjectsSpy).toHaveBeenCalledOnce();
      expect(listRoomsSpy).toHaveBeenCalledOnce();
      expect(listRoomsSpy).toHaveBeenCalledWith("p1", "active");
    });

    it("continues on listAgents failure", async () => {
      vi.spyOn(client, "listAgents").mockRejectedValue(new Error("fail"));
      const listManifestSpy = vi.spyOn(client, "listManifestAgents").mockResolvedValue({
        request_id: "r1",
        success: true,
        data: { agents: [] },
      } satisfies CommandResultPayload);
      const listProjectsSpy = vi.spyOn(client, "listProjects").mockResolvedValue({
        request_id: "r2",
        success: true,
        data: { projects: [{ project_id: "p1" }] },
      } satisfies CommandResultPayload);
      const listRoomsSpy = vi.spyOn(client, "listRooms").mockResolvedValue({
        request_id: "r3",
        success: true,
        data: { rooms: [] },
      } satisfies CommandResultPayload);

      await (client as any)._syncInitialState();

      expect(listManifestSpy).toHaveBeenCalledOnce();
      expect(listProjectsSpy).toHaveBeenCalledOnce();
      expect(listRoomsSpy).toHaveBeenCalledOnce();
      expect(listRoomsSpy).toHaveBeenCalledWith("p1", "active");
    });
  });
});
