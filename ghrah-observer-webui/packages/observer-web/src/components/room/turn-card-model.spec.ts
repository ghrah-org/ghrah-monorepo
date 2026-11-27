// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";

import { i18n } from "@/i18n";
import { delivery, entry, openedAt, turn } from "@/test/turn-card-fixtures";
import {
  agentColorIndex,
  derivePhase,
  formatCacheRate,
  formatCount,
  formatDuration,
  formatTokens,
  humanStateText,
  injectedSummary,
  nodeSummary,
  phaseText,
  triggerText,
  turnStats,
} from "./turn-card-model.js";

const { t } = i18n.global;
const now = Date.parse(openedAt) + 5000;
const running = ActionNodeSchema.parse({
  id: "running",
  ability_names: ["execute_command"],
  metadata: { status: "running" },
});

describe("derivePhase", () => {
  it.each([
    ["closed", "done"],
    ["failed", "failed"],
    ["interrupted", "interrupted"],
    ["aborted", "aborted"],
    ["rolled_back", "rolled_back"],
    ["waiting_hitl", "waiting_hitl"],
  ])("%s takes priority over a running node", (status, kind) => {
    expect(derivePhase(turn({ status }), [running], now).kind).toBe(kind);
  });

  it("uses the latest completed step as evidence when available", () => {
    const prior = ActionNodeSchema.parse({
      ability_names: ["read_file"],
      metadata: { status: "completed" },
    });
    expect(derivePhase(turn(), [prior, running], now)).toEqual({
      kind: "acting",
      abilityClass: "read",
      step: 2,
    });
    expect(derivePhase(turn(), [running], now)).toMatchObject({
      kind: "acting",
      abilityClass: "execute",
    });
    expect(derivePhase(turn(), [prior], now)).toEqual({ kind: "idle" });
    expect(derivePhase(turn(), [], now)).toEqual({ kind: "idle" });
  });

  it("uses explicit waiting and failure facts without inventing reasons", () => {
    const failed = turn({ status: "failed", incomplete_node_id: "error" });
    const node = ActionNodeSchema.parse({
      id: "error",
      metadata: { error: "provider unavailable" },
    });
    expect(phaseText(derivePhase(failed, [node], now), t)).toBe("Failed · provider unavailable");
    expect(phaseText(derivePhase(failed, [], now), t)).toBe("Failed");
    expect(derivePhase(turn({ status: "waiting_hitl" }), [], now)).toEqual({
      kind: "waiting_hitl",
      since: openedAt,
    });
  });
});

it("uses server step and token facts whether or not nodes are loaded", () => {
  const closed = turn({ status: "closed" });
  expect(turnStats(closed, [], now)).toEqual({
    steps: 2,
    durationMs: 6000,
    tokens: { input: 1400, output: 200, total: 1600, cacheRead: 700, cacheWrite: 100 },
    cacheHitRate: 0.5,
  });
  expect(turnStats(closed, [running], now)).toEqual(turnStats(closed, [], now));
  expect(turnStats(turn(), [], now).durationMs).toBe(5000);
  expect(turnStats(turn({ status: "waiting_hitl" }), [], now).durationMs).toBe(5000);
  expect(turnStats(turn(), [], Date.parse(openedAt) - 1).durationMs).toBe(0);
  expect(turnStats(turn({ opened_at: "invalid" }), [], now).durationMs).toBeNull();
});

it("keeps the duration, token and cache formats from the example", () => {
  expect([null, 0, 65000, 3720000].map(formatDuration)).toEqual(["—", "0s", "1m5s", "1h2m"]);
  expect([null, 50, 1400, 1230000].map(formatTokens)).toEqual(["—", "50", "1.4k", "1.2M"]);
  expect([null, 0, 0.495].map(formatCacheRate)).toEqual(["—", "0%", "50%"]);
  expect([null, 0, 12].map(formatCount)).toEqual(["—", "0", "12"]);
});

it("assigns a stable identity slot and uses all eight slots", () => {
  expect(agentColorIndex("hello")).toBe(4);
  const slots = new Set(
    Array.from({ length: 32 }, (_, index) => agentColorIndex(`agent-${index}`)),
  );
  expect([...slots].sort()).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
});

it("keeps opener identity, delivery state, and node summary separate", () => {
  expect(triggerText(turn(), entry(), t)).toBe("↳ reply to Ada");
  expect(
    triggerText(
      turn({ parent_turn_id: "parent" }),
      entry({ author_type: "agent", author: "Coder" }),
      t,
    ),
  ).toBe("↳ reply to @Coder · sub-agent turn");
  expect(triggerText(turn(), null, t)).toBe("");
  expect(humanStateText(entry({ human_state: "injected", injection_step: 2 }), false, t)).toBe(
    "Injected · step 2",
  );
  expect(
    humanStateText(
      entry({ human_state: "failed", deliveries: [delivery({ error: "Agent offline" })] }),
      false,
      t,
    ),
  ).toBe("Not delivered · Agent offline");
  expect(humanStateText(entry({ human_state: "queued" }), false, t)).toBe("Queued");
  expect(humanStateText(entry(), true, t)).toBe("Sending");
  expect(
    nodeSummary(
      ActionNodeSchema.parse({
        ability_names: ["read_file"],
        messages_delta: [
          { role: "user", content_blocks: [{ type: "text", text: "Never use the input" }] },
          { role: "ai", content_blocks: [{ type: "text", text: "Read the log\nMore detail" }] },
        ],
      }),
    ),
  ).toBe("Read the log");
});

it("counts consumed and waiting inserts by delivery and excludes queued entries", () => {
  const consumed = entry({
    insert_before_turn_id: "other",
    deliveries: [
      delivery({ turn_id: "other", injection_step: 4 }),
      delivery({ injection_step: 2, display_state: "consumed" }),
    ],
  });
  const waiting = entry({ entry_id: "waiting", deliveries: [delivery()] });
  const queued = entry({
    entry_id: "queued",
    deliveries: [delivery({ turn_id: null, display_state: "queued" })],
  });
  expect(injectedSummary([consumed, waiting, queued], "turn-1")).toEqual({
    injected: 1,
    waiting: 1,
    inlineInserts: [{ entry: consumed, step: 2 }],
    waitingInserts: [waiting],
  });
});
