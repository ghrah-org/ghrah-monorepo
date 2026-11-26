// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomTranscriptView, TranscriptEntryView, TurnDetailView } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HttpError } from "./http.js";
import { useChatStore } from "./stores/chat.js";
import { MAX_CACHED_ROOM_LOGS, useRoomsStore } from "./stores/rooms.js";
import { useTranscriptStore } from "./stores/transcript.js";
import { deferred, entry, page, target, turn } from "./test-fixtures/transcript.js";
import { createTranscriptReader, type TranscriptReader } from "./transcript-reader.js";
import type { TurnApiClient } from "./turn-api.js";

function processingEntry(seq = 1): TranscriptEntryView {
  return entry(seq, {
    deliveries: [
      {
        delivery_id: "d",
        message_id: `message-${seq}`,
        recipient_id: "agent",
        state: "processing",
        error: null,
        blocked_reason: null,
        turn_id: "t",
        delivery_policy: "queue",
        display_state: "processing",
        queue_position: 0,
        queue_reason: null,
        cancelable: false,
        injection_step: null,
      },
    ],
  });
}

function setup() {
  const roomTranscript = vi.fn<TurnApiClient["roomTranscript"]>();
  const turnDetail = vi.fn<TurnApiClient["turnDetail"]>();
  reader = createTranscriptReader({ roomTranscript, turnDetail });
  return { roomTranscript, turnDetail, reader };
}

let reader: TranscriptReader | undefined;
beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});
afterEach(() => {
  reader?.dispose();
  reader = undefined;
  vi.useRealTimers();
});

it("starts incremental reads at zero in an empty Room and merges a burst of events", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([]))
    .mockResolvedValueOnce(page([entry(1)], { next_since_seq: 1 }));
  await reader.loadInitial(target);
  for (let index = 0; index < 3; index += 1) reader.invalidate(target);
  await vi.advanceTimersByTimeAsync(149);
  expect(roomTranscript).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  expect(roomTranscript).toHaveBeenLastCalledWith(
    "p",
    "r",
    expect.objectContaining({ sinceSeq: 0 }),
  );
  expect(
    useTranscriptStore()
      .entriesOf(target)
      .map((item) => item.entry_id),
  ).toEqual(["entry-1"]);
});

it("makes the initial history available before active turn details arrive", async () => {
  const { reader, roomTranscript, turnDetail } = setup();
  const detail = deferred<TurnDetailView>();
  const turnTarget = { projectId: "p", agentId: "agent", turnId: "t" };
  roomTranscript.mockResolvedValueOnce(page([entry(1)], { turns: [turn()] }));
  turnDetail.mockReturnValueOnce(detail.promise);
  const ready = vi.fn();
  const entering = reader.loadInitial(target).then(ready);
  await vi.advanceTimersByTimeAsync(0);
  expect(ready).toHaveBeenCalledOnce();
  expect(useRoomsStore().logs.get("r")?.[0].id).toBe("entry-1");
  expect(useTranscriptStore().turnDetail(turnTarget)).toBeUndefined();
  detail.resolve({ turn: turn(), nodes: [] });
  await entering;
  await vi.advanceTimersByTimeAsync(0);
  expect(useTranscriptStore().turnDetail(turnTarget)?.turn.turn_id).toBe("t");
});

it("drains incremental pages without replacing the earlier cursor and loads more history", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([entry(3)], { next_before_seq: 3, has_more: true }))
    .mockResolvedValueOnce(page([entry(4)], { next_since_seq: 4, latest_seq: 5, has_more: true }))
    .mockResolvedValueOnce(page([entry(5)], { next_since_seq: 5 }))
    .mockResolvedValueOnce(page([entry(1), entry(2)], { latest_seq: 5 }));
  await reader.loadInitial(target);
  await reader.refreshRoom(target);
  await reader.loadOlder(target);
  expect(
    roomTranscript.mock.calls.map(([, , options]) => [options?.beforeSeq, options?.sinceSeq]),
  ).toEqual([
    [undefined, undefined],
    [undefined, 3],
    [undefined, 4],
    [3, undefined],
  ]);
  expect(useTranscriptStore().room(target)).toMatchObject({ nextSinceSeq: 5, nextBeforeSeq: null });
  expect(
    useTranscriptStore()
      .entriesOf(target)
      .map((item) => item.seq),
  ).toEqual([1, 2, 3, 4, 5]);
});

it("continues from the committed cursor after reaching the catch-up batch limit", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([]))
    .mockImplementation(async (_projectId, _roomId, options) => {
      const seq = (options?.sinceSeq ?? 0) + 1;
      return page([entry(seq)], { next_since_seq: seq, latest_seq: 6, has_more: seq < 6 });
    });
  await reader.loadInitial(target);
  reader.invalidate(target);
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenCalledTimes(6);
  expect(useTranscriptStore().room(target)?.nextSinceSeq).toBe(5);
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenLastCalledWith(
    "p",
    "r",
    expect.objectContaining({ sinceSeq: 5 }),
  );
  expect(useTranscriptStore().entriesOf(target)).toHaveLength(6);
});

it("stops a stalled incremental cycle and retries from the committed cursor", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([entry(1)]))
    .mockResolvedValueOnce(page([], { next_since_seq: 1, latest_seq: 2, has_more: true }))
    .mockResolvedValueOnce(page([entry(2)], { next_since_seq: 2 }));
  await reader.loadInitial(target);

  await expect(reader.refreshRoom(target)).rejects.toMatchObject({ code: "cursor_stalled" });
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  expect(useTranscriptStore().room(target)?.nextSinceSeq).toBe(1);
  expect(
    useRoomsStore()
      .logs.get(target.roomId)
      ?.map(({ id }) => id),
  ).toEqual(["entry-1"]);

  await vi.advanceTimersByTimeAsync(999);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(roomTranscript.mock.calls.map(([, , options]) => options?.sinceSeq)).toEqual([
    undefined,
    1,
    1,
  ]);
  expect(useTranscriptStore().room(target)?.nextSinceSeq).toBe(2);
  expect(
    useRoomsStore()
      .logs.get(target.roomId)
      ?.map(({ id }) => id),
  ).toEqual(["entry-1", "entry-2"]);
});

it.each([
  ["completed", "closed"],
  ["failed", "failed"],
])(
  "reads %s delivery and %s turn when the incremental page is empty",
  async (deliveryState, turnStatus) => {
    const { reader, roomTranscript, turnDetail } = setup();
    const live = processingEntry();
    const done = entry(1, {
      deliveries: [{ ...live.deliveries[0], state: deliveryState, display_state: deliveryState }],
    });
    roomTranscript
      .mockResolvedValueOnce(page([live], { turns: [turn()] }))
      .mockResolvedValueOnce(page([], { latest_seq: 1, next_since_seq: 1 }))
      .mockResolvedValueOnce(page([done], { turns: [turn(turnStatus)] }));
    turnDetail
      .mockResolvedValueOnce({ turn: turn(), nodes: [] })
      .mockResolvedValueOnce({ turn: turn(turnStatus, { opener_entry_id: null }), nodes: [] });
    await reader.loadInitial(target);
    await vi.advanceTimersByTimeAsync(1000);
    expect(roomTranscript).toHaveBeenLastCalledWith(
      "p",
      "r",
      expect.objectContaining({ beforeSeq: 2, limit: 1 }),
    );
    expect(useTranscriptStore().entriesOf(target)[0].deliveries[0].state).toBe(deliveryState);
    expect(useRoomsStore().logs.get(target.roomId)?.[0].data.deliveries).toMatchObject([
      { state: deliveryState },
    ]);
    expect(useTranscriptStore().turnsOf(target)).toMatchObject([
      { status: turnStatus, opener_entry_id: "entry-1" },
    ]);
    expect(vi.getTimerCount()).toBe(0);
  },
);

it.each(["manual", "timer"])(
  "keeps the transcript and mirror on a %s delivery reread 404",
  async (source) => {
    const { reader, roomTranscript } = setup();
    const unavailable = vi.fn();
    reader.onRoomUnavailable(unavailable);
    roomTranscript
      .mockResolvedValueOnce(page([entry(1), processingEntry(2)]))
      .mockResolvedValueOnce(page([], { latest_seq: 2, next_since_seq: 2 }))
      .mockRejectedValueOnce(
        new HttpError({ status: 404, code: "not_found", message: "entry not found" }),
      );

    await reader.loadInitial(target);
    if (source === "manual") await expect(reader.refreshRoom(target)).resolves.toBeUndefined();
    else await vi.advanceTimersByTimeAsync(1000);

    expect(roomTranscript).toHaveBeenLastCalledWith(
      "p",
      "r",
      expect.objectContaining({ beforeSeq: 3, limit: 1 }),
    );
    expect(unavailable).not.toHaveBeenCalled();
    expect(useTranscriptStore().entriesOf(target)).toMatchObject([
      { entry_id: "entry-1" },
      { entry_id: "entry-2" },
    ]);
    expect(useRoomsStore().logs.get(target.roomId)).toMatchObject([
      { id: "entry-1" },
      { id: "entry-2" },
    ]);
  },
);

it("drops only the missing turn when its detail returns 404", async () => {
  const { reader, roomTranscript, turnDetail } = setup();
  const active = turn();
  const closed = turn("closed", { turn_id: "keep" });
  const scope = { projectId: target.projectId, agentId: "agent", turnId: active.turn_id };
  const keepScope = { ...scope, turnId: closed.turn_id };
  roomTranscript
    .mockResolvedValueOnce(page([entry(1)], { turns: [active, closed] }))
    .mockResolvedValueOnce(page([], { latest_seq: 1, next_since_seq: 1 }));
  turnDetail
    .mockResolvedValueOnce({ turn: active, nodes: [] })
    .mockResolvedValueOnce({ turn: closed, nodes: [] })
    .mockRejectedValueOnce(
      new HttpError({ status: 404, code: "not_found", message: "turn missing" }),
    );

  await reader.loadInitial(target);
  await reader.loadTurnDetail(target, closed.turn_id);
  expect(useTranscriptStore().turnDetail(scope)).toBeDefined();
  expect(useTranscriptStore().turnDetail(keepScope)).toBeDefined();
  const unavailable = vi.fn();
  reader.onRoomUnavailable(unavailable);

  await reader.refreshRoom(target);
  expect(
    useTranscriptStore()
      .turnsOf(target)
      .map(({ turn_id }) => turn_id),
  ).toEqual(["keep"]);
  expect(useTranscriptStore().turnDetail(scope)).toBeUndefined();
  expect(useTranscriptStore().turnDetail(keepScope)).toBeDefined();
  expect(
    useRoomsStore()
      .logs.get(target.roomId)
      ?.map(({ id }) => id),
  ).toEqual(["entry-1"]);
  expect(unavailable).not.toHaveBeenCalled();
});

it("retains a dirty event during an in-flight read and ignores a late result after clear", async () => {
  const { reader, roomTranscript } = setup();
  const first = deferred<RoomTranscriptView>();
  const second = deferred<RoomTranscriptView>();
  roomTranscript.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const loading = reader.loadInitial(target);
  await vi.advanceTimersByTimeAsync(0);
  reader.invalidate(target);
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenCalledTimes(1);
  first.resolve(page([]));
  await loading;
  await vi.advanceTimersByTimeAsync(0);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  useTranscriptStore().clearRoom(target);
  second.resolve(page([entry(1)], { next_since_seq: 1 }));
  await vi.advanceTimersByTimeAsync(0);
  expect(useTranscriptStore().room(target)).toBeUndefined();
  expect(vi.getTimerCount()).toBe(0);
});

it("ignores the late page of an evicted Room", async () => {
  const { reader, roomTranscript } = setup();
  const pending = deferred<RoomTranscriptView>();
  roomTranscript.mockResolvedValueOnce(page([entry(1)])).mockReturnValueOnce(pending.promise);
  await reader.loadInitial(target);
  const refreshing = reader.refreshRoom(target);
  await vi.advanceTimersByTimeAsync(0);
  const signal = roomTranscript.mock.calls[1][2]?.signal;
  expect(signal?.aborted).toBe(false);

  const store = useTranscriptStore();
  for (let index = 0; index < MAX_CACHED_ROOM_LOGS; index += 1) {
    store.enterRoom({ ...target, roomId: `other-${index}` });
  }
  expect(signal?.aborted).toBe(true);
  pending.resolve(page([entry(2)], { next_since_seq: 2 }));
  await refreshing;
  expect(store.room(target)).toBeUndefined();
  expect(useRoomsStore().logs.has(target.roomId)).toBe(false);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
});

it("routes the first Agent turn to loaded member Rooms and rejects responses from a cleared Agent read", async () => {
  const { reader, roomTranscript } = setup();
  const pending = deferred<RoomTranscriptView>();
  const directory = new Map([
    [
      target.roomId,
      {
        project_id: target.projectId,
        members: [{ subject: "agent", subject_type: "agent" }],
      },
    ],
  ]);
  roomTranscript
    .mockResolvedValueOnce(page([]))
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(page([], { next_since_seq: 0 }));
  await reader.loadInitial(target);
  reader.invalidateAgent("foreign", "agent", directory, { turnId: "t" });
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenCalledTimes(1);

  reader.invalidateAgent(target.projectId, "agent", directory, { turnId: "t" });
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  reader.invalidateAgent(target.projectId, "agent", directory, { restart: true });
  useTranscriptStore().clearAgent({ projectId: target.projectId, agentId: "agent" });
  expect(roomTranscript.mock.calls[1][2]?.signal?.aborted).toBe(true);
  pending.resolve(page([entry(1)], { next_since_seq: 1, turns: [turn()] }));
  await vi.advanceTimersByTimeAsync(150);
  expect(roomTranscript).toHaveBeenCalledTimes(3);
  expect(useTranscriptStore().turnsOf(target)).toEqual([]);
  expect(useTranscriptStore().entriesOf(target)).toEqual([]);
});

it("matches identical optimistic sends in either arrival order using the log IDs", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([]))
    .mockResolvedValueOnce(page([entry(1), entry(2)], { next_since_seq: 2 }));
  await reader.loadInitial(target);
  const chat = useChatStore();
  const send = { ...target, to: "r", content: "同一条文字", agentName: "" };
  const one = chat.addPendingEntry(send);
  const two = chat.addPendingEntry(send);
  reader.acknowledgeSend(target, two.clientSeq, "entry-2");
  await reader.refreshRoom(target);
  expect(chat.pendingOf(target).map((item) => item.clientSeq)).toEqual([one.clientSeq]);
  reader.acknowledgeSend(target, one.clientSeq, "entry-1");
  expect(chat.pendingOf(target)).toEqual([]);
  expect(useTranscriptStore().entriesOf(target)).toHaveLength(2);
});

it("retries an idle Room after a failed event read and resets from a fresh tail on reconnect", async () => {
  const { reader, roomTranscript } = setup();
  roomTranscript
    .mockResolvedValueOnce(page([entry(1)]))
    .mockRejectedValueOnce(new Error("read failed"))
    .mockResolvedValueOnce(page([entry(2)], { next_since_seq: 2 }))
    .mockResolvedValueOnce(page([entry(4)], { next_before_seq: 4, has_more: true }));
  await reader.loadInitial(target);
  reader.invalidate(target);
  await vi.advanceTimersByTimeAsync(150);
  await vi.advanceTimersByTimeAsync(999);
  expect(roomTranscript).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(useTranscriptStore().room(target)?.nextSinceSeq).toBe(2);

  reader.pause();
  reader.resume(true);
  await vi.advanceTimersByTimeAsync(0);
  expect(roomTranscript).toHaveBeenLastCalledWith("p", "r", expect.objectContaining({ limit: 50 }));
  expect(
    useTranscriptStore()
      .entriesOf(target)
      .map(({ seq }) => seq),
  ).toEqual([4]);
  expect(
    useRoomsStore()
      .logs.get(target.roomId)
      ?.map(({ seq }) => seq),
  ).toEqual([4]);
  expect(useTranscriptStore().room(target)).toMatchObject({ nextSinceSeq: 4, nextBeforeSeq: 4 });
});
