// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { CommandType, EventType, type RoomLogEntryPayload } from "@ghrah/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockState, type MockStateOptions } from "./state.js";

interface Emitted {
  type: EventType;
  payload: Record<string, unknown>;
}

/** 建 project + room + N 个 agent 成员，返回稳定身份。 */
function seed(
  state: MockState,
  agentIds: string[] = ["a1"],
): { projectId: string; roomId: string } {
  const project = state.handleCommand(CommandType.PROJECT_CREATE, { name: "p" });
  const projectId = (project.data as { project: { project_id: string } }).project.project_id;
  const room = state.handleCommand(CommandType.ROOM_CREATE, { project_id: projectId, name: "r" });
  const roomId = (room.data as { room: { room_id: string } }).room.room_id;
  for (const agentId of agentIds) {
    const spawned = state.handleCommand(CommandType.SPAWN_AGENT, {
      project_id: projectId,
      config: { name: `name-${agentId}`, agent_id: agentId },
    });
    expect(spawned.success).toBe(true);
    const joined = state.handleCommand(CommandType.ROOM_JOIN, {
      room_id: roomId,
      subject: agentId,
      subject_type: "agent",
      subject_name: `name-${agentId}`,
    });
    expect(joined.success).toBe(true);
  }
  return { projectId, roomId };
}

function makeState(options: MockStateOptions = {}): { state: MockState; events: Emitted[] } {
  const state = new MockState(options);
  const events: Emitted[] = [];
  state.emit = (type, payload) => {
    events.push({ type, payload });
  };
  return { state, events };
}

function send(
  state: MockState,
  roomId: string,
  data: Record<string, unknown>,
): RoomLogEntryPayload {
  const outcome = state.handleCommand(CommandType.ROOM_SEND, {
    room_id: roomId,
    author: "human:user",
    author_type: "human",
    data,
  });
  expect(outcome.success).toBe(true);
  return (outcome.data as { entry: RoomLogEntryPayload }).entry;
}

function readLog(state: MockState, roomId: string): RoomLogEntryPayload[] {
  const outcome = state.handleCommand(CommandType.ROOM_GET_LOG, { room_id: roomId });
  expect(outcome.success).toBe(true);
  return (outcome.data as { entries: RoomLogEntryPayload[] }).entries;
}

type Deliveries = Array<{
  delivery_id: string;
  recipient_id: string;
  state: string;
  error: string | null;
  blocked_reason: string | null;
}>;

/** 取某条 entry 在 room_get_log 读通里被注入的 deliveries。 */
function deliveriesOf(entries: RoomLogEntryPayload[], entryId: string): Deliveries | undefined {
  return entries.find((e) => e.id === entryId)?.data.deliveries as Deliveries | undefined;
}

describe("MockState 投递态模拟", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("默认关闭时不产生 deliveries", () => {
    const { state } = makeState();
    const { roomId } = seed(state);
    const entry = send(state, roomId, { message: "hi" });
    expect(deliveriesOf(readLog(state, roomId), entry.id)).toBeUndefined();
  });

  it("开启后按 pending → processing → completed 推进", () => {
    const { state } = makeState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const { roomId } = seed(state);
    const entry = send(state, roomId, { message: "hi" });

    const at = (label: string) => {
      const list = deliveriesOf(readLog(state, roomId), entry.id);
      expect(list, label).toBeDefined();
      return list![0].state;
    };

    expect(at("刚落账")).toBe("pending");
    vi.advanceTimersByTime(100);
    expect(at("第一拍")).toBe("processing");
    vi.advanceTimersByTime(100);
    expect(at("第二拍")).toBe("completed");
  });

  it("记录形状对齐 Core 读通口径", () => {
    const { state } = makeState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const { roomId } = seed(state, ["a1"]);
    const entry = send(state, roomId, { message: "hi" });
    const record = deliveriesOf(readLog(state, roomId), entry.id)![0];
    expect(Object.keys(record).sort()).toEqual([
      "blocked_reason",
      "delivery_id",
      "error",
      "recipient_id",
      "state",
    ]);
    expect(record.recipient_id).toBe("a1");
    expect(record.error).toBeNull();
    expect(record.blocked_reason).toBeNull();
    expect(record.delivery_id).not.toBe("");
  });

  it("targets 缺省时覆盖该 room 的全部 agent 成员", () => {
    const { state } = makeState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const { roomId } = seed(state, ["a1", "a2", "a3"]);
    const entry = send(state, roomId, { message: "broadcast" });
    const recipients = deliveriesOf(readLog(state, roomId), entry.id)!
      .map((d) => d.recipient_id)
      .sort();
    expect(recipients).toEqual(["a1", "a2", "a3"]);
  });

  it("targets 指定时只为该 target 建记录", () => {
    const { state } = makeState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const { roomId } = seed(state, ["a1", "a2"]);
    const entry = send(state, roomId, { message: "hi a2", targets: ["a2"] });
    const recipients = deliveriesOf(readLog(state, roomId), entry.id)!.map((d) => d.recipient_id);
    expect(recipients).toEqual(["a2"]);
  });

  it("deliveries 不经 ROOM_LOG_APPENDED 下发，只出现在 room_get_log", () => {
    const { state, events } = makeState({
      simulateDeliveries: true,
      deliveryIntervalMs: 100,
    });
    const { roomId } = seed(state, ["a1"]);
    events.length = 0;
    const entry = send(state, roomId, { message: "hi" });
    vi.advanceTimersByTime(300);
    const appended = events.filter((e) => e.type === EventType.ROOM_LOG_APPENDED);
    expect(appended).toHaveLength(1);
    const emittedEntry = (appended[0].payload as { entry: RoomLogEntryPayload }).entry;
    expect(emittedEntry.id).toBe(entry.id);
    expect(emittedEntry.data.deliveries).toBeUndefined();
    // 读通侧才有
    expect(deliveriesOf(readLog(state, roomId), entry.id)).toBeDefined();
  });

  it("dispose 清掉未到期的推进定时器", () => {
    const { state } = makeState({ simulateDeliveries: true, deliveryIntervalMs: 100 });
    const { roomId } = seed(state);
    const entry = send(state, roomId, { message: "hi" });
    state.dispose();
    vi.advanceTimersByTime(1000);
    expect(deliveriesOf(readLog(state, roomId), entry.id)![0].state).toBe("pending");
  });
});

describe("MockState.emitAgentResponse", () => {
  it("落账一条 agent 消息并广播 agent_response", () => {
    const { state, events } = makeState();
    const { roomId } = seed(state, ["a1"]);
    events.length = 0;

    const outcome = state.emitAgentResponse("a1", roomId, "done!", 3);
    expect(outcome.success).toBe(true);

    const entries = readLog(state, roomId);
    const reply = entries[entries.length - 1];
    expect(reply.author_type).toBe("agent");
    expect(reply.author).toBe("a1");
    expect(reply.data.message).toBe("done!");

    const responses = events.filter((e) => e.type === EventType.AGENT_RESPONSE);
    expect(responses).toHaveLength(1);
    const payload = responses[0].payload;
    expect(payload.agent_id).toBe("a1");
    expect(payload.agent_name).toBe("name-a1");
    expect(payload.content).toBe("done!");
    expect(payload.message_type).toBe("result");
    expect(payload.metadata).toEqual({ iteration: 3, room_id: roomId });
  });

  it("agent 不属于该 room 所属项目时拒绝且不广播", () => {
    const { state, events } = makeState();
    const { roomId } = seed(state, ["a1"]);
    events.length = 0;
    const outcome = state.emitAgentResponse("no-such-agent", roomId, "x");
    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain("agent not in room project");
    expect(events.filter((e) => e.type === EventType.AGENT_RESPONSE)).toHaveLength(0);
  });

  it("room 不存在时拒绝", () => {
    const { state } = makeState();
    const outcome = state.emitAgentResponse("a1", "no-such-room", "x");
    expect(outcome.success).toBe(false);
    expect(outcome.error).toContain("room not found");
  });
});
