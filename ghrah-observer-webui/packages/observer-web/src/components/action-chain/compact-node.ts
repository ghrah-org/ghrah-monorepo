// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ActionNode } from "@ghrah/protocol";

export function isCompactNode(node: ActionNode | null | undefined): boolean {
  return node?.is_snapshot === true || node?.metadata.node_kind === "compact";
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function range(value: unknown): readonly [string, string] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const start = text(value[0]);
  const end = text(value[1]);
  return start === null || end === null ? null : [start, end];
}

export function compactMetrics(node: ActionNode) {
  const metadata = node.metadata;
  return {
    tokensBefore: count(metadata.tokens_before),
    tokensAfter: count(metadata.tokens_after),
    triggerSource: text(metadata.trigger_source),
    postCheck: text(metadata.post_check),
    summarizedRange: range(metadata.summarized_range),
    keptRecentNodes: count(metadata.kept_recent_nodes),
  };
}

export function compactWarnings(node: ActionNode) {
  const metadata = node.metadata;
  return {
    degraded: metadata.degraded === true,
    overBudget: metadata.post_check === "over_budget",
    truncatedSummaryInput: metadata.truncated_summary_input === true,
  };
}
