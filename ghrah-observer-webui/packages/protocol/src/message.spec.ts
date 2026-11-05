// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from "vitest";
import { ClientType, SystemType } from "./enums.js";
import { parseMessage, ServerMessageSchema, serializeMessage } from "./message.js";

describe("ServerMessageSchema", () => {
  it("parses a minimal message with only type", () => {
    const result = ServerMessageSchema.parse({ type: "ping" });
    expect(result.type).toBe("ping");
    expect(result.payload).toEqual({});
    expect(result.request_id).toBeUndefined();
    expect(result.timestamp).toBeUndefined();
    expect(result.client_type).toBeUndefined();
    expect(result.seq_id).toBeUndefined();
  });

  it("parses a full message with all fields", () => {
    const result = ServerMessageSchema.parse({
      type: "spawn_agent",
      payload: { name: "agent-1" },
      request_id: "abc123",
      timestamp: 12345.678,
      client_type: "observer",
      seq_id: 42,
    });
    expect(result.type).toBe("spawn_agent");
    expect(result.request_id).toBe("abc123");
    expect(result.timestamp).toBe(12345.678);
    expect(result.client_type).toBe(ClientType.OBSERVER);
    expect(result.seq_id).toBe(42);
  });

  it("rejects missing type", () => {
    expect(() => ServerMessageSchema.parse({ payload: {} })).toThrow();
  });

  it("accepts null for nullable optional fields", () => {
    const result = ServerMessageSchema.parse({
      type: "ping",
      request_id: null,
      timestamp: null,
      client_type: null,
      seq_id: null,
    });
    expect(result.request_id).toBeNull();
    expect(result.timestamp).toBeNull();
    expect(result.client_type).toBeNull();
    expect(result.seq_id).toBeNull();
  });
});

describe("serializeMessage", () => {
  it("auto-fills timestamp when missing", () => {
    const before = Date.now() / 1000;
    const json = serializeMessage({ type: "ping", payload: {} });
    const after = Date.now() / 1000;
    const parsed = JSON.parse(json);
    expect(parsed.timestamp).toBeGreaterThanOrEqual(before);
    expect(parsed.timestamp).toBeLessThanOrEqual(after);
  });

  it("preserves existing timestamp", () => {
    const json = serializeMessage({ type: "ping", payload: {}, timestamp: 12345.0 });
    const parsed = JSON.parse(json);
    expect(parsed.timestamp).toBe(12345.0);
  });

  it("produces valid JSON that parseMessage can read", () => {
    const msg = {
      type: SystemType.PING,
      payload: {},
      request_id: "test-123",
    };
    const json = serializeMessage(msg);
    const roundTripped = parseMessage(json);
    expect(roundTripped.type).toBe("ping");
    expect(roundTripped.request_id).toBe("test-123");
  });
});

describe("parseMessage", () => {
  it("parses valid JSON string", () => {
    const msg = parseMessage('{"type":"pong","payload":{}}');
    expect(msg.type).toBe("pong");
  });

  it("throws on invalid JSON", () => {
    expect(() => parseMessage("not json")).toThrow();
  });

  it("throws on valid JSON that does not match schema", () => {
    expect(() => parseMessage('{"payload":{}}')).toThrow();
  });

  it("round-trips through serializeMessage", () => {
    const original = {
      type: "agent_spawned" as const,
      payload: { name: "agent-1" },
      request_id: "req-001",
      seq_id: 7,
    };
    const serialized = serializeMessage(original);
    const deserialized = parseMessage(serialized);
    expect(deserialized.type).toBe(original.type);
    expect(deserialized.payload).toEqual(original.payload);
    expect(deserialized.request_id).toBe(original.request_id);
    expect(deserialized.seq_id).toBe(original.seq_id);
  });
});
