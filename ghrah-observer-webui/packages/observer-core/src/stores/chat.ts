// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomLogEntryPayload } from "@ghrah/protocol";
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

export const useChatStore = defineStore("ghrah-chat", () => {
  const entries = ref<ChatEntry[]>([]);
  const currentAuthor = ref<string>(DEFAULT_HUMAN_AUTHOR);
  /** 服务端回读确认时没带回我们发出的媒体块 → 提示「图片可能被旧核丢弃」。 */
  const blockLossRooms = ref<Set<string>>(new Set());
  let pendingSeq = 0;

  function addPendingEntry(partial: {
    projectId: string;
    to: string;
    content: string;
    agentName: string;
    agentId?: string;
    roomId: string;
    targets?: string[];
    blocks?: ChatBlock[];
  }): ChatEntry {
    const seq = ++pendingSeq;
    const blocks = withTextContent(partial.blocks, partial.content);
    const entry: ChatEntry = {
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
      childSeq: -seq,
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
    if (index < 0) return;
    const pending = entries.value[index];
    const sentMedia = pending.blocks?.some((block) => block.type !== "text") === true;
    const incoming = contentBlocksFromData(entry.data ?? {});
    const echoedMedia = incoming?.some((block) => block.type !== "text") === true;
    if (sentMedia && !echoedMedia) {
      const next = new Set(blockLossRooms.value);
      next.add(entry.room_id);
      blockLossRooms.value = next;
    }
    const next = [...entries.value];
    next.splice(index, 1);
    entries.value = next;
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
    blockLossRooms,
    setCurrentAuthor,
    addPendingEntry,
    onRoomLogAppended,
    clearBlockLoss,
    markPendingError,
    clearAgent,
    clearRoom,
    clearProject,
    clearAll,
  };
});
