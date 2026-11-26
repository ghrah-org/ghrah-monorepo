// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { TranscriptEntryView, TurnCardView } from "@ghrah/protocol";

export interface Exchange {
  anchorId: string;
  anchorMessageId: string | null;
  optimistic: boolean;
  turnIds: string[];
  entryIds: string[];
}

export function isAnchor(entry: TranscriptEntryView, agentMemberIds: ReadonlySet<string>): boolean {
  return entry.author_type === "human" || entry.targets.some((id) => agentMemberIds.has(id));
}

export function projectExchanges(
  entries: readonly TranscriptEntryView[],
  turns: readonly TurnCardView[],
  pending: readonly { clientSeq: number }[],
  agentMemberIds: ReadonlySet<string>,
): Exchange[] {
  const exchanges: Exchange[] = [];
  const byMessageId = new Map<string, Exchange>();
  const byEntryId = new Map<string, Exchange>();
  let current: Exchange | undefined;
  for (const entry of entries) {
    if (isAnchor(entry, agentMemberIds)) {
      current = {
        anchorId: entry.entry_id,
        anchorMessageId: entry.message_id,
        optimistic: false,
        turnIds: [],
        entryIds: [],
      };
      exchanges.push(current);
      byEntryId.set(entry.entry_id, current);
      if (entry.message_id) byMessageId.set(entry.message_id, current);
    }
    if (!current) {
      current = {
        anchorId: "",
        anchorMessageId: null,
        optimistic: false,
        turnIds: [],
        entryIds: [],
      };
      exchanges.push(current);
    }
    current.entryIds.push(entry.entry_id);
  }

  for (const turn of turns) {
    const exchange =
      (turn.opener_entry_id ? byEntryId.get(turn.opener_entry_id) : undefined) ??
      (turn.opener_message_id ? byMessageId.get(turn.opener_message_id) : undefined);
    if (exchange && !exchange.turnIds.includes(turn.turn_id)) exchange.turnIds.push(turn.turn_id);
  }

  for (const { clientSeq } of [...pending].sort(
    (left, right) => left.clientSeq - right.clientSeq,
  )) {
    const anchorId = `pending:${clientSeq}`;
    exchanges.push({
      anchorId,
      anchorMessageId: null,
      optimistic: true,
      turnIds: [],
      entryIds: [anchorId],
    });
  }
  return exchanges;
}

export function humanStateOf(entry: TranscriptEntryView, isPending = false) {
  if (isPending) return "pending";
  if (entry.author_type !== "human") return null;
  const state = entry.human_state;
  switch (state) {
    case "sent":
    case "queued":
    case "injected":
    case "failed":
      return state;
    default:
      return null;
  }
}
