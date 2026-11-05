// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { PythonHalfInfo, TaskClaimPayload, TaskInfoPayload } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import { MockState } from "./state.js";

function pyHalf(overrides: Partial<PythonHalfInfo> = {}): PythonHalfInfo {
  return {
    plugin_id: "task-commit-attribution",
    version: "0.1.0",
    provides: ["checker/commit-exists", "evidence-kind/git_commit"],
    requires_capabilities: [],
    instances: [],
    ...overrides,
  };
}

function seedTask(): TaskInfoPayload {
  return {
    task_id: "t1",
    project_id: "p1",
    title: "x",
    description: "",
    agent_id: null,
    agent_name: null,
    status: "delivered",
    priority: "normal",
    parent_id: null,
    dependencies: [],
    result: null,
    error: null,
    created_at: "",
    updated_at: "",
    started_at: null,
    completed_at: null,
    metadata: {},
    verification: null,
  };
}

function seedClaim(): TaskClaimPayload {
  return {
    claim_id: "c1",
    task_id: "t1",
    claimant_type: "agent",
    claimant_id: "a1",
    claimant_name: null,
    note: null,
    evidence: [
      {
        evidence_id: "e1",
        kind: "git_commit",
        ref: "repo@abc",
        digest: null,
        payload: {},
        created_by: "a1",
        created_at: "",
      },
    ],
    state: "submitted",
    checks: [],
    verdict_by: null,
    verdict_at: null,
    verdict_reason: null,
    provenance: null,
    created_at: "",
  };
}

function seededState(): MockState {
  const state = new MockState();
  state.pythonHalves = [
    pyHalf(),
    pyHalf({ plugin_id: "stale-plugin", version: "0.2.0" }),
    pyHalf({
      plugin_id: "py-only",
      version: "1.0.0",
      provides: ["capability:x"],
      requires_capabilities: ["webview-sandbox"],
    }),
  ];
  state.tasks.set("t1", seedTask());
  state.tasks.set("t2", { ...seedTask(), task_id: "t2", project_id: "p2" });
  const claim = seedClaim();
  state.claims.set(claim.claim_id, claim);
  for (const item of claim.evidence) state.evidence.set(item.evidence_id, item);
  return state;
}

describe("MockState plugin negotiation", () => {
  it("matched for same-version halves with merged provides", () => {
    const state = seededState();
    const outcome = state.handleCommand("plugin_negotiate", {
      enabled_ts: [
        {
          plugin_id: "task-commit-attribution",
          version: "0.1.0",
          provides: ["badge-renderer/git_commit"],
        },
      ],
    });
    expect(outcome.success).toBe(true);
    const data = outcome.data as Record<string, unknown>;
    expect(data.matched).toEqual([
      {
        plugin_id: "task-commit-attribution",
        version: "0.1.0",
        provides: [
          "checker/commit-exists",
          "evidence-kind/git_commit",
          "badge-renderer/git_commit",
        ],
        instances: [],
      },
    ]);
    expect(data.version_conflicts).toEqual([]);
    expect(data.missing_capabilities).toEqual(["webview-sandbox"]);
    expect((data.python_only as Array<{ plugin_id: string }>).map((x) => x.plugin_id)).toEqual([
      "stale-plugin",
      "py-only",
    ]);
    expect(data.ts_only as unknown[]).toEqual([]);
  });

  it("version conflict when halves differ", () => {
    const state = seededState();
    const outcome = state.handleCommand("plugin_negotiate", {
      enabled_ts: [
        { plugin_id: "stale-plugin", version: "9.9.9", provides: [] },
        { plugin_id: "unknown-ts-plugin", version: "0.0.1", provides: [] },
      ],
    });
    const data = outcome.data as Record<string, unknown>;
    expect(data.version_conflicts).toEqual([
      { plugin_id: "stale-plugin", python_version: "0.2.0", ts_version: "9.9.9" },
    ]);
    expect((data.ts_only as Array<{ plugin_id: string }>).map((x) => x.plugin_id)).toEqual([
      "unknown-ts-plugin",
    ]);
  });

  it("empty enabled_ts yields python_only for all declared halves", () => {
    const state = seededState();
    const outcome = state.handleCommand("plugin_negotiate", { enabled_ts: [] });
    const data = outcome.data as Record<string, unknown>;
    expect(data.matched).toEqual([]);
    expect((data.python_only as unknown[]).length).toBe(3);
  });

  it("rejects invalid payload", () => {
    const state = new MockState();
    const outcome = state.handleCommand("plugin_negotiate", { enabled_ts: "nope" });
    expect(outcome.success).toBe(false);
  });
});

describe("MockState task attribution commands", () => {
  it("task_dump returns seeded tasks/claims/evidence with claim-embedded evidence", () => {
    const state = seededState();
    const outcome = state.handleCommand("task_dump", {});
    expect(outcome.success).toBe(true);
    const data = outcome.data as Record<string, unknown>;
    expect((data.tasks as Array<{ task_id: string }>).map((t) => t.task_id).sort()).toEqual([
      "t1",
      "t2",
    ]);
    expect((data.claims as Array<{ claim_id: string }>).map((c) => c.claim_id)).toEqual(["c1"]);
    expect((data.evidence as Array<{ evidence_id: string }>).map((e) => e.evidence_id)).toEqual([
      "e1",
    ]);
  });

  it("task_dump filters by project and applies limit to tasks", () => {
    const state = seededState();
    const byProject = state.handleCommand("task_dump", { project_id: "p2" });
    const data = byProject.data as Record<string, unknown>;
    expect((data.tasks as unknown[]).length).toBe(1);
    expect(data.claims).toEqual([]);

    const limited = state.handleCommand("task_dump", { limit: 1 });
    expect(((limited.data as Record<string, unknown>).tasks as unknown[]).length).toBe(1);
  });

  it("task_list_claims filters by task and state", () => {
    const state = seededState();
    const byTask = state.handleCommand("task_list_claims", { task_id: "t1" });
    const data = byTask.data as Record<string, unknown>;
    expect((data.claims as unknown[]).length).toBe(1);
    expect(data.count).toBe(1);

    const none = state.handleCommand("task_list_claims", { state: "verified" });
    expect((none.data as Record<string, unknown>).claims).toEqual([]);
  });
});
