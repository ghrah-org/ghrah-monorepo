// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomLogEntryPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "./chat.js";

const pending = (overrides: Record<string, unknown> = {}) => ({
  projectId: "p1",
  roomId: "r1",
  to: "r1",
  content: "hello",
  agentName: "",
  ...overrides,
});
const log = (overrides: Partial<RoomLogEntryPayload> = {}): RoomLogEntryPayload => ({
  id: "e1",
  room_id: "r1",
  seq: 1,
  author: "user",
  author_type: "human",
  timestamp: 1,
  data: { message: "hello" },
  ...overrides,
});

describe("useChatStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("stores project and room scope on pending entries", () => {
    const entry = useChatStore().addPendingEntry(pending());
    expect(entry).toMatchObject({ projectId: "p1", roomId: "r1", pending: true });
  });

  it("echo confirmation only removes the exact project/room pending", () => {
    const store = useChatStore();
    store.addPendingEntry(pending());
    store.addPendingEntry(pending({ projectId: "p2" }));
    store.onRoomLogAppended(log(), "p1");
    expect(store.entries).toHaveLength(1);
    expect(store.entries[0].projectId).toBe("p2");
  });

  it("marks only the exact pending entry as failed", () => {
    const store = useChatStore();
    store.addPendingEntry(pending());
    store.addPendingEntry(pending({ roomId: "r2", to: "r2" }));
    store.markPendingError({ projectId: "p1", roomId: "r1" }, "hello", "failed");
    expect(store.entries[0]).toMatchObject({ pending: false, error: "failed" });
    expect(store.entries[1].pending).toBe(true);
  });

  it("clears room, agent, and project temporary state precisely", () => {
    const store = useChatStore();
    store.addPendingEntry(pending({ agentId: "a1" }));
    store.addPendingEntry(pending({ roomId: "r2", to: "r2", agentId: "a2" }));
    store.addPendingEntry(pending({ projectId: "p2", agentId: "a1" }));
    store.clearAgent({ projectId: "p1", agentId: "a1", agentName: "coder" });
    expect(store.entries).toHaveLength(2);
    store.clearRoom({ projectId: "p1", roomId: "r2" });
    expect(store.entries.map((entry) => entry.projectId)).toEqual(["p2"]);
    store.clearProject("p2");
    expect(store.entries).toEqual([]);
  });

  it("clearAll resets pending sequence", () => {
    const store = useChatStore();
    expect(store.addPendingEntry(pending()).childSeq).toBe(-1);
    store.clearAll();
    expect(store.addPendingEntry(pending()).childSeq).toBe(-1);
  });
});
