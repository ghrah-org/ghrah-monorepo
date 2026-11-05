// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomLogEntryPayload } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import type { ChatEntry } from "../projection.js";
import type { AgentTarget, RoomTarget } from "../scope.js";

export const DEFAULT_HUMAN_AUTHOR = "user";

export const useChatStore = defineStore("ghrah-chat", () => {
  const entries = ref<ChatEntry[]>([]);
  const currentAuthor = ref<string>(DEFAULT_HUMAN_AUTHOR);
  let pendingSeq = 0;

  function addPendingEntry(partial: {
    projectId: string;
    to: string;
    content: string;
    agentName: string;
    agentId?: string;
    roomId: string;
    targets?: string[];
  }): ChatEntry {
    const seq = ++pendingSeq;
    const entry: ChatEntry = {
      projectId: partial.projectId,
      agentId: partial.agentId,
      from: currentAuthor.value,
      to: partial.to,
      content: partial.content,
      kind: "human_input",
      timestamp: new Date().toISOString(),
      nodeId: "",
      agentName: partial.agentName,
      childSeq: -seq,
      roomId: partial.roomId,
      targets: partial.targets?.length ? partial.targets : undefined,
      pending: true,
    };
    entries.value = [...entries.value, entry];
    return entry;
  }

  function entryContent(entry: RoomLogEntryPayload): string {
    const message = entry.data?.message;
    return typeof message === "string" ? message : JSON.stringify(entry.data ?? {});
  }

  function onRoomLogAppended(entry: RoomLogEntryPayload, projectId: string) {
    if (entry.author_type !== "human" || entry.author !== currentAuthor.value) return;
    const content = entryContent(entry);
    const index = entries.value.findIndex(
      (pending) =>
        pending.pending === true &&
        pending.projectId === projectId &&
        pending.roomId === entry.room_id &&
        pending.content === content,
    );
    if (index < 0) return;
    const next = [...entries.value];
    next.splice(index, 1);
    entries.value = next;
  }

  function markPendingError(target: RoomTarget, content: string, error: string) {
    const index = entries.value.findIndex(
      (pending) =>
        pending.pending === true &&
        pending.projectId === target.projectId &&
        pending.roomId === target.roomId &&
        pending.content === content,
    );
    if (index < 0) return;
    const next = [...entries.value];
    next[index] = { ...next[index], pending: false, error };
    entries.value = next;
  }

  const allEntries = computed<ChatEntry[]>(() => entries.value);

  function setCurrentAuthor(author: string) {
    currentAuthor.value = author;
  }

  function clearAgent(target: AgentTarget) {
    entries.value = entries.value.filter(
      (entry) => entry.projectId !== target.projectId || entry.agentId !== target.agentId,
    );
  }

  function clearRoom(target: RoomTarget) {
    entries.value = entries.value.filter(
      (entry) => entry.projectId !== target.projectId || entry.roomId !== target.roomId,
    );
  }

  function clearProject(projectId: string) {
    entries.value = entries.value.filter((entry) => entry.projectId !== projectId);
  }

  function clearAll() {
    entries.value = [];
    pendingSeq = 0;
  }

  return {
    entries,
    allEntries,
    currentAuthor,
    setCurrentAuthor,
    addPendingEntry,
    onRoomLogAppended,
    markPendingError,
    clearAgent,
    clearRoom,
    clearProject,
    clearAll,
  };
});
