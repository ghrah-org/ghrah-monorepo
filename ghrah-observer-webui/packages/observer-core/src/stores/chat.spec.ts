// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomLogEntryPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { entry as transcriptEntry } from "../test-fixtures/transcript.js";
import { useChatStore } from "./chat.js";

const room = { projectId: "p1", roomId: "r1" };

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

  it("addPendingEntry carries blocks and prepends the text block", () => {
    const store = useChatStore();
    const entry = store.addPendingEntry(
      pending({
        blocks: [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
      }),
    );
    expect(entry.blocks).toEqual([
      { type: "text", text: "hello" },
      { type: "image", base64: "aGk=", mime_type: "image/png" },
    ]);
  });

  it("image-only pending matches an empty-content echo and clears", () => {
    const store = useChatStore();
    store.addPendingEntry(
      pending({
        content: "",
        blocks: [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
      }),
    );
    store.onRoomLogAppended(
      log({
        data: {
          message: "",
          content_blocks: [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
        },
      }),
      "p1",
    );
    expect(store.entries).toHaveLength(0);
    expect(store.blockLossRooms.has("r1")).toBe(false);
  });

  it("carries a video block through pending and clears on media echo", () => {
    const store = useChatStore();
    const video = {
      type: "video" as const,
      attachment_id: "att-v",
      mime_type: "video/mp4",
      filename: "clip.mp4",
      duration_s: 5,
      width: 640,
      height: 360,
      frames: [{ type: "image" as const, base64: "RlJBTUU=", mime_type: "image/jpeg" }],
    };
    store.addPendingEntry(pending({ content: "", blocks: [video] }));
    expect(store.entries[0].blocks).toEqual([video]);
    store.onRoomLogAppended(log({ data: { message: "", content_blocks: [video] } }), "p1");
    expect(store.entries).toHaveLength(0);
    expect(store.blockLossRooms.has("r1")).toBe(false);
  });

  it("flags block loss when the echo carries no media blocks", () => {
    const store = useChatStore();
    store.addPendingEntry(
      pending({
        content: "",
        blocks: [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
      }),
    );
    store.onRoomLogAppended(log({ data: { message: "" } }), "p1");
    expect(store.entries).toHaveLength(0);
    expect(store.blockLossRooms.has("r1")).toBe(true);
    store.clearBlockLoss("r1");
    expect(store.blockLossRooms.has("r1")).toBe(false);
  });

  it("marks the matching pending with the same media block count", () => {
    const store = useChatStore();
    store.addPendingEntry(
      pending({
        content: "",
        blocks: [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
      }),
    );
    store.addPendingEntry(pending({ content: "" }));
    store.markPendingError({ projectId: "p1", roomId: "r1" }, "", "failed", 1);
    expect(store.entries[0]).toMatchObject({ pending: false, error: "failed" });
    expect(store.entries[1].pending).toBe(true);
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

  describe("transcript reconciliation", () => {
    it.each(["receipt first", "transcript first"])("matches by entry ID with %s", (order) => {
      const store = useChatStore();
      const draft = store.addPendingEntry(pending());
      const received = transcriptEntry(1, { content: "normalized by server" });
      if (order === "receipt first") {
        store.attachServerEntryId(room, draft.clientSeq, received.entry_id);
        expect(store.pendingOf(room)).toMatchObject([{ serverEntryId: received.entry_id }]);
        store.onTranscriptEntry(room, received);
      } else {
        store.onTranscriptEntry(room, received);
        expect(store.pendingOf(room)).toMatchObject([{ clientSeq: draft.clientSeq }]);
        store.attachServerEntryId(room, draft.clientSeq, received.entry_id, received);
      }
      expect(store.pendingOf(room)).toEqual([]);
    });

    it("handles reversed receipts and replay for identical sends in the same Room", () => {
      const store = useChatStore();
      const first = store.addPendingEntry(pending());
      const second = store.addPendingEntry(pending());
      const echo = transcriptEntry(2, { content: second.content });
      store.attachServerEntryId(room, second.clientSeq, echo.entry_id);
      store.attachServerEntryId(room, first.clientSeq, "entry-1");

      store.onTranscriptEntry({ ...room, projectId: "other" }, echo);
      expect(store.pendingOf(room)).toHaveLength(2);
      store.onTranscriptEntry(room, echo);
      store.onTranscriptEntry(room, echo);
      store.attachServerEntryId(room, second.clientSeq, echo.entry_id, echo);
      expect(store.pendingOf(room)).toMatchObject([
        { clientSeq: first.clientSeq, serverEntryId: "entry-1" },
      ]);

      store.onTranscriptEntry(room, transcriptEntry(1, { content: first.content }));
      expect(store.pendingOf(room)).toEqual([]);
    });

    it.each([true, false])(
      "sets the media loss flag when the echo lacks media (%s)",
      (hasMedia) => {
        const store = useChatStore();
        const image = { type: "image" as const, base64: "aGk=", mime_type: "image/png" };
        const draft = store.addPendingEntry(pending({ blocks: [image] }));
        const echo = transcriptEntry(1, { content_blocks: hasMedia ? [image] : null });
        store.attachServerEntryId(room, draft.clientSeq, echo.entry_id);
        store.onTranscriptEntry(room, echo);
        expect(store.pendingOf(room)).toEqual([]);
        expect(store.blockLossRooms.has(room.roomId)).toBe(!hasMedia);
      },
    );

    it("marks only the requested client sequence as failed for identical sends", () => {
      const store = useChatStore();
      const first = store.addPendingEntry(pending());
      const second = store.addPendingEntry(pending());
      store.markPendingErrorBySeq({ ...room, roomId: "other" }, first.clientSeq, "wrong Room");
      store.markPendingErrorBySeq(room, second.clientSeq, "send failed");
      expect(store.entries).toMatchObject([
        { clientSeq: first.clientSeq, pending: true },
        { clientSeq: second.clientSeq, pending: false, error: "send failed" },
      ]);
      expect(store.entries[0].error).toBeUndefined();
    });
  });
});
