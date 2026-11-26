// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  RoomLogEntryPayload,
  RoomTranscriptView,
  TranscriptEntryView,
  TurnCardView,
  TurnDetailView,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { reactive, shallowRef } from "vue";
import { type AgentKey, roomKey, type RoomTarget } from "../scope.js";
import { MAX_CACHED_ROOM_LOGS, useRoomsStore } from "./rooms.js";

export const MAX_TURN_DETAILS = 10;

export interface TurnTarget extends AgentKey {
  turnId: string;
}

export interface TranscriptRoom extends RoomTarget {
  entries: TranscriptEntryView[];
  turns: Map<string, TurnCardView>;
  nextBeforeSeq: number | null;
  nextSinceSeq: number | null;
  latestSeq: number;
  readAt: string | null;
}

export type TranscriptPageMode = "initial" | "older" | "incremental";

function turnKey({ projectId, agentId, turnId }: TurnTarget): string {
  return JSON.stringify([projectId, agentId, turnId]);
}

export function isActiveTurn(turn: TurnCardView): boolean {
  return turn.status === "open" || turn.status === "waiting_hitl";
}

export function hasActiveDelivery(entry: TranscriptEntryView): boolean {
  return entry.deliveries.some(({ state }) => state === "pending" || state === "processing");
}

function withKnownOpener(incoming: TurnCardView, current?: TurnCardView): TurnCardView {
  if (
    !incoming.opener_entry_id &&
    current?.opener_entry_id &&
    current.opener_message_id === incoming.opener_message_id
  ) {
    return { ...incoming, opener_entry_id: current.opener_entry_id };
  }
  return incoming;
}

function toRoomLogEntry(entry: TranscriptEntryView, roomId: string): RoomLogEntryPayload {
  return {
    id: entry.entry_id,
    room_id: roomId,
    seq: entry.seq,
    author: entry.author,
    author_type: entry.author_type,
    timestamp: entry.timestamp,
    data: {
      message: entry.content,
      content_blocks: entry.content_blocks,
      targets: entry.targets,
      deliveries: entry.deliveries,
      node_id: entry.node_id,
      ability: entry.ability ?? null,
      ...(entry.author_type === "human" && entry.message_id
        ? { mm_message_id: entry.message_id }
        : {}),
    },
  };
}

export const useTranscriptStore = defineStore("ghrah-transcript", () => {
  const rooms = shallowRef(new Map<string, TranscriptRoom>());
  const details = shallowRef(new Map<string, { target: TurnTarget; view: TurnDetailView }>());
  const detailRecency = new Set<string>();
  const roomDirectory = useRoomsStore();

  function room(target: RoomTarget): TranscriptRoom | undefined {
    return rooms.value.get(roomKey(target));
  }

  function enterRoom(target: RoomTarget): TranscriptRoom {
    const key = roomKey(target);
    const next = new Map(rooms.value);
    const existing = next.get(key);
    const state =
      existing ??
      reactive<TranscriptRoom>({
        ...target,
        entries: [],
        turns: new Map(),
        nextBeforeSeq: null,
        nextSinceSeq: null,
        latestSeq: 0,
        readAt: null,
      });
    next.delete(key);
    next.set(key, state);
    if (next.size > MAX_CACHED_ROOM_LOGS) {
      const evicted = [...next.entries()].find(
        ([, cached]) => cached !== state && cached.roomId !== roomDirectory.activeRoomId,
      );
      if (evicted) {
        next.delete(evicted[0]);
        roomDirectory.replaceRoomLog(evicted[1], null);
      }
    }
    rooms.value = next;
    discardUnusedDetails();
    return state;
  }

  function has(target: RoomTarget): boolean {
    return room(target)?.nextSinceSeq != null;
  }

  function entriesOf(target: RoomTarget): readonly TranscriptEntryView[] {
    return room(target)?.entries ?? [];
  }

  function turnsOf(target: RoomTarget): TurnCardView[] {
    return [...(room(target)?.turns.values() ?? [])];
  }

  function applyPage(state: TranscriptRoom, page: RoomTranscriptView, mode: TranscriptPageMode) {
    const advancingFrom = state.nextSinceSeq;
    const entries =
      mode === "initial"
        ? new Map<string, TranscriptEntryView>()
        : new Map(state.entries.map((entry) => [entry.entry_id, entry]));
    for (const entry of page.entries) {
      if (mode === "incremental" && advancingFrom !== null && entry.seq <= advancingFrom) continue;
      entries.set(entry.entry_id, entry);
    }
    state.entries = [...entries.values()].sort((left, right) => left.seq - right.seq);

    const turns = mode === "initial" ? new Map<string, TurnCardView>() : new Map(state.turns);
    for (const incoming of page.turns) {
      const current = turns.get(incoming.turn_id);
      if (mode === "older" && current) {
        turns.set(incoming.turn_id, withKnownOpener(current, incoming));
      } else {
        turns.set(incoming.turn_id, withKnownOpener(incoming, current));
      }
    }
    state.turns = turns;
    if (mode !== "incremental") state.nextBeforeSeq = page.next_before_seq;
    if (mode === "initial") {
      state.nextSinceSeq = page.latest_seq;
      state.latestSeq = page.latest_seq;
      discardUnusedDetails();
    } else {
      if (mode === "incremental" && page.next_since_seq !== null) {
        state.nextSinceSeq = Math.max(advancingFrom ?? 0, page.next_since_seq);
      }
      state.latestSeq = Math.max(state.latestSeq, page.latest_seq);
    }
    state.readAt = page.read_at;
    syncRoomLog(state);
  }

  function replaceEntry(state: TranscriptRoom, entry: TranscriptEntryView): boolean {
    const index = state.entries.findIndex(
      (current) => current.entry_id === entry.entry_id && current.seq === entry.seq,
    );
    if (index < 0) return false;
    state.entries[index] = entry;
    syncRoomLog(state);
    return true;
  }

  function syncRoomLog(state: TranscriptRoom) {
    roomDirectory.replaceRoomLog(
      state,
      state.entries.map((entry) => toRoomLogEntry(entry, state.roomId)),
    );
  }

  function touchDetail(key: string) {
    detailRecency.delete(key);
    detailRecency.add(key);
  }

  function turnDetail(target: TurnTarget): TurnDetailView | undefined {
    const key = turnKey(target);
    const detail = details.value.get(key)?.view;
    if (detail) touchDetail(key);
    return detail;
  }

  function setTurnDetail(state: TranscriptRoom, target: TurnTarget, view: TurnDetailView) {
    const turn = withKnownOpener(view.turn, state.turns.get(target.turnId));
    state.turns.set(target.turnId, turn);
    const key = turnKey(target);
    const next = new Map(details.value);
    next.set(key, { target, view: { turn, nodes: view.nodes } });
    touchDetail(key);
    if (next.size > MAX_TURN_DETAILS) {
      const evicted = detailRecency.values().next().value;
      if (evicted !== undefined) {
        detailRecency.delete(evicted);
        next.delete(evicted);
      }
    }
    details.value = next;
  }

  function discardUnusedDetails() {
    const remaining = new Map(details.value);
    for (const [key, { target }] of remaining) {
      const inUse = [...rooms.value.values()].some(
        (state) =>
          state.projectId === target.projectId &&
          state.turns.get(target.turnId)?.agent_name === target.agentId,
      );
      if (!inUse) {
        remaining.delete(key);
        detailRecency.delete(key);
      }
    }
    if (remaining.size !== details.value.size) details.value = remaining;
  }

  function removeTurn(state: TranscriptRoom, turnId: string) {
    state.turns.delete(turnId);
    discardUnusedDetails();
  }

  function clearRoom(target: RoomTarget) {
    const key = roomKey(target);
    if (!rooms.value.has(key)) return;
    const next = new Map(rooms.value);
    next.delete(key);
    rooms.value = next;
    roomDirectory.replaceRoomLog(target, null);
    discardUnusedDetails();
  }

  function clearAgent(target: AgentKey) {
    for (const state of rooms.value.values()) {
      if (state.projectId !== target.projectId) continue;
      for (const [turnId, turn] of state.turns) {
        if (turn.agent_name === target.agentId) {
          state.turns.delete(turnId);
        }
      }
    }
    discardUnusedDetails();
  }

  function clearProject(projectId: string) {
    const next = new Map(rooms.value);
    for (const [key, state] of rooms.value) {
      if (state.projectId !== projectId) continue;
      next.delete(key);
      roomDirectory.replaceRoomLog(state, null);
    }
    rooms.value = next;
    discardUnusedDetails();
  }

  function clearAll() {
    for (const state of rooms.value.values()) roomDirectory.replaceRoomLog(state, null);
    rooms.value = new Map();
    details.value = new Map();
    detailRecency.clear();
  }

  return {
    rooms,
    room,
    enterRoom,
    has,
    entriesOf,
    turnsOf,
    applyPage,
    replaceEntry,
    turnDetail,
    setTurnDetail,
    removeTurn,
    clearRoom,
    clearAgent,
    clearProject,
    clearAll,
  };
});
