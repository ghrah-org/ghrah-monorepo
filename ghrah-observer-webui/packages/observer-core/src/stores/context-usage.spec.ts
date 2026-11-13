// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { ContextUsageUpdatedPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useContextUsageStore } from "./context-usage.js";

const payload = (
  overrides: Partial<ContextUsageUpdatedPayload> = {},
): ContextUsageUpdatedPayload => ({
  project_id: "p1",
  agent_id: "a1",
  agent_name: "coder",
  phase: "post_call",
  occupied_tokens: 4000,
  basis: "real",
  budget_tokens: 8192,
  budget_source: "declared",
  compact_threshold: 0.8,
  real_input_tokens: 4000,
  real_output_tokens: 300,
  real_cache_read_tokens: 3000,
  real_cache_write_tokens: 200,
  compaction: null,
  iteration: 2,
  ...overrides,
});

describe("useContextUsageStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("stores the latest snapshot keyed by agent", () => {
    const store = useContextUsageStore();
    expect(store.onContextUsageUpdated(payload(), 111)).toBe(true);
    expect(
      store.onContextUsageUpdated(payload({ phase: "pre_call", occupied_tokens: 4100 }), 222),
    ).toBe(true);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    // 展示优先 post_call 槽：latest 已是 pre_call，但占用取 4000
    expect(display).not.toBeNull();
    expect(display?.mode).toBe("ratio");
    expect(display?.occupiedTokens).toBe(4000);
    expect(display?.percent).toBe(49);
  });

  it("keeps latestPostCall slot separate from latest", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload(), 111);
    store.onContextUsageUpdated(payload({ phase: "pre_call", basis: "anchor" }), 222);
    const key = JSON.stringify(["p1", "a1"]);
    const entry = store.entries.get(key);
    expect(entry?.latest.phase).toBe("pre_call");
    expect(entry?.latestPostCall?.phase).toBe("post_call");
  });

  it("accumulates real input tokens across post_call events", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload({ real_input_tokens: 100 }), 1);
    store.onContextUsageUpdated(payload({ real_input_tokens: 250 }), 2);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.cumulativeInputTokens).toBe(350);
  });

  it("projects cache breakdown and budget source into display", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(
      payload({
        real_input_tokens: 4000,
        real_output_tokens: 300,
        real_cache_read_tokens: 3000,
        real_cache_write_tokens: 200,
        budget_source: "vendor",
      }),
      1,
    );
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.realInputTokens).toBe(4000);
    expect(display?.realOutputTokens).toBe(300);
    expect(display?.realCacheReadTokens).toBe(3000);
    expect(display?.realCacheWriteTokens).toBe(200);
    expect(display?.budgetSource).toBe("vendor");
    expect(display?.cumulativeOutputTokens).toBe(300);
    expect(display?.cumulativeCacheReadTokens).toBe(3000);
  });

  it("defaults cache dimensions to null when vendor omits them", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(
      payload({
        real_cache_read_tokens: undefined,
        real_cache_write_tokens: undefined,
        budget_source: undefined,
      }),
      1,
    );
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.realCacheReadTokens).toBeNull();
    expect(display?.realCacheWriteTokens).toBeNull();
    expect(display?.budgetSource).toBeNull();
  });

  it("falls back to cumulative mode when budget is zero", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload({ budget_tokens: 0 }), 1);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.mode).toBe("cumulative");
    expect(display?.percent).toBeNull();
  });

  it("treats the built-in default budget as unknown, not as a real window", () => {
    // budget_source="default" 表示链路无任何真实窗口信息，budget 是
    // DEFAULT_WINDOW_MAX_TOKENS 兜底值。据它画比例条会给出不存在的精度，
    // 故落 cumulative（只报累计用量）。
    const store = useContextUsageStore();
    store.onContextUsageUpdated(
      payload({
        budget_tokens: 1_000_000,
        budget_source: "default",
        occupied_tokens: 5000,
      }),
      1,
    );
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.mode).toBe("cumulative");
    expect(display?.percent).toBeNull();
    expect(display?.budgetTokens).toBe(0);
  });

  it("renders a ratio bar for a genuinely resolved budget", () => {
    // agentconf 解析出的窗口是真实预算，应正常出比例条。
    const store = useContextUsageStore();
    store.onContextUsageUpdated(
      payload({
        budget_tokens: 200_000,
        budget_source: "agentconf",
        occupied_tokens: 50_000,
      }),
      1,
    );
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.mode).toBe("ratio");
    expect(display?.percent).toBe(25);
    expect(display?.budgetTokens).toBe(200_000);
  });

  it("stays cumulative when budget is zero regardless of source", () => {
    // 零预算本身已走 cumulative。
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload({ budget_tokens: 0, budget_source: "default" }), 1);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.mode).toBe("cumulative");
    expect(display?.percent).toBeNull();
  });

  it("rejects payloads without project-scoped identity", () => {
    const store = useContextUsageStore();
    expect(store.onContextUsageUpdated(payload({ project_id: "" }))).toBe(false);
    expect(store.onContextUsageUpdated(payload({ agent_id: "" }))).toBe(false);
    expect(store.entries.size).toBe(0);
  });

  it("caps percent at 100 when occupied exceeds budget", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload({ occupied_tokens: 20000 }), 1);
    expect(store.usageFor({ projectId: "p1", agentId: "a1" })?.percent).toBe(100);
  });

  it("returns null for unknown agents", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload(), 1);
    expect(store.usageFor({ projectId: "p1", agentId: "nope" })).toBeNull();
  });

  it("clears exact agent and project scopes", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload(), 1);
    store.onContextUsageUpdated(payload({ project_id: "p2", agent_id: "a2" }), 2);
    store.clearAgent({ projectId: "p1", agentId: "a1" });
    expect(store.entries.size).toBe(1);
    store.clearProject("p2");
    expect(store.entries.size).toBe(0);
  });

  it("clearAll empties the store", () => {
    const store = useContextUsageStore();
    store.onContextUsageUpdated(payload(), 1);
    store.clearAll();
    expect(store.entries.size).toBe(0);
  });

  it("restoreSnapshot directly sets snapshot slots and cumulative values", () => {
    const store = useContextUsageStore();
    // 刷新后 store 为空：恢复直置 latest/latestPostCall/cumulative
    const restored = store.restoreSnapshot(
      payload({ real_input_tokens: 500, real_output_tokens: 50 }),
      { input: 3500, output: 300, cache_read: 2000 },
      100,
    );
    expect(restored).toBe(true);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.realInputTokens).toBe(500);
    expect(display?.realOutputTokens).toBe(50);
    expect(display?.cumulativeInputTokens).toBe(3500);
    expect(display?.cumulativeOutputTokens).toBe(300);
    expect(display?.cumulativeCacheReadTokens).toBe(2000);
    // latestPostCall 槽恢复（breakdown 展示依赖）
    const entry = store.entries.get(JSON.stringify(["p1", "a1"]));
    expect(entry?.latestPostCall?.realInputTokens).toBe(500);
  });

  it("restoreSnapshot skips stale restore when realtime event is newer", () => {
    const store = useContextUsageStore();
    // 实时事件先到（t=200），恢复回执较旧（t=100）：不回退快照
    store.onContextUsageUpdated(payload({ real_input_tokens: 999, occupied_tokens: 999 }), 200);
    const restored = store.restoreSnapshot(
      payload({ real_input_tokens: 500 }),
      { input: 3500, output: 300, cache_read: 2000 },
      100,
    );
    expect(restored).toBe(false);
    const display = store.usageFor({ projectId: "p1", agentId: "a1" });
    expect(display?.realInputTokens).toBe(999);
    expect(display?.cumulativeInputTokens).toBe(999);
  });

  it("restoreSnapshot rejects payloads without identity", () => {
    const store = useContextUsageStore();
    expect(
      store.restoreSnapshot(payload({ project_id: "" }), { input: 1, output: 1, cache_read: 1 }),
    ).toBe(false);
    expect(store.entries.size).toBe(0);
  });
});
