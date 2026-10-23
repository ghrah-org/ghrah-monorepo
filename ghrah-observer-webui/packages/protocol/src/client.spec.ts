import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionError, ServerClient, TimeoutError, type WebSocketLike } from "./client.js";
import { ClientType, CommandType, EventType, SystemType } from "./enums.js";
import type { ServerMessage } from "./message.js";

const WS_OPEN = 1;
const WS_CLOSED = 3;

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
    close() {
      ws.readyState = WS_CLOSED;
    },
  };
  return ws;
}

describe("ServerClient", () => {
  let client: ServerClient;
  let mockWs: MockWebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWs();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function createClientWithMockWs(opts?: {
    maxReconnectDelay?: number;
    initialReconnectDelay?: number;
  }) {
    const c = new ServerClient("ws://localhost:8080/ws", "observer", {
      ...opts,
      wsFactory: () => mockWs,
    });
    return c;
  }

  async function connectClient(c: ServerClient) {
    const connectPromise = c.connect();
    mockWs.onopen!();
    await connectPromise;
  }

  describe("_buildUrl", () => {
    it("includes client_type query param", () => {
      client = new ServerClient("ws://localhost:8080/ws", "observer");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const url = (client as any)._buildUrl() as string;
      expect(url).toContain("client_type=observer");
    });

    it("includes last_seq_id when > 0", () => {
      client = new ServerClient("ws://localhost:8080/ws", "observer");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (client as any)._lastSeqId = 42;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const url = (client as any)._buildUrl() as string;
      expect(url).toContain("last_seq_id=42");
    });

    it("omits last_seq_id when 0", () => {
      client = new ServerClient("ws://localhost:8080/ws", "observer");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const url = (client as any)._buildUrl() as string;
      expect(url).not.toContain("last_seq_id");
    });
  });

  describe("connect()", () => {
    it("calls onConnected callback on first connection", async () => {
      client = createClientWithMockWs();
      const connectedCb = vi.fn();
      client.onConnected(connectedCb);

      await connectClient(client);

      expect(connectedCb).toHaveBeenCalledOnce();
    });

    it("calls onReconnected on subsequent connections", async () => {
      client = createClientWithMockWs();
      const reconnectedCb = vi.fn();
      client.onReconnected(reconnectedCb);

      await connectClient(client);

      (client as any)._reconnectAttempt = 2;
      const reconnectPromise = (client as any)._doConnect((client as any)._buildUrl() as string);
      mockWs.onopen!();
      await reconnectPromise;

      expect(reconnectedCb).toHaveBeenCalledOnce();
    });
  });

  describe("disconnect()", () => {
    it("clears running state and closes websocket", async () => {
      client = createClientWithMockWs();
      await connectClient(client);

      expect(client.connected).toBe(true);
      await client.disconnect();
      expect(client.connected).toBe(false);
    });
  });

  describe("send()", () => {
    it("throws ConnectionError when not connected", async () => {
      client = createClientWithMockWs();
      await expect(client.send({ type: "ping", payload: {} })).rejects.toThrow(ConnectionError);
    });

    it("sends serialized message when connected", async () => {
      client = createClientWithMockWs();
      await connectClient(client);

      await client.send({ type: "ping", payload: {} });
      expect(mockWs.sent).toHaveLength(1);
      const parsed = JSON.parse(mockWs.sent[0]);
      expect(parsed.type).toBe("ping");
    });
  });

  describe("request()", () => {
    it("resolves with CommandResultPayload on response", async () => {
      client = createClientWithMockWs();
      await connectClient(client);

      const msg: ServerMessage = {
        type: CommandType.LIST_AGENTS,
        payload: {},
        request_id: "test-req-001",
        client_type: ClientType.OBSERVER,
      };

      const requestPromise = client.request(msg, 5000);

      expect(mockWs.sent).toHaveLength(1);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: {
            request_id: "test-req-001",
            success: true,
            data: { agents: [] },
          },
          request_id: "test-req-001",
        }),
      });

      const result = await requestPromise;
      expect(result.request_id).toBe("test-req-001");
      expect(result.success).toBe(true);
    });

    it("surfaces correlated system errors immediately and still dispatches the event", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      const handler = vi.fn();
      client.on(SystemType.ERROR, handler);
      const pending = client.request(
        {
          type: CommandType.LIST_AGENTS,
          payload: {},
          request_id: "failed-command",
        },
        5000,
      );
      mockWs.onmessage!({
        data: JSON.stringify({
          type: "error",
          request_id: "failed-command",
          payload: { code: "COMMAND_ERROR", message: "source ability missing" },
        }),
      });
      await expect(pending).resolves.toMatchObject({
        success: false,
        request_id: "failed-command",
        error: "source ability missing",
        error_detail: "COMMAND_ERROR",
      });
      expect(handler).toHaveBeenCalledTimes(1);
    });

    it("rejects with TimeoutError after timeout", async () => {
      client = createClientWithMockWs();
      await connectClient(client);

      const msg: ServerMessage = {
        type: CommandType.LIST_AGENTS,
        payload: {},
        request_id: "test-timeout",
        client_type: ClientType.OBSERVER,
      };

      const requestPromise = client.request(msg, 100);

      vi.advanceTimersByTime(200);

      await expect(requestPromise).rejects.toThrow(TimeoutError);
    });
  });

  describe("on()/off()", () => {
    it("dispatches event handlers", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      const handler = vi.fn();
      client.on(EventType.AGENT_SPAWNED, handler);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_spawned",
          payload: { name: "agent-1" },
        }),
      });

      expect(handler).toHaveBeenCalledOnce();
      const callArg = handler.mock.calls[0][0] as ServerMessage;
      expect(callArg.type).toBe("agent_spawned");
    });

    it("dispatches system handlers", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      const handler = vi.fn();
      client.on(SystemType.ERROR, handler);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "error",
          payload: { code: "ERR", message: "fail" },
        }),
      });

      expect(handler).toHaveBeenCalledOnce();
    });

    it("removes specific handler with off(handler)", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      const handler = vi.fn();
      client.on(EventType.AGENT_ERROR, handler);
      client.off(EventType.AGENT_ERROR, handler);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_error",
          payload: { agent_name: "a1", error: "err" },
        }),
      });

      expect(handler).not.toHaveBeenCalled();
    });

    it("removes all handlers for event type with off(event)", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      const handler1 = vi.fn();
      const handler2 = vi.fn();
      client.on(EventType.AGENT_ERROR, handler1);
      client.on(EventType.AGENT_ERROR, handler2);
      client.off(EventType.AGENT_ERROR);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_error",
          payload: { agent_name: "a1", error: "err" },
        }),
      });

      expect(handler1).not.toHaveBeenCalled();
      expect(handler2).not.toHaveBeenCalled();
    });
  });

  describe("_processMessage()", () => {
    beforeEach(() => {
      client = createClientWithMockWs();
    });

    it("replies with PONG when receiving PING", async () => {
      await connectClient(client);
      mockWs.sent.length = 0;

      mockWs.onmessage!({ data: JSON.stringify({ type: "ping", payload: {} }) });

      expect(mockWs.sent).toHaveLength(1);
      const sent = JSON.parse(mockWs.sent[0]);
      expect(sent.type).toBe("pong");
    });

    it("resolves pending request on command_result", async () => {
      await connectClient(client);
      const requestId = "resolve-test";

      const requestPromise = client.request(
        {
          type: CommandType.LIST_AGENTS,
          payload: {},
          request_id: requestId,
          client_type: ClientType.OBSERVER,
        },
        5000,
      );

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: {
            request_id: requestId,
            success: true,
            data: { agents: ["a1"] },
          },
          request_id: requestId,
        }),
      });

      const result = await requestPromise;
      expect(result.request_id).toBe(requestId);
      expect(result.success).toBe(true);
    });

    it("updates foldedState on event messages", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_response",
          payload: { sender: "agent-1", recipient: "user", content: "hi" },
        }),
      });

      expect(client.foldedState).toHaveProperty("agent-1:agent_response");
    });

    it("does not fold command_result messages", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "command_result",
          payload: { request_id: "r1", success: true, data: {} },
          request_id: "r1",
        }),
      });

      expect(Object.keys(client.foldedState)).toHaveLength(0);
    });

    it("does not fold PONG messages", async () => {
      await connectClient(client);

      mockWs.onmessage!({ data: JSON.stringify({ type: "pong", payload: {} }) });

      expect(Object.keys(client.foldedState)).toHaveLength(0);
    });

    it("updates lastSeqId from message seq_id", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_spawned",
          payload: { name: "a1" },
          seq_id: 99,
        }),
      });

      expect(client.lastSeqId).toBe(99);
    });
  });

  describe("state folding", () => {
    beforeEach(() => {
      client = createClientWithMockWs();
    });

    it("extracts agent name from agent_name field", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_error",
          payload: { agent_name: "a1", error: "oops" },
        }),
      });

      expect(client.foldedState).toHaveProperty("a1:agent_error");
    });

    it("extracts agent name from name field as fallback", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_spawned",
          payload: { name: "a1", config: { name: "a1" } },
        }),
      });

      expect(client.foldedState).toHaveProperty("a1:agent_spawned");
    });

    it("extracts agent name from sender field as fallback", async () => {
      await connectClient(client);

      mockWs.onmessage!({
        data: JSON.stringify({
          type: "agent_response",
          payload: { sender: "a1", recipient: "user", content: "hi" },
        }),
      });

      expect(client.foldedState).toHaveProperty("a1:agent_response");
    });
  });

  describe("callbacks", () => {
    it("triggers onDisconnected via _notifyDisconnected", () => {
      client = createClientWithMockWs();
      const disconnectedCb = vi.fn();
      client.onDisconnected(disconnectedCb);

      (client as any)._notifyDisconnected();

      expect(disconnectedCb).toHaveBeenCalledOnce();
    });

    it("triggers onConnected via _notifyConnected", () => {
      client = createClientWithMockWs();
      const connectedCb = vi.fn();
      client.onConnected(connectedCb);

      (client as any)._notifyConnected();

      expect(connectedCb).toHaveBeenCalledOnce();
    });

    it("triggers onReconnected via _notifyReconnected", () => {
      client = createClientWithMockWs();
      const reconnectedCb = vi.fn();
      client.onReconnected(reconnectedCb);

      (client as any)._notifyReconnected();

      expect(reconnectedCb).toHaveBeenCalledOnce();
    });
  });

  describe("heart behavior", () => {
    it("sends ping after heartbeat interval", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      mockWs.sent.length = 0;

      vi.advanceTimersByTime(30_000);

      const sentMessages = mockWs.sent.map((s) => JSON.parse(s));
      const pings = sentMessages.filter((m) => m.type === "ping");
      expect(pings.length).toBeGreaterThanOrEqual(1);
    });

    it("stops heartbeat on disconnect", async () => {
      client = createClientWithMockWs();
      await connectClient(client);

      await client.disconnect();

      mockWs.sent.length = 0;
      vi.advanceTimersByTime(60_000);

      const pings = mockWs.sent.filter((s) => JSON.parse(s).type === "ping");
      expect(pings).toHaveLength(0);
    });
  });

  describe("connected getter", () => {
    it("returns false when ws is null", () => {
      client = createClientWithMockWs();
      expect(client.connected).toBe(false);
    });

    it("returns true when ws is open and running", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      expect(client.connected).toBe(true);
    });

    it("returns false when ws is closed", async () => {
      client = createClientWithMockWs();
      await connectClient(client);
      mockWs.readyState = WS_CLOSED;
      expect(client.connected).toBe(false);
    });
  });

  describe("_reconnectDelay", () => {
    it("returns initial delay on first attempt", () => {
      client = createClientWithMockWs();
      (client as any)._reconnectAttempt = 0;
      const delay = (client as any)._reconnectDelay() as number;
      expect(delay).toBeGreaterThanOrEqual(1000);
      expect(delay).toBeLessThan(2500);
    });

    it("increases delay with exponential backoff", () => {
      client = createClientWithMockWs();
      (client as any)._reconnectAttempt = 5;
      const delay = (client as any)._reconnectDelay() as number;
      expect(delay).toBeGreaterThan(1000);
    });
  });
});

describe("ConnectionError", () => {
  it("has correct name and message", () => {
    const err = new ConnectionError("test");
    expect(err.name).toBe("ConnectionError");
    expect(err.message).toBe("test");
  });
});

describe("TimeoutError", () => {
  it("has correct name and message", () => {
    const err = new TimeoutError("test timeout");
    expect(err.name).toBe("TimeoutError");
    expect(err.message).toBe("test timeout");
  });
});
