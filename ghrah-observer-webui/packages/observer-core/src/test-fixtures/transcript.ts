// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { RoomTranscriptView, TranscriptEntryView, TurnCardView } from "@ghrah/protocol";

export const target = { projectId: "p", roomId: "r" };
const now = "2026-11-26T00:00:00Z";

export function entry(
  seq: number,
  overrides: Partial<TranscriptEntryView> = {},
): TranscriptEntryView {
  return {
    entry_id: `entry-${seq}`,
    seq,
    author: "user",
    author_type: "human",
    timestamp: seq,
    message_id: `message-${seq}`,
    content: "同一条文字",
    content_blocks: null,
    targets: [],
    deliveries: [],
    turn_id: null,
    insert_before_turn_id: null,
    injection_step: null,
    human_state: null,
    node_id: null,
    ...overrides,
  };
}

export function page(
  entries: TranscriptEntryView[],
  overrides: Partial<RoomTranscriptView> = {},
): RoomTranscriptView {
  return {
    project_id: target.projectId,
    room_id: target.roomId,
    entries,
    turns: [],
    next_before_seq: null,
    next_since_seq: null,
    latest_seq: entries.at(-1)?.seq ?? 0,
    has_more: false,
    read_at: now,
    ...overrides,
  };
}

export function turn(status = "open", overrides: Partial<TurnCardView> = {}): TurnCardView {
  return {
    turn_id: "t",
    agent_name: "agent",
    session_id: "s",
    status,
    closing_kind: null,
    base_node_id: null,
    start_node_id: null,
    end_node_id: null,
    branch_id: null,
    end_branch_id: null,
    opener_delivery_id: null,
    opened_at: now,
    waiting_since: null,
    closed_at: null,
    abort_reason: null,
    incomplete_node_id: null,
    recovery_branch_id: null,
    turn_index: 1,
    opener_message_id: "message-1",
    opener_entry_id: "entry-1",
    parent_turn_id: null,
    stats: {
      steps: 0,
      node_count: 0,
      token_input: 0,
      token_output: 0,
      token_total: 0,
      token_cache_read: 0,
      token_cache_write: 0,
      cache_hit_rate: null,
      duration_ms: null,
      waiting_ms: 0,
    },
    ...overrides,
  };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
