// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";

import { compactMetrics, compactWarnings, isCompactNode } from "./compact-node.js";

describe("compact node", () => {
  it("recognizes both stored markers", () => {
    expect(isCompactNode(ActionNodeSchema.parse({ is_snapshot: true }))).toBe(true);
    expect(isCompactNode(ActionNodeSchema.parse({ metadata: { node_kind: "compact" } }))).toBe(
      true,
    );
    expect(isCompactNode(ActionNodeSchema.parse({ metadata: { node_kind: "incomplete" } }))).toBe(
      false,
    );
    expect(isCompactNode(undefined)).toBe(false);
  });

  it("reads real metadata types and preserves all three warnings", () => {
    const node = ActionNodeSchema.parse({
      metadata: {
        tokens_before: 1000,
        tokens_after: 0,
        trigger_source: "manual",
        summarized_range: ["first", "last"],
        kept_recent_nodes: 0,
        post_check: "over_budget",
        degraded: true,
        truncated_summary_input: true,
      },
    });
    expect(compactMetrics(node)).toEqual({
      tokensBefore: 1000,
      tokensAfter: 0,
      triggerSource: "manual",
      summarizedRange: ["first", "last"],
      keptRecentNodes: 0,
      postCheck: "over_budget",
    });
    expect(compactWarnings(node)).toEqual({
      degraded: true,
      overBudget: true,
      truncatedSummaryInput: true,
    });
  });

  it("ignores invalid and missing values without casting them", () => {
    const node = ActionNodeSchema.parse({
      metadata: {
        tokens_before: "1000",
        tokens_after: -1,
        trigger_source: false,
        summarized_range: ["one", 2],
        post_check: true,
        degraded: "true",
      },
    });
    expect(Object.values(compactMetrics(node))).toEqual(Array(6).fill(null));
    expect(Object.values(compactWarnings(node))).toEqual([false, false, false]);
  });
});
