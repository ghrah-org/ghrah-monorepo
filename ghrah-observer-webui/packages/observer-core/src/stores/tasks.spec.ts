// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  TaskClaimEventPayload,
  TaskClaimPayload,
  TaskDumpResultPayload,
  TaskInfoPayload,
} from "@ghrah/protocol";
import { EventType } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useTasksStore } from "./tasks.js";

function makeTask(overrides: Partial<TaskInfoPayload> = {}): TaskInfoPayload {
  return {
    task_id: "t1",
    project_id: "p1",
    title: "task-1",
    description: "",
    status: "pending",
    priority: "normal",
    dependencies: [],
    created_at: "",
    updated_at: "",
    metadata: {},
    ...overrides,
  };
}

describe("useTasksStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts empty", () => {
    const store = useTasksStore();
    expect(store.tasks.size).toBe(0);
  });

  it("onTaskEvent upserts for non-delete events", () => {
    const store = useTasksStore();
    store.onTaskEvent(EventType.TASK_CREATED, { task: makeTask() });
    expect(store.tasks.size).toBe(1);
    store.onTaskEvent(EventType.TASK_UPDATED, {
      task: makeTask({ status: "in_progress" }),
      previous_status: "pending",
    });
    expect(store.tasks.size).toBe(1);
    expect(store.tasks.get("t1")?.status).toBe("in_progress");
  });

  it("onTaskEvent removes on TASK_DELETED", () => {
    const store = useTasksStore();
    store.onTaskEvent(EventType.TASK_CREATED, { task: makeTask() });
    store.onTaskEvent(EventType.TASK_DELETED, { task: makeTask(), reason: "cleanup" });
    expect(store.tasks.size).toBe(0);
  });

  it("tasksByProject filters by project_id", () => {
    const store = useTasksStore();
    store.setTasksFromList([
      makeTask({ task_id: "t1", project_id: "p1" }),
      makeTask({ task_id: "t2", project_id: "p2" }),
      makeTask({ task_id: "t3", project_id: "p1" }),
    ]);
    expect(
      store
        .tasksByProject("p1")
        .map((t) => t.task_id)
        .sort(),
    ).toEqual(["t1", "t3"]);
    expect(store.tasksByProject("p2")).toHaveLength(1);
    expect(store.tasksByProject("nope")).toEqual([]);
  });

  it("setTasksFromList replaces map", () => {
    const store = useTasksStore();
    store.onTaskEvent(EventType.TASK_CREATED, { task: makeTask({ task_id: "old" }) });
    store.setTasksFromList([makeTask({ task_id: "t1" })]);
    expect([...store.tasks.keys()]).toEqual(["t1"]);
  });

  it("clearAll resets", () => {
    const store = useTasksStore();
    store.setTasksFromList([makeTask()]);
    store.clearAll();
    expect(store.tasks.size).toBe(0);
  });

  it("clearProject removes only tasks owned by that Project", () => {
    const store = useTasksStore();
    store.setTasksFromList([
      makeTask({ task_id: "t1", project_id: "p1" }),
      makeTask({ task_id: "t2", project_id: "p2" }),
    ]);
    store.clearProject("p1");
    expect([...store.tasks.keys()]).toEqual(["t2"]);
  });
});

function makeClaim(overrides: Partial<TaskClaimPayload> = {}): TaskClaimPayload {
  return {
    claim_id: "c1",
    task_id: "t1",
    claimant_type: "agent",
    claimant_id: "a1",
    claimant_name: "agent-1",
    note: null,
    evidence: [
      {
        evidence_id: "e1",
        kind: "git_commit",
        ref: "repo@abc123",
        digest: null,
        payload: { sha: "abc123" },
        created_by: "a1",
        created_at: "2026-09-21T00:00:00Z",
      },
    ],
    state: "submitted",
    checks: [{ checker: "commit-exists", passed: true, detail: null }],
    verdict_by: null,
    verdict_at: null,
    verdict_reason: null,
    provenance: { agent_id: "a1", session_id: "s1", branch_id: "b1", node_id: "n1" },
    created_at: "2026-09-21T00:00:00Z",
    ...overrides,
  };
}

function makeClaimEvent(task: TaskInfoPayload, claim: TaskClaimPayload): TaskClaimEventPayload {
  return { task, claim, previous_status: "in_progress", reason: null };
}

describe("useTasksStore attribution", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("onClaimEvent upserts task + claim with embedded evidence", () => {
    const store = useTasksStore();
    const claim = makeClaim();
    store.onClaimEvent(EventType.TASK_DELIVERED, makeClaimEvent(makeTask(), claim));
    expect(store.tasks.get("t1")).toEqual(makeTask());
    expect(store.claims.get("c1")).toEqual(claim);
    expect(store.evidenceForClaim("c1")).toEqual(claim.evidence);
    expect(store.claimsByTask("t1")).toEqual([claim]);
  });

  it("upsertClaim merges task_list_claims results without touching tasks", () => {
    const store = useTasksStore();
    store.upsertClaim(makeClaim({ claim_id: "c2", state: "verified" }));
    expect(store.claims.size).toBe(1);
    expect(store.tasks.size).toBe(0);
  });

  it("replaceFromDump fully replaces claims/evidence and upserts tasks", () => {
    const store = useTasksStore();
    store.onClaimEvent(EventType.TASK_DELIVERED, makeClaimEvent(makeTask(), makeClaim()));
    const dump: TaskDumpResultPayload = {
      tasks: [makeTask({ status: "delivered" })],
      claims: [makeClaim({ claim_id: "c9", state: "verified" })],
      evidence: [],
    };
    store.replaceFromDump(dump);
    expect(store.claims.size).toBe(1);
    expect(store.claims.has("c1")).toBe(false);
    expect(store.claims.get("c9")?.state).toBe("verified");
    expect(store.tasks.get("t1")?.status).toBe("delivered");
    // legacy task 不删除边界：dump 外的既有 task 保留
    store.replaceFromDump({ tasks: [], claims: [], evidence: [] });
    expect(store.tasks.size).toBe(1);
    expect(store.claims.size).toBe(0);
  });

  it("clearProject/clearAll 同步清理 claims/evidence", () => {
    const store = useTasksStore();
    store.onClaimEvent(
      EventType.TASK_DELIVERED,
      makeClaimEvent(makeTask({ task_id: "t1", project_id: "p1" }), makeClaim()),
    );
    store.onClaimEvent(
      EventType.TASK_DELIVERED,
      makeClaimEvent(
        makeTask({ task_id: "t2", project_id: "p2" }),
        makeClaim({ claim_id: "c2", task_id: "t2" }),
      ),
    );
    store.clearProject("p1");
    expect(store.claims.has("c1")).toBe(false);
    expect(store.claims.has("c2")).toBe(true);
    store.clearAll();
    expect(store.claims.size).toBe(0);
    expect(store.tasks.size).toBe(0);
  });

  // 验收②：事件流累计态 == 清空后 dump 重建态（探针 5 P0 切面）。
  it("event-stream terminal state equals dump-rebuilt state", () => {
    const events = [
      makeClaimEvent(makeTask({ task_id: "t1" }), makeClaim({ claim_id: "c1", task_id: "t1" })),
      makeClaimEvent(
        makeTask({ task_id: "t2", status: "delivered" }),
        makeClaim({ claim_id: "c2", task_id: "t2", state: "verified" }),
      ),
    ];
    const live = useTasksStore();
    for (const event of events) live.onClaimEvent(EventType.TASK_DELIVERED, event);

    setActivePinia(createPinia());
    const rebuilt = useTasksStore();
    const dump: TaskDumpResultPayload = {
      tasks: events.map((event) => event.task),
      claims: events.map((event) => event.claim),
      evidence: events.flatMap((event) => event.claim.evidence),
    };
    rebuilt.replaceFromDump(dump);

    expect([...rebuilt.tasks.keys()].sort()).toEqual([...live.tasks.keys()].sort());
    expect([...rebuilt.claims.keys()].sort()).toEqual([...live.claims.keys()].sort());
    for (const claimId of live.claims.keys()) {
      expect(rebuilt.claims.get(claimId)).toEqual(live.claims.get(claimId));
    }
    for (const taskId of live.tasks.keys()) {
      expect(rebuilt.tasks.get(taskId)).toEqual(live.tasks.get(taskId));
    }
    for (const claimId of live.claims.keys()) {
      expect(rebuilt.evidenceForClaim(claimId)).toEqual(live.evidenceForClaim(claimId));
    }
  });
});
