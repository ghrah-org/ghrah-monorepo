// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import {
  createCommand,
  createCommandResult,
  createError,
  createEvent,
  createPing,
  createPong,
  generateRequestId,
} from "./builders.js";
import { ClientType, CommandType, EventType, SystemType } from "./enums.js";

describe("generateRequestId", () => {
  it("returns a 12-character hex string", () => {
    const id = generateRequestId();
    expect(id).toHaveLength(12);
    expect(id).toMatch(/^[0-9a-f]{12}$/);
  });

  it("generates unique ids", () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateRequestId()));
    expect(ids.size).toBe(100);
  });
});

describe("createCommand", () => {
  it("creates a command message with correct type and payload", () => {
    const msg = createCommand(CommandType.SPAWN_AGENT, { name: "agent-1" });
    expect(msg.type).toBe("spawn_agent");
    expect(msg.payload).toEqual({ name: "agent-1" });
    expect(msg.client_type).toBe(ClientType.OBSERVER);
  });

  it("generates request_id when not provided", () => {
    const msg = createCommand(CommandType.LIST_AGENTS, {});
    expect(msg.request_id).toBeDefined();
    expect(msg.request_id).toHaveLength(12);
  });

  it("uses provided request_id", () => {
    const msg = createCommand(
      CommandType.SEND_MESSAGE,
      { target: "a1", content: "hi" },
      "custom-id",
    );
    expect(msg.request_id).toBe("custom-id");
  });
});

describe("createEvent", () => {
  it("creates an event message", () => {
    const msg = createEvent(EventType.AGENT_SPAWNED, { name: "agent-1" });
    expect(msg.type).toBe("agent_spawned");
    expect(msg.payload).toEqual({ name: "agent-1" });
  });

  it("does not include request_id", () => {
    const msg = createEvent(EventType.AGENT_ERROR, { agent_name: "a1", error: "err" });
    expect(msg.request_id).toBeUndefined();
  });
});

describe("createCommandResult", () => {
  it("creates a success result", () => {
    const msg = createCommandResult("req-001", true, { name: "agent-1" });
    expect(msg.type).toBe(SystemType.COMMAND_RESULT);
    expect(msg.request_id).toBe("req-001");
    expect(msg.payload).toEqual({
      request_id: "req-001",
      success: true,
      data: { name: "agent-1" },
      error: null,
      error_detail: null,
    });
  });

  it("creates a failure result", () => {
    const msg = createCommandResult("req-002", false, undefined, "not_found", "Agent missing");
    expect(msg.type).toBe(SystemType.COMMAND_RESULT);
    expect(msg.payload.success).toBe(false);
    expect(msg.payload.data).toBeNull();
    expect(msg.payload.error).toBe("not_found");
    expect(msg.payload.error_detail).toBe("Agent missing");
  });
});

describe("createError", () => {
  it("creates an error message", () => {
    const msg = createError("UNKNOWN_COMMAND", "Unknown command type: foo");
    expect(msg.type).toBe(SystemType.ERROR);
    expect(msg.payload.code).toBe("UNKNOWN_COMMAND");
    expect(msg.payload.message).toBe("Unknown command type: foo");
    expect(msg.payload.details).toBeNull();
  });

  it("creates an error message with details and request_id", () => {
    const msg = createError("VALIDATION_ERROR", "Invalid payload", { field: "name" }, "req-123");
    expect(msg.payload.details).toEqual({ field: "name" });
    expect(msg.request_id).toBe("req-123");
  });
});

describe("createPing", () => {
  it("creates a ping message", () => {
    const msg = createPing();
    expect(msg.type).toBe(SystemType.PING);
    expect(msg.payload).toEqual({});
  });
});

describe("createPong", () => {
  it("creates a pong message", () => {
    const msg = createPong();
    expect(msg.type).toBe(SystemType.PONG);
    expect(msg.payload).toEqual({});
  });
});
