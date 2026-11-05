// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  RoomDeletedEventPayload,
  RoomEventPayload,
  RoomInfoPayload,
  RoomLogEntryPayload,
  RoomLogEventPayload,
  RoomMemberEventPayload,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { RoomTarget } from "../scope.js";

/** room log cache limit (least recently used room is evicted first). */
export const MAX_CACHED_ROOM_LOGS = 10;

export const useRoomsStore = defineStore("ghrah-rooms", () => {
  const activeRooms = ref<Map<string, RoomInfoPayload>>(new Map());
  const archivedRooms = ref<Map<string, RoomInfoPayload>>(new Map());
  /** Compatibility alias: the shell only consumes active rooms before M4. */
  const rooms = activeRooms;
  const logs = ref<Map<string, RoomLogEntryPayload[]>>(new Map());
  const activeRoomId = ref<string | null>(null);

  const roomList = computed(() => [...activeRooms.value.values()]);
  const archivedRoomList = computed(() => [...archivedRooms.value.values()]);
  const activeRoom = computed(() =>
    activeRoomId.value ? (activeRooms.value.get(activeRoomId.value) ?? null) : null,
  );
  const activeRoomLog = computed<RoomLogEntryPayload[]>(() => {
    if (!activeRoomId.value) return [];
    return [...(logs.value.get(activeRoomId.value) ?? [])].sort((a, b) => a.seq - b.seq);
  });

  function roomsForProject(projectId: string, status: "active" | "archived" = "active") {
    const source = status === "active" ? activeRooms.value : archivedRooms.value;
    return [...source.values()].filter((room) => room.project_id === projectId);
  }

  function upsertRoom(room: RoomInfoPayload, allowRestore = true) {
    const active = new Map(activeRooms.value);
    const archived = new Map(archivedRooms.value);
    if (room.status === "archived") {
      active.delete(room.room_id);
      archived.set(room.room_id, room);
      if (activeRoomId.value === room.room_id) activeRoomId.value = null;
    } else if (allowRestore) {
      archived.delete(room.room_id);
      active.set(room.room_id, room);
    }
    activeRooms.value = active;
    archivedRooms.value = archived;
  }

  function touchLog(roomId: string) {
    const map = logs.value;
    const list = map.get(roomId);
    if (list !== undefined) {
      map.delete(roomId);
      map.set(roomId, list);
    }
    while (map.size > MAX_CACHED_ROOM_LOGS) {
      let evicted = false;
      for (const key of map.keys()) {
        if (key === activeRoomId.value) continue;
        map.delete(key);
        evicted = true;
        break;
      }
      if (!evicted) break;
    }
  }

  function onRoomCreated(payload: RoomEventPayload) {
    upsertRoom(payload.room);
  }

  function onRoomUpdated(payload: RoomEventPayload) {
    upsertRoom(payload.room);
  }

  function onRoomArchived(payload: RoomEventPayload) {
    upsertRoom({ ...payload.room, status: "archived" });
  }

  function onRoomRestored(payload: RoomEventPayload, projectOperable: boolean) {
    upsertRoom({ ...payload.room, status: "active" }, projectOperable);
  }

  function clearRoom(target: RoomTarget) {
    const room = activeRooms.value.get(target.roomId) ?? archivedRooms.value.get(target.roomId);
    if (room && room.project_id !== target.projectId) return;
    const active = new Map(activeRooms.value);
    const archived = new Map(archivedRooms.value);
    active.delete(target.roomId);
    archived.delete(target.roomId);
    activeRooms.value = active;
    archivedRooms.value = archived;
    logs.value.delete(target.roomId);
    if (activeRoomId.value === target.roomId) activeRoomId.value = null;
  }

  function clearRoomLog(target: RoomTarget) {
    const room = activeRooms.value.get(target.roomId) ?? archivedRooms.value.get(target.roomId);
    if (room && room.project_id !== target.projectId) return;
    logs.value.delete(target.roomId);
    if (activeRoomId.value === target.roomId) activeRoomId.value = null;
  }

  function onRoomDeleted(payload: RoomDeletedEventPayload) {
    clearRoom({ projectId: payload.project_id, roomId: payload.room_id });
  }

  function onRoomMemberJoined(payload: RoomMemberEventPayload) {
    upsertRoom(payload.room);
  }

  function onRoomMemberLeft(payload: RoomMemberEventPayload) {
    upsertRoom(payload.room);
  }

  function onRoomLogAppended(payload: RoomLogEventPayload) {
    const entry = payload.entry;
    if (!entry) return;
    const map = logs.value;
    let list = map.get(entry.room_id);
    if (!list) {
      list = [];
      map.set(entry.room_id, list);
    }
    if (list.some((existing) => existing.id === entry.id)) return;
    let index = list.length;
    while (index > 0 && list[index - 1].seq > entry.seq) index -= 1;
    list.splice(index, 0, entry);
    touchLog(entry.room_id);
  }

  function replaceProjectRooms(
    projectId: string,
    list: RoomInfoPayload[],
    status: "active" | "archived",
  ): RoomTarget[] {
    const previousActive = roomsForProject(projectId, "active");
    const source = status === "active" ? activeRooms.value : archivedRooms.value;
    const next = new Map([...source.entries()].filter(([, room]) => room.project_id !== projectId));
    const accepted = list.filter((room) => room.project_id === projectId && room.status === status);
    for (const room of accepted) next.set(room.room_id, room);
    if (status === "active") {
      activeRooms.value = next;
      const archived = new Map(archivedRooms.value);
      for (const room of accepted) archived.delete(room.room_id);
      archivedRooms.value = archived;
    } else {
      archivedRooms.value = next;
      const active = new Map(activeRooms.value);
      for (const room of accepted) active.delete(room.room_id);
      activeRooms.value = active;
    }
    const retainedActiveIds = new Set(
      roomsForProject(projectId, "active").map((room) => room.room_id),
    );
    const removed = previousActive
      .filter((room) => !retainedActiveIds.has(room.room_id))
      .map((room) => ({ projectId, roomId: room.room_id }));
    if (activeRoomId.value && removed.some((target) => target.roomId === activeRoomId.value)) {
      activeRoomId.value = null;
    }
    return removed;
  }

  function setRoomLog(roomId: string, entries: RoomLogEntryPayload[]) {
    const existing = logs.value.get(roomId) ?? [];
    const byId = new Map(existing.map((entry) => [entry.id, entry] as const));
    for (const entry of entries) byId.set(entry.id, entry);
    logs.value.set(
      roomId,
      [...byId.values()].sort((a, b) => a.seq - b.seq),
    );
    touchLog(roomId);
  }

  function setActiveRoom(roomId: string | null) {
    activeRoomId.value = roomId !== null && activeRooms.value.has(roomId) ? roomId : null;
    if (activeRoomId.value && logs.value.has(activeRoomId.value)) touchLog(activeRoomId.value);
  }

  function clearProject(projectId: string) {
    const roomIds = new Set([
      ...roomsForProject(projectId, "active").map((room) => room.room_id),
      ...roomsForProject(projectId, "archived").map((room) => room.room_id),
    ]);
    activeRooms.value = new Map(
      [...activeRooms.value.entries()].filter(([, room]) => room.project_id !== projectId),
    );
    archivedRooms.value = new Map(
      [...archivedRooms.value.entries()].filter(([, room]) => room.project_id !== projectId),
    );
    for (const roomId of roomIds) logs.value.delete(roomId);
    if (activeRoomId.value && roomIds.has(activeRoomId.value)) activeRoomId.value = null;
  }

  function clearAll() {
    activeRooms.value = new Map();
    archivedRooms.value = new Map();
    logs.value = new Map();
    activeRoomId.value = null;
  }

  return {
    rooms,
    activeRooms,
    archivedRooms,
    logs,
    activeRoomId,
    roomList,
    archivedRoomList,
    activeRoom,
    activeRoomLog,
    roomsForProject,
    onRoomCreated,
    onRoomUpdated,
    onRoomArchived,
    onRoomRestored,
    onRoomDeleted,
    onRoomMemberJoined,
    onRoomMemberLeft,
    onRoomLogAppended,
    replaceProjectRooms,
    setRoomLog,
    setActiveRoom,
    clearRoom,
    clearRoomLog,
    clearProject,
    clearAll,
  };
});
