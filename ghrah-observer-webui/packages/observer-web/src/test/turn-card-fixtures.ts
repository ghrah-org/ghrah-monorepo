// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { TranscriptDeliveryView, TranscriptEntryView, TurnCardView } from "@ghrah/protocol";

export const openedAt = "2026-11-26T02:00:00Z";

export function turn(overrides: Partial<TurnCardView> = {}): TurnCardView {
  return {
    turn_id: "turn-1",
    agent_name: "agent-1",
    session_id: "session-1",
    status: "open",
    closing_kind: null,
    base_node_id: null,
    start_node_id: null,
    end_node_id: null,
    branch_id: null,
    end_branch_id: null,
    opener_delivery_id: null,
    opened_at: openedAt,
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
      steps: 2,
      node_count: 2,
      token_input: 1400,
      token_output: 200,
      token_total: 1600,
      token_cache_read: 700,
      token_cache_write: 100,
      cache_hit_rate: 0.5,
      duration_ms: 6000,
      waiting_ms: 0,
    },
    ...overrides,
  };
}

export function entry(overrides: Partial<TranscriptEntryView> = {}): TranscriptEntryView {
  return {
    entry_id: "entry-1",
    seq: 1,
    author: "Ada",
    author_type: "human",
    timestamp: 0,
    message_id: "message-1",
    content: "Check the logs.\nFull message.",
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

export function delivery(overrides: Partial<TranscriptDeliveryView> = {}): TranscriptDeliveryView {
  return {
    delivery_id: "delivery-1",
    message_id: "message-1",
    recipient_id: "agent-1",
    state: "pending",
    error: null,
    blocked_reason: null,
    turn_id: "turn-1",
    delivery_policy: "inject",
    display_state: "injected",
    queue_position: 0,
    queue_reason: null,
    cancelable: false,
    injection_step: null,
    ...overrides,
  };
}
