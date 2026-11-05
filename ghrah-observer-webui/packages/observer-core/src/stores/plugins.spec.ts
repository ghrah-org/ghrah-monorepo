// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { PluginNegotiateResultPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { usePluginsStore } from "./plugins.js";

function makeResult(
  overrides: Partial<PluginNegotiateResultPayload> = {},
): PluginNegotiateResultPayload {
  return {
    matched: [
      { plugin_id: "p1", version: "0.1.0", provides: ["badge-renderer/git_commit"], instances: [] },
    ],
    python_only: [
      { plugin_id: "p2", version: "0.2.0", provides: [], requires_capabilities: [], instances: [] },
    ],
    ts_only: [{ plugin_id: "p3", version: "0.3.0", provides: [] }],
    version_conflicts: [{ plugin_id: "p4", python_version: "1.0.0", ts_version: "0.9.0" }],
    missing_capabilities: ["webview-sandbox"],
    instances: {},
    ...overrides,
  };
}

describe("usePluginsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts empty", () => {
    const store = usePluginsStore();
    expect(store.negotiation).toBeNull();
    expect(store.matchedById.size).toBe(0);
  });

  it("setNegotiationResult stores the authoritative result and derives matchedById", () => {
    const store = usePluginsStore();
    store.setNegotiationResult(makeResult());
    expect(store.negotiation?.matched).toHaveLength(1);
    expect(store.matchedById.get("p1")?.version).toBe("0.1.0");
    store.setNegotiationResult(makeResult({ matched: [] }));
    expect(store.matchedById.size).toBe(0);
  });

  it("accumulates crashed entries and lifecycle by plugin_id", () => {
    const store = usePluginsStore();
    store.onCrashed({
      plugin_id: "p1",
      command: "task_submit_completion",
      error: "boom",
      instance_id: null,
    });
    store.onCrashed({ plugin_id: "p1", command: "task_verify", error: "again", instance_id: "i1" });
    expect(store.crashed).toHaveLength(2);
    store.onLifecycleEvent({ plugin_id: "p1", version: "0.1.0", instance_ids: [] });
    store.onLifecycleEvent({ plugin_id: "p1", version: "0.1.1", instance_ids: ["i1"] });
    expect(store.lifecycle.get("p1")?.instance_ids).toEqual(["i1"]);
  });

  it("clear resets all projections", () => {
    const store = usePluginsStore();
    store.setNegotiationResult(makeResult());
    store.onCrashed({ plugin_id: "p1", command: "c", error: "e", instance_id: null });
    store.clear();
    expect(store.negotiation).toBeNull();
    expect(store.crashed).toEqual([]);
    expect(store.lifecycle.size).toBe(0);
  });
});
