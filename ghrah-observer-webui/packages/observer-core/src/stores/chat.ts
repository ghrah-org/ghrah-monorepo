// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomLogEntryPayload, TranscriptEntryView } from "@ghrah/protocol";
import type { ChatBlock } from "../block-compat.js";
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import {
  type ChatEntry,
  contentBlocksFromData,
  textFromBlocks,
  withTextContent,
} from "../projection.js";
import type { AgentTarget, RoomTarget } from "../scope.js";

export const DEFAULT_HUMAN_AUTHOR = "user";

export interface OptimisticChatEntry extends ChatEntry {
  projectId: string;
  roomId: string;
  clientSeq: number;
  serverEntryId?: string;
}

export const useChatStore = defineStore("ghrah-chat", () => {
  const entries = ref<OptimisticChatEntry[]>([]);
  const currentAuthor = ref<string>(DEFAULT_HUMAN_AUTHOR);
  /** 服务端回读确认时没带回我们发出的媒体块 → 提示「图片可能被旧核丢弃」。 */
  const blockLossRooms = ref<Set<string>>(new Set());
  // 清空列表后，迟到的回执仍可能携带已经分配过的 clientSeq。
  let lastClientSeq = 0;
  let pendingChildSeq = 0;

  function addPendingEntry(partial: {
    projectId: string;
    to: string;
    content: string;
    agentName: string;
    agentId?: string;
    roomId: string;
    targets?: string[];
    blocks?: ChatBlock[];
  }): OptimisticChatEntry {
    const clientSeq = ++lastClientSeq;
    const blocks = withTextContent(partial.blocks, partial.content);
    const entry: OptimisticChatEntry = {
      projectId: partial.projectId,
      agentId: partial.agentId,
      from: currentAuthor.value,
      to: partial.to,
      content: partial.content,
      blocks: blocks && blocks.length > 0 ? blocks : undefined,
      kind: "human_input",
      timestamp: new Date().toISOString(),
      nodeId: "",
      agentName: partial.agentName,
      childSeq: --pendingChildSeq,
      clientSeq,
      roomId: partial.roomId,
      targets: partial.targets?.length ? partial.targets : undefined,
      pending: true,
    };
    entries.value = [...entries.value, entry];
    return entry;
  }

  function entryContent(entry: RoomLogEntryPayload): string {
    const data = entry.data ?? {};
    const message = data.message;
    if (typeof message === "string" && message) return message;
    const blocks = contentBlocksFromData(data);
    if (blocks) return textFromBlocks(blocks);
    return typeof message === "string" ? message : JSON.stringify(data);
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
    if (index >= 0) acknowledgePending(index, entry.data);
  }

  function acknowledgePending(index: number, data: Record<string, unknown>) {
    const pending = entries.value[index];
    const sentMedia = pending.blocks?.some((block) => block.type !== "text") === true;
    const incoming = contentBlocksFromData(data);
    const echoedMedia = incoming?.some((block) => block.type !== "text") === true;
    if (sentMedia && !echoedMedia) {
      const next = new Set(blockLossRooms.value);
      next.add(pending.roomId);
      blockLossRooms.value = next;
    }
    const next = [...entries.value];
    next.splice(index, 1);
    entries.value = next;
  }

  function pendingForRoom(entry: OptimisticChatEntry, target: RoomTarget): boolean {
    return (
      entry.pending === true &&
      entry.projectId === target.projectId &&
      entry.roomId === target.roomId
    );
  }

  function pendingOf(target: RoomTarget): OptimisticChatEntry[] {
    return entries.value.filter((entry) => pendingForRoom(entry, target));
  }

  function hasPending(target: RoomTarget): boolean {
    return entries.value.some((entry) => pendingForRoom(entry, target));
  }

  function attachServerEntryId(
    target: RoomTarget,
    clientSeq: number,
    entryId: string,
    received?: TranscriptEntryView,
  ): void {
    const index = entries.value.findIndex(
      (entry) => entry.clientSeq === clientSeq && pendingForRoom(entry, target),
    );
    if (index < 0) return;
    if (received?.entry_id === entryId) {
      acknowledgePending(index, { content_blocks: received.content_blocks });
      return;
    }
    entries.value[index] = { ...entries.value[index], serverEntryId: entryId };
  }

  function onTranscriptEntry(target: RoomTarget, received: TranscriptEntryView): void {
    if (received.author_type !== "human") return;
    const index = entries.value.findIndex(
      (entry) => entry.serverEntryId === received.entry_id && pendingForRoom(entry, target),
    );
    if (index >= 0) acknowledgePending(index, { content_blocks: received.content_blocks });
  }

  function clearBlockLoss(roomId?: string) {
    if (roomId === undefined) {
      if (blockLossRooms.value.size === 0) return;
      blockLossRooms.value = new Set();
      return;
    }
    if (!blockLossRooms.value.has(roomId)) return;
    const next = new Set(blockLossRooms.value);
    next.delete(roomId);
    blockLossRooms.value = next;
  }

  function markPendingError(
    target: RoomTarget,
    content: string,
    error: string,
    mediaBlockCount?: number,
  ) {
    const index = entries.value.findIndex(
      (pending) =>
        pending.pending === true &&
        pending.projectId === target.projectId &&
        pending.roomId === target.roomId &&
        pending.content === content &&
        (mediaBlockCount === undefined ||
          (pending.blocks?.filter((block) => block.type !== "text").length ?? 0) ===
            mediaBlockCount),
    );
    if (index >= 0) failPending(index, error);
  }

  function markPendingErrorBySeq(target: RoomTarget, clientSeq: number, error: string) {
    const index = entries.value.findIndex(
      (entry) => entry.clientSeq === clientSeq && pendingForRoom(entry, target),
    );
    if (index >= 0) failPending(index, error);
  }

  function failPending(index: number, error: string) {
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
    pendingChildSeq = 0;
  }

  return {
    entries,
    allEntries,
    currentAuthor,
    blockLossRooms,
    setCurrentAuthor,
    addPendingEntry,
    onRoomLogAppended,
    onTranscriptEntry,
    attachServerEntryId,
    pendingOf,
    hasPending,
    clearBlockLoss,
    markPendingError,
    markPendingErrorBySeq,
    clearAgent,
    clearRoom,
    clearProject,
    clearAll,
  };
});
