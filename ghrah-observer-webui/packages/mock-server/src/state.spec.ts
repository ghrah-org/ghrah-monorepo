// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { PythonHalfInfo, TaskClaimPayload, TaskInfoPayload } from "@ghrah/protocol";
import { describe, expect, it, vi } from "vitest";
import { MockState } from "./state.js";
import { AgentSnapshotSchema, CommandType } from "@ghrah/protocol";

describe("stable mock entities", () => {
  it("keeps chain iterations increasing across messages and restarts", () => {
    vi.useFakeTimers();
    const state = new MockState({ simulateChains: true, chainIntervalMs: 100 });
    try {
      const project = state.handleCommand(CommandType.PROJECT_CREATE, { name: "iterations" })
        .data as { project: { project_id: string } };
      const snapshot = AgentSnapshotSchema.parse(
        state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
          project_id: project.project.project_id,
          name: "worker",
          manifest_ref: "mock.agent",
          start: true,
        }).data,
      );
      const iterations: number[] = [];
      state.emit = (type, payload) => {
        if (type === "action_chain_updated")
          iterations.push((payload.node as { iteration: number }).iteration);
      };
      vi.advanceTimersByTime(100);
      expect(
        state.handleCommand(CommandType.SEND_MESSAGE, {
          ...snapshot.identity,
          target: "worker",
          content: "hello",
        }).success,
      ).toBe(true);
      const operation = (type: string) =>
        state.handleCommand(CommandType.AGENT_ENTITY, {
          ...snapshot.identity,
          operation: { type },
        });
      operation("stop");
      operation("start");
      vi.advanceTimersByTime(100);
      expect(iterations).toEqual([1, 2, 3]);
    } finally {
      state.dispose();
      vi.useRealTimers();
    }
  });
  it("rejects malformed and cross-project messages without committing a turn", () => {
    const state = new MockState();
    const first = state.handleCommand(CommandType.PROJECT_CREATE, { name: "first" }).data as {
      project: { project_id: string };
    };
    const second = state.handleCommand(CommandType.PROJECT_CREATE, { name: "second" }).data as {
      project: { project_id: string };
    };
    const create = (projectId: string, name: string) =>
      AgentSnapshotSchema.parse(
        state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
          project_id: projectId,
          name,
          manifest_ref: "mock.agent",
          system_prompt: "Default prompt",
          start: true,
        }).data,
      );
    const source = create(first.project.project_id, "worker");
    const foreign = create(second.project.project_id, "peer");
    const payload = { ...source.identity, target: "worker", content: "hello" };
    const before = state.handleCommand(CommandType.AGENT_ENTITY, {
      ...source.identity,
      operation: { type: "snapshot" },
    });
    for (const execute of [
      null,
      "bad",
      { name: "send_message", args: { target: foreign.identity.agent_id, content: "hello" } },
    ]) {
      expect(
        state.handleCommand(CommandType.SEND_MESSAGE, {
          ...payload,
          metadata: { execute_ability: execute },
        }).success,
      ).toBe(false);
    }
    const after = AgentSnapshotSchema.parse(
      state.handleCommand(CommandType.AGENT_ENTITY, {
        ...source.identity,
        operation: { type: "snapshot" },
      }).data,
    );
    expect(after.context).toEqual(AgentSnapshotSchema.parse(before.data).context);
    const inbox = state.handleCommand(CommandType.AGENT_ENTITY, {
      ...source.identity,
      operation: { type: "mailbox.list_inbox" },
    });
    expect(inbox.data).toEqual({ items: [], next_before_seq: null });
    const session = state.handleCommand(CommandType.AGENT_ENTITY, {
      ...source.identity,
      operation: { type: "context.create_session" },
    }).data as { session: { system_prompt: string } };
    expect(session.session.system_prompt).toBe("Default prompt");
    state.dispose();
  });
  it("preserves identity and history across stop/start, checks revisions and incarnations", () => {
    const state = new MockState();
    const created = state.handleCommand(CommandType.PROJECT_CREATE, { name: "entities" });
    const projectId = (created.data as { project: { project_id: string } }).project.project_id;
    const entity = (agentId: string, operation: Record<string, unknown>) =>
      state.handleCommand(CommandType.AGENT_ENTITY, {
        project_id: projectId,
        agent_id: agentId,
        operation,
      });
    const result = state.handleCommand(CommandType.AGENT_ENTITY_CREATE, {
      project_id: projectId,
      name: "worker",
      manifest_ref: "mock.agent",
      start: false,
    });
    expect(result.success).toBe(true);
    const stopped = AgentSnapshotSchema.parse(result.data);
    expect(stopped.runtime.availability).toBe("offline");
    expect(stopped.context.session_count).toBe(0);
    const agentId = stopped.identity.agent_id;
    const running = AgentSnapshotSchema.parse(
      entity(agentId, {
        type: "start",
        expected_revision: stopped.specification_revision,
      }).data,
    );
    expect(running.runtime.availability).toBe("online");
    expect(
      entity(agentId, { type: "stop", expected_revision: stopped.specification_revision }).error,
    ).toBe("agent_specification_conflict");
    expect(entity(agentId, { type: "stop", expected_incarnation_id: "obsolete" }).error).toBe(
      "agent_incarnation_conflict",
    );
    const stoppedAgain = AgentSnapshotSchema.parse(entity(agentId, { type: "stop" }).data);
    expect(stoppedAgain.specification.desired_state).toBe("stopped");
    expect(stoppedAgain.runtime.incarnation_id).toBeNull();
    expect(stoppedAgain.context.active_session_id).toBe(running.context.active_session_id);
    expect(entity(agentId, { type: "context.list_sessions" }).success).toBe(true);
    expect(entity(agentId, { type: "context.create_session" }).error).toBe("agent_not_running");
    const restarted = AgentSnapshotSchema.parse(entity(agentId, { type: "start" }).data);
    expect(restarted.identity).toEqual(running.identity);
    expect(restarted.runtime.incarnation_id).not.toBe(running.runtime.incarnation_id);
    expect(restarted.context.active_session_id).toBe(running.context.active_session_id);
    expect(
      state.handleCommand(CommandType.AGENT_ENTITY, {
        project_id: "other-project",
        agent_id: agentId,
        operation: { type: "snapshot" },
      }).success,
    ).toBe(false);
    state.dispose();
  });
});

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
