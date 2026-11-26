// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomTranscriptView, TurnCardView } from "@ghrah/protocol";
import { watch } from "vue";
import { createHttpAbortController, HttpError } from "./http.js";
import { roomKey, type RoomTarget } from "./scope.js";
import { useChatStore } from "./stores/chat.js";
import {
  hasActiveDelivery,
  isActiveTurn,
  type TranscriptPageMode,
  type TranscriptRoom,
  type TurnTarget,
  useTranscriptStore,
} from "./stores/transcript.js";
import type { TranscriptPageOptions, TurnApiClient } from "./turn-api.js";

const EVENT_DELAY = 150;
const POLL_DELAY = 1000;
const MAX_INCREMENTAL_PAGES = 5;
const MAX_SUPPLEMENTAL_READS = 2;

interface RoomWork {
  state: TranscriptRoom;
  request: ReturnType<typeof createHttpAbortController>;
  tail: Promise<void>;
  turnIds: Set<string>;
  timer?: unknown;
  scheduledAt: number;
  dirty: boolean;
  retry: boolean;
  reset: boolean;
  flushing: boolean;
  unavailable: boolean;
}

export function createTranscriptReader(api: Pick<TurnApiClient, "roomTranscript" | "turnDetail">) {
  const store = useTranscriptStore();
  const chat = useChatStore();
  const workByRoom = new Map<string, RoomWork>();
  const waiting: Array<() => void> = [];
  const timers = globalThis as {
    setTimeout?: (handler: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
  };
  let supplementalReads = 0;
  let online = true;
  let disposed = false;
  let clearUnavailableRoom: (target: RoomTarget) => void = store.clearRoom;

  function current(work: RoomWork): boolean {
    return (
      online && !disposed && !work.request.signal.aborted && store.room(work.state) === work.state
    );
  }

  function cancel(work: RoomWork) {
    if (work.timer !== undefined) timers.clearTimeout?.(work.timer);
    work.request.abort();
    workByRoom.delete(roomKey(work.state));
  }

  const stopWatching = watch(
    () => store.rooms,
    () => {
      for (const work of workByRoom.values()) {
        if (store.room(work.state) !== work.state) cancel(work);
      }
    },
    { flush: "sync" },
  );

  function workFor(target: RoomTarget): RoomWork {
    if (disposed) throw new Error("Room 读调度器已关闭");
    const state = store.enterRoom(target);
    const key = roomKey(target);
    const existing = workByRoom.get(key);
    if (existing) return existing;
    const work: RoomWork = {
      state,
      request: createHttpAbortController(),
      tail: Promise.resolve(),
      turnIds: new Set(),
      scheduledAt: 0,
      dirty: false,
      retry: false,
      reset: state.nextSinceSeq === null,
      flushing: false,
      unavailable: false,
    };
    workByRoom.set(key, work);
    return work;
  }

  function enqueue(work: RoomWork, action: () => Promise<void>): Promise<void> {
    const result = work.tail.then(async () => {
      if (current(work)) await action();
    });
    work.tail = result.catch(() => undefined);
    return result;
  }

  function enqueueSupplement(work: RoomWork, action: () => Promise<void>): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const run = () => {
        supplementalReads += 1;
        void Promise.resolve()
          .then(async () => {
            if (current(work)) await action();
          })
          .then(resolve, reject)
          .finally(() => {
            supplementalReads -= 1;
            waiting.shift()?.();
          });
      };
      if (supplementalReads < MAX_SUPPLEMENTAL_READS) run();
      else waiting.push(run);
    });
  }

  function hasActivity(state: TranscriptRoom): boolean {
    return (
      chat.hasPending(state) ||
      state.entries.some(hasActiveDelivery) ||
      [...state.turns.values()].some(isActiveTurn)
    );
  }

  function schedule(work: RoomWork, delay: number) {
    if (!current(work) || work.unavailable || work.flushing) return;
    const due = Date.now() + delay;
    if (work.timer !== undefined && work.scheduledAt <= due) return;
    if (work.timer !== undefined) timers.clearTimeout?.(work.timer);
    work.scheduledAt = due;
    work.timer = timers.setTimeout?.(() => {
      work.timer = undefined;
      work.flushing = true;
      void enqueue(work, () => refresh(work))
        .catch(() => undefined)
        .finally(() => {
          work.flushing = false;
          scheduleNext(work);
        });
    }, delay);
  }

  function scheduleNext(work: RoomWork) {
    if (work.retry) schedule(work, POLL_DELAY);
    else if (work.dirty) schedule(work, EVENT_DELAY);
    else if (hasActivity(work.state)) schedule(work, POLL_DELAY);
  }

  async function readPage(
    work: RoomWork,
    options: TranscriptPageOptions,
  ): Promise<RoomTranscriptView> {
    const { projectId, roomId } = work.state;
    const page = await api
      .roomTranscript(projectId, roomId, { ...options, signal: work.request.signal })
      .catch((error: unknown) => {
        if (
          options.beforeSeq === undefined &&
          current(work) &&
          error instanceof HttpError &&
          (error.status === 404 || error.status === 409)
        ) {
          clearUnavailableRoom({ projectId, roomId });
        }
        throw error;
      });
    if (page.project_id !== projectId || page.room_id !== roomId) {
      throw new HttpError({
        code: "internal",
        status: 200,
        message: "transcript 响应与请求的 Room 不一致",
      });
    }
    return page;
  }

  function applyPage(work: RoomWork, page: RoomTranscriptView, mode: TranscriptPageMode) {
    store.applyPage(work.state, page, mode);
    for (const entry of page.entries) chat.onTranscriptEntry(work.state, entry);
  }

  async function loadDetail(work: RoomWork, turn: TurnCardView) {
    const target: TurnTarget = {
      projectId: work.state.projectId,
      agentId: turn.agent_name,
      turnId: turn.turn_id,
    };
    try {
      const detail = await api.turnDetail(target.projectId, target.agentId, target.turnId, {
        signal: work.request.signal,
      });
      if (detail.turn.turn_id !== target.turnId || detail.turn.agent_name !== target.agentId) {
        throw new HttpError({
          code: "internal",
          status: 200,
          message: "轮次详情与请求的轮次不一致",
        });
      }
      if (current(work) && work.state.turns.get(target.turnId)?.agent_name === target.agentId) {
        store.setTurnDetail(work.state, target, detail);
      }
    } catch (error) {
      if (!current(work)) return;
      if (error instanceof HttpError && error.status === 404)
        store.removeTurn(work.state, target.turnId);
      else throw error;
    }
  }

  async function supplement(
    work: RoomWork,
    previous: ReadonlyMap<string, TurnCardView>,
    seenEntries: ReadonlySet<string>,
    seenTurns: ReadonlySet<string>,
    invalidated: ReadonlySet<string>,
    manual: boolean,
  ) {
    const reads: Promise<void>[] = [];
    for (const entry of work.state.entries) {
      if (
        seenEntries.has(entry.entry_id) ||
        (!hasActiveDelivery(entry) && !(manual && entry.deliveries.length))
      )
        continue;
      reads.push(
        enqueueSupplement(work, async () => {
          const page = await readPage(work, { beforeSeq: entry.seq + 1, limit: 1 }).catch(
            (error: unknown) => {
              if (error instanceof HttpError && error.status === 404) return null;
              throw error;
            },
          );
          const updated = page?.entries[0];
          if (current(work) && updated?.entry_id === entry.entry_id && updated.seq === entry.seq) {
            store.replaceEntry(work.state, updated);
            chat.onTranscriptEntry(work.state, updated);
          }
        }),
      );
    }

    for (const turn of work.state.turns.values()) {
      const old = previous.get(turn.turn_id);
      const detail = store.turnDetail({
        projectId: work.state.projectId,
        agentId: turn.agent_name,
        turnId: turn.turn_id,
      });
      const change =
        !detail ||
        detail.turn.status !== turn.status ||
        detail.turn.stats.node_count !== turn.stats.node_count;
      if (
        invalidated.has(turn.turn_id) ||
        (old && isActiveTurn(old) && (change || !seenTurns.has(turn.turn_id))) ||
        (isActiveTurn(turn) && change)
      ) {
        reads.push(enqueueSupplement(work, () => loadDetail(work, turn)));
      }
    }
    const results = await Promise.allSettled(reads);
    const failure = results.find((result) => result.status === "rejected");
    if (failure) throw failure.reason;
  }

  async function refresh(work: RoomWork, manual = false) {
    const { state } = work;
    work.dirty = false;
    work.retry = false;
    work.unavailable = false;
    const invalidated = new Set(work.turnIds);
    work.turnIds.clear();
    try {
      if (work.reset || state.nextSinceSeq === null) {
        const page = await readPage(work, { limit: 50 });
        if (!current(work)) return;
        applyPage(work, page, "initial");
        work.reset = false;
        if (page.turns.some(isActiveTurn) || invalidated.size > 0) {
          // 首屏历史已可渲染。详情继续排在同一串行队列中。
          void enqueue(work, () =>
            supplement(
              work,
              new Map(),
              new Set(page.entries.map((entry) => entry.entry_id)),
              new Set(),
              invalidated,
              false,
            ),
          ).catch((error: unknown) => {
            recordRefreshFailure(work, error, invalidated);
            scheduleNext(work);
          });
        }
        return;
      }

      const previous = new Map(state.turns);
      const seenEntries = new Set<string>();
      const seenTurns = new Set<string>();
      for (let count = 0; count < MAX_INCREMENTAL_PAGES; count += 1) {
        const cursor = state.nextSinceSeq;
        if (cursor === null) return;
        const page = await readPage(work, { sinceSeq: cursor, limit: 200 });
        if (!current(work)) return;
        applyPage(work, page, "incremental");
        for (const entry of page.entries) seenEntries.add(entry.entry_id);
        for (const turn of page.turns) seenTurns.add(turn.turn_id);
        if (state.nextSinceSeq === null || state.nextSinceSeq >= page.latest_seq) break;
        if (state.nextSinceSeq <= cursor) {
          throw new HttpError({
            code: "cursor_stalled",
            status: 0,
            message: "transcript 增量游标未前进",
          });
        }
        if (count === MAX_INCREMENTAL_PAGES - 1) work.dirty = true;
      }
      await supplement(work, previous, seenEntries, seenTurns, invalidated, manual);
    } catch (error) {
      recordRefreshFailure(work, error, invalidated);
      throw error;
    }
  }

  function recordRefreshFailure(work: RoomWork, error: unknown, invalidated: ReadonlySet<string>) {
    if (!current(work)) return;
    for (const turnId of invalidated) work.turnIds.add(turnId);
    if (
      error instanceof HttpError &&
      (error.status === 403 || error.code === "capability_unavailable" || error.code === "internal")
    ) {
      work.unavailable = true;
    } else {
      work.retry = true;
    }
  }

  function loadInitial(target: RoomTarget) {
    const work = workFor(target);
    return enqueue(work, async () => {
      if (work.reset || !store.has(target)) await refresh(work);
    }).finally(() => scheduleNext(work));
  }

  function loadOlder(target: RoomTarget) {
    const work = workFor(target);
    return enqueue(work, async () => {
      const cursor = work.state.nextBeforeSeq;
      if (cursor === null) return;
      const page = await readPage(work, { beforeSeq: cursor, limit: 50 });
      if (current(work)) applyPage(work, page, "older");
    }).finally(() => scheduleNext(work));
  }

  function refreshRoom(target: RoomTarget) {
    const work = workFor(target);
    return enqueue(work, () => refresh(work, true)).finally(() => scheduleNext(work));
  }

  function invalidate(target: RoomTarget, turnId?: string) {
    const work = workByRoom.get(roomKey(target));
    if (!work) return;
    if (turnId) work.turnIds.add(turnId);
    work.dirty = true;
    schedule(work, EVENT_DELAY);
  }

  function acknowledgeSend(target: RoomTarget, clientSeq: number, entryId: string) {
    if (disposed) return;
    const entry = store.entriesOf(target).find((candidate) => candidate.entry_id === entryId);
    chat.attachServerEntryId(target, clientSeq, entryId, entry);
    if (!entry) invalidate(target);
  }

  function invalidateAgent(
    projectId: string,
    agentId: string,
    directory: ReadonlyMap<
      string,
      { project_id: string; members: Array<{ subject: string; subject_type: string }> }
    >,
    { turnId, roomId, restart }: { turnId?: string; roomId?: string; restart?: boolean } = {},
  ) {
    if (!projectId || !agentId) return;
    const scoped = [...workByRoom.values()].filter(({ state }) => state.projectId === projectId);
    const known = scoped.filter(
      ({ state }) =>
        (roomId !== undefined && state.roomId === roomId) ||
        (turnId !== undefined && state.turns.get(turnId)?.agent_name === agentId) ||
        (restart && [...state.turns.values()].some((turn) => turn.agent_name === agentId)),
    );
    const targets = new Set(known);
    if (restart || targets.size === 0) {
      for (const work of scoped) {
        const room = directory.get(work.state.roomId);
        if (
          room?.project_id === projectId &&
          room.members.some(
            (member) => member.subject_type === "agent" && member.subject === agentId,
          )
        )
          targets.add(work);
      }
    }
    for (const work of targets) {
      const { state } = work;
      if (restart) {
        cancel(work);
        workFor(state);
      }
      invalidate(state, turnId);
    }
  }

  function pause() {
    online = false;
    for (const work of workByRoom.values()) cancel(work);
  }

  function resume(replace = false) {
    if (disposed) return;
    if (replace) pause();
    online = true;
    if (!replace) return;
    for (const state of store.rooms.values()) {
      const work = workFor(state);
      work.reset = true;
      schedule(work, 0);
    }
  }

  function loadTurnDetail(target: RoomTarget, turnId: string) {
    const work = workByRoom.get(roomKey(target));
    const turn = work?.state.turns.get(turnId);
    if (!work || !turn) return Promise.resolve();
    return enqueue(work, async () => {
      const cached = store.turnDetail({
        projectId: target.projectId,
        agentId: turn.agent_name,
        turnId,
      });
      if (!cached && work.state.turns.has(turnId)) {
        await enqueueSupplement(work, () => loadDetail(work, turn));
      }
    });
  }

  function onRoomUnavailable(handler: (target: RoomTarget) => void) {
    clearUnavailableRoom = handler;
  }

  function dispose() {
    disposed = true;
    pause();
    stopWatching();
    store.clearAll();
    clearUnavailableRoom = store.clearRoom;
  }

  return {
    loadInitial,
    loadOlder,
    refreshRoom,
    loadTurnDetail,
    onRoomUnavailable,
    invalidate,
    invalidateAgent,
    acknowledgeSend,
    pause,
    resume,
    dispose,
  };
}

export type TranscriptReader = ReturnType<typeof createTranscriptReader>;
