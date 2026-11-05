// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomInfoPayload, RoomLogEntryPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { MAX_CACHED_ROOM_LOGS, useRoomsStore } from "./rooms.js";

const room = (overrides: Partial<RoomInfoPayload> = {}): RoomInfoPayload => ({
  room_id: "r1",
  project_id: "p1",
  name: "room",
  status: "active",
  members: [],
  seq_watermark: 0,
  version: 1,
  created_at: "",
  updated_at: "",
  archived_at: null,
  ...overrides,
});
const entry = (overrides: Partial<RoomLogEntryPayload> = {}): RoomLogEntryPayload => ({
  id: "e1",
  room_id: "r1",
  seq: 1,
  author: "a1",
  author_type: "agent",
  timestamp: 1,
  data: {},
  ...overrides,
});

describe("useRoomsStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("replaces active rooms per project including an exact empty bucket", () => {
    const store = useRoomsStore();
    store.replaceProjectRooms("p1", [room()], "active");
    store.replaceProjectRooms("p2", [room({ project_id: "p2", room_id: "r2" })], "active");
    store.replaceProjectRooms("p1", [], "active");
    expect(store.roomsForProject("p1")).toEqual([]);
    expect(store.roomsForProject("p2")).toHaveLength(1);
  });

  it("separates active and archived rooms through lifecycle events", () => {
    const store = useRoomsStore();
    store.onRoomCreated({ room: room() });
    store.onRoomArchived({ room: room({ status: "archived", archived_at: "now" }) });
    expect(store.roomList).toEqual([]);
    expect(store.archivedRoomList).toHaveLength(1);
    store.onRoomRestored({ room: room() }, true);
    expect(store.roomList).toHaveLength(1);
    expect(store.archivedRoomList).toEqual([]);
  });

  it("keeps active and archived snapshot buckets disjoint", () => {
    const store = useRoomsStore();
    store.replaceProjectRooms("p1", [room({ status: "archived", archived_at: "now" })], "archived");
    store.replaceProjectRooms("p1", [room()], "active");
    expect(store.archivedRooms.has("r1")).toBe(false);
    const removed = store.replaceProjectRooms(
      "p1",
      [room({ status: "archived", archived_at: "later" })],
      "archived",
    );
    expect(store.activeRooms.has("r1")).toBe(false);
    expect(removed).toEqual([{ projectId: "p1", roomId: "r1" }]);
  });

  it("does not restore a Room under an inoperable Project", () => {
    const store = useRoomsStore();
    store.onRoomArchived({ room: room({ status: "archived" }) });
    store.onRoomRestored({ room: room() }, false);
    expect(store.roomList).toEqual([]);
    expect(store.archivedRoomList).toHaveLength(1);
  });

  it("archive clears active selection while retaining the entity", () => {
    const store = useRoomsStore();
    store.onRoomCreated({ room: room() });
    store.setActiveRoom("r1");
    store.onRoomArchived({ room: room({ status: "archived" }) });
    expect(store.activeRoomId).toBeNull();
    expect(store.archivedRooms.has("r1")).toBe(true);
  });

  it("deletes room and log with matching project scope", () => {
    const store = useRoomsStore();
    store.onRoomCreated({ room: room() });
    store.setRoomLog("r1", [entry()]);
    store.onRoomDeleted({ project_id: "p1", room_id: "r1" });
    expect(store.activeRooms.has("r1")).toBe(false);
    expect(store.logs.has("r1")).toBe(false);
  });

  it("deduplicates and orders Room log replay", () => {
    const store = useRoomsStore();
    store.onRoomLogAppended({ entry: entry({ id: "e2", seq: 2 }) });
    store.onRoomLogAppended({ entry: entry() });
    store.onRoomLogAppended({ entry: entry() });
    expect(store.logs.get("r1")?.map((item) => item.seq)).toEqual([1, 2]);
  });

  it("keeps the LRU cache bounded", () => {
    const store = useRoomsStore();
    for (let index = 0; index < MAX_CACHED_ROOM_LOGS + 2; index++) {
      store.setRoomLog(`r${index}`, [entry({ room_id: `r${index}` })]);
    }
    expect(store.logs.size).toBe(MAX_CACHED_ROOM_LOGS);
    expect(store.logs.has("r0")).toBe(false);
  });

  it("clears all Room entities and logs for one Project", () => {
    const store = useRoomsStore();
    store.onRoomCreated({ room: room() });
    store.onRoomCreated({ room: room({ project_id: "p2", room_id: "r2" }) });
    store.setRoomLog("r1", [entry()]);
    store.clearProject("p1");
    expect(store.activeRooms.has("r1")).toBe(false);
    expect(store.activeRooms.has("r2")).toBe(true);
    expect(store.logs.has("r1")).toBe(false);
  });
});
