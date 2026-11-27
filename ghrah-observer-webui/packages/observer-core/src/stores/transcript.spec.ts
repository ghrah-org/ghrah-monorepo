// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, it } from "vitest";
import { entry, page, target, turn } from "../test-fixtures/transcript.js";
import { MAX_CACHED_TRANSCRIPTS, MAX_TURN_DETAILS, useTranscriptStore } from "./transcript.js";

beforeEach(() => setActivePinia(createPinia()));

it("opens an empty transcript with a zero incremental cursor", () => {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  expect(store.has(target)).toBe(false);
  store.applyPage(state, page([]), "initial");
  expect(store.has(target)).toBe(true);
  expect(state).toMatchObject({ nextBeforeSeq: null, nextSinceSeq: 0, latestSeq: 0 });
  expect(store.entriesOf(target)).toEqual([]);
});

it("keeps the history and incremental cursors independent through a point update", () => {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  store.applyPage(state, page([entry(3)], { next_before_seq: 3 }), "initial");
  store.applyPage(
    state,
    page([entry(3, { content: "stale duplicate" }), entry(4)], { next_since_seq: 4 }),
    "incremental",
  );
  expect(state).toMatchObject({ nextBeforeSeq: 3, nextSinceSeq: 4, latestSeq: 4 });
  expect(state.entries[0].content).toBe(entry(3).content);

  store.applyPage(state, page([entry(2)], { next_before_seq: 2, latest_seq: 4 }), "older");
  expect(store.replaceEntry(state, entry(3, { content: "point update" }))).toBe(true);
  expect(store.replaceEntry(state, entry(3, { entry_id: "wrong-entry" }))).toBe(false);
  expect(state).toMatchObject({ nextBeforeSeq: 2, nextSinceSeq: 4, latestSeq: 4 });
  expect(store.entriesOf(target).map(({ entry_id, content }) => [entry_id, content])).toEqual([
    ["entry-2", entry(2).content],
    ["entry-3", "point update"],
    ["entry-4", entry(4).content],
  ]);
});

it("replaces the transcript and preserves media, targets, and ability data", () => {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  const media = [{ type: "image", base64: "aGk=", mime_type: "image/png" }];
  store.applyPage(state, page([entry(1)]), "initial");
  store.applyPage(
    state,
    page([
      entry(4, { content_blocks: media, targets: ["agent"] }),
      entry(5, { author_type: "agent", ability: "end_task", node_id: "last-node" }),
    ]),
    "initial",
  );
  expect(store.entriesOf(target).map(({ entry_id }) => entry_id)).toEqual(["entry-4", "entry-5"]);
  expect(store.entriesOf(target)).toMatchObject([
    {
      entry_id: "entry-4",
      content_blocks: media,
      targets: ["agent"],
      message_id: "message-4",
    },
    { entry_id: "entry-5", ability: "end_task", node_id: "last-node" },
  ]);
});

it("keeps the known opener when an older page or detail lacks it", () => {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  const scope = { projectId: target.projectId, agentId: "agent", turnId: "t" };
  const opened = turn();
  store.applyPage(state, page([entry(2)], { turns: [opened] }), "initial");
  store.applyPage(
    state,
    page([entry(1)], { turns: [turn("waiting_hitl", { opener_entry_id: null })] }),
    "older",
  );
  expect(store.turnsOf(target)).toMatchObject([{ status: "open", opener_entry_id: "entry-1" }]);

  store.setTurnDetail(state, scope, {
    turn: turn("closed", { opener_entry_id: null }),
    nodes: [],
  });
  expect(store.turnDetail(scope)?.turn).toMatchObject({
    status: "closed",
    opener_entry_id: "entry-1",
  });
});

it("bounds and clears the transcript cache", () => {
  const store = useTranscriptStore();
  for (let index = 0; index <= MAX_CACHED_TRANSCRIPTS; index += 1) {
    const roomId = `r${index}`;
    const state = store.enterRoom({ ...target, roomId });
    store.applyPage(state, page([entry(1)], { room_id: roomId }), "initial");
  }
  expect(store.rooms.size).toBe(MAX_CACHED_TRANSCRIPTS);
  expect(store.room({ ...target, roomId: "r0" })).toBeUndefined();
  store.clearRoom({ ...target, roomId: "r1" });
  expect(store.room({ ...target, roomId: "r1" })).toBeUndefined();
  expect(store.entriesOf({ ...target, roomId: "r2" })).toHaveLength(1);
  store.clearProject(target.projectId);
  expect(store.rooms.size).toBe(0);
});

it("evicts the least recently used detail without removing the turn card", () => {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  const scope = (index: number) => ({
    projectId: target.projectId,
    agentId: "agent",
    turnId: `t${index}`,
  });
  for (let index = 0; index < MAX_TURN_DETAILS; index += 1) {
    store.setTurnDetail(state, scope(index), {
      turn: turn("closed", { turn_id: scope(index).turnId }),
      nodes: [],
    });
  }
  expect(store.turnDetail(scope(0))).toBeDefined();
  store.setTurnDetail(state, scope(MAX_TURN_DETAILS), {
    turn: turn("closed", { turn_id: scope(MAX_TURN_DETAILS).turnId }),
    nodes: [],
  });
  expect(store.turnDetail(scope(0))).toBeDefined();
  expect(store.turnDetail(scope(1))).toBeUndefined();
  expect(store.turnsOf(target)).toHaveLength(MAX_TURN_DETAILS + 1);
});
