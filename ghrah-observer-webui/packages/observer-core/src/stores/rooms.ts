// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  RoomDeletedEventPayload,
  RoomEventPayload,
  RoomInfoPayload,
  RoomMemberEventPayload,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { RoomTarget } from "../scope.js";

export const useRoomsStore = defineStore("ghrah-rooms", () => {
  const activeRooms = ref<Map<string, RoomInfoPayload>>(new Map());
  const archivedRooms = ref<Map<string, RoomInfoPayload>>(new Map());
  /** Compatibility alias: the shell only consumes active rooms before M4. */
  const rooms = activeRooms;
  const activeRoomId = ref<string | null>(null);

  const roomList = computed(() => [...activeRooms.value.values()]);
  const archivedRoomList = computed(() => [...archivedRooms.value.values()]);
  const activeRoom = computed(() =>
    activeRoomId.value ? (activeRooms.value.get(activeRoomId.value) ?? null) : null,
  );
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
    if (activeRoomId.value === target.roomId) activeRoomId.value = null;
  }

  function clearRoomSelection(target: RoomTarget) {
    const room = activeRooms.value.get(target.roomId) ?? archivedRooms.value.get(target.roomId);
    if (room && room.project_id !== target.projectId) return;
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

  function setActiveRoom(roomId: string | null) {
    activeRoomId.value = roomId !== null && activeRooms.value.has(roomId) ? roomId : null;
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
    if (activeRoomId.value && roomIds.has(activeRoomId.value)) activeRoomId.value = null;
  }

  function clearAll() {
    activeRooms.value = new Map();
    archivedRooms.value = new Map();
    activeRoomId.value = null;
  }

  return {
    rooms,
    activeRooms,
    archivedRooms,
    activeRoomId,
    roomList,
    archivedRoomList,
    activeRoom,
    roomsForProject,
    onRoomCreated,
    onRoomUpdated,
    onRoomArchived,
    onRoomRestored,
    onRoomDeleted,
    onRoomMemberJoined,
    onRoomMemberLeft,
    replaceProjectRooms,
    setActiveRoom,
    clearRoom,
    clearRoomSelection,
    clearProject,
    clearAll,
  };
});
