// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema, type BranchInfoPayload, type SessionInfoPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import {
  getVisibleNodes,
  useActionChainsStore,
  useBranchesStore,
  useSessionsStore,
} from "./index.js";

function node(overrides: Record<string, unknown> = {}) {
  return ActionNodeSchema.parse(overrides);
}

function branchInfo(overrides: Partial<BranchInfoPayload>): BranchInfoPayload {
  return {
    branch_id: "b",
    session_id: "s1",
    name: "main",
    head_node_id: "root",
    lifecycle: "open",
    parent_branch_id: null,
    fork_point_node_id: null,
    created_at: "",
    metadata: {},
    ...overrides,
  } as BranchInfoPayload;
}

function sessionInfo(overrides: Partial<SessionInfoPayload>): SessionInfoPayload {
  return {
    session_id: "s1",
    agent_name: "alpha",
    name: "Session 1",
    root_node_id: "root",
    active_branch_id: "main",
    lifecycle: "open",
    ...overrides,
  } as SessionInfoPayload;
}

const agent = { projectId: "p1", agentId: "a1", agentName: "alpha" };
const otherAgent = { projectId: "p1", agentId: "a2", agentName: "beta" };
const sessionTarget = { ...agent, sessionId: "s1" };

describe("getVisibleNodes", () => {
  beforeEach(() => setActivePinia(createPinia()));

  function setup() {
    return {
      sessions: useSessionsStore(),
      branches: useBranchesStore(),
      chains: useActionChainsStore(),
    };
  }

  it("walks single branch from its head to root", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.setActiveSession(agent, "s1");
    store.branches.replaceSessionBranches(
      sessionTarget,
      [branchInfo({ branch_id: "main", head_node_id: "n2" })],
      { explicitActiveBranchId: "main" },
    );
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "root", parent_id: null, timestamp: "t0", session_id: "s1" }),
      node({ id: "n1", parent_id: "root", timestamp: "t1", session_id: "s1" }),
      node({ id: "n2", parent_id: "n1", timestamp: "t2", session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent);
    expect(visible.map((item) => item.id)).toEqual(["root", "n1", "n2"]);
  });

  it("merges ancestors of all branches and dedupes shared nodes", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.setActiveSession(agent, "s1");
    store.branches.replaceSessionBranches(
      sessionTarget,
      [
        branchInfo({ branch_id: "main", head_node_id: "n2" }),
        branchInfo({ branch_id: "retry", name: "retry", head_node_id: "r1" }),
      ],
      { explicitActiveBranchId: "main" },
    );
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "root", parent_id: null, timestamp: "t0", iteration: 0, session_id: "s1" }),
      node({ id: "n1", parent_id: "root", timestamp: "t1", iteration: 1, session_id: "s1" }),
      node({ id: "n2", parent_id: "n1", timestamp: "t2", iteration: 2, session_id: "s1" }),
    ]);
    store.chains.setChain({ ...sessionTarget, branchId: "retry" }, [
      node({ id: "r1", parent_id: "root", timestamp: "t3", iteration: 3, session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent, {
      branchId: null,
    });
    // root 共享去重：root, n1, n2, r1
    expect(visible.map((item) => item.id)).toEqual(["root", "n1", "n2", "r1"]);
  });

  it("isolates roots across sessions of the same agent", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [
      sessionInfo({ session_id: "s1", active_branch_id: "main" }),
      sessionInfo({ session_id: "s2", active_branch_id: "main" }),
    ]);
    store.sessions.setActiveSession(agent, "s1");
    for (const sessionId of ["s1", "s2"]) {
      store.branches.replaceSessionBranches(
        { ...agent, sessionId },
        [branchInfo({ session_id: sessionId, head_node_id: `${sessionId}-head` })],
        { explicitActiveBranchId: "main" },
      );
      store.chains.setChain({ ...agent, sessionId, branchId: "main" }, [
        node({ id: `${sessionId}-root`, parent_id: null, timestamp: "t0", session_id: sessionId }),
        node({
          id: `${sessionId}-head`,
          parent_id: `${sessionId}-root`,
          timestamp: "t1",
          session_id: sessionId,
        }),
      ]);
    }
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent);
    expect(visible.map((item) => item.id)).toEqual(["s1-root", "s1-head"]);
  });

  it("rejects cross-session parent edges and keeps known prefix", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.setActiveSession(agent, "s1");
    store.branches.replaceSessionBranches(sessionTarget, [branchInfo({ head_node_id: "n1" })], {
      explicitActiveBranchId: "main",
    });
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "foreign-root", parent_id: null, timestamp: "t0", session_id: "s2" }),
      node({ id: "n1", parent_id: "foreign-root", timestamp: "t1", session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent);
    // 跨 Session parent 边被拒绝：保留 n1，不引入 s2 Root
    expect(visible.map((item) => item.id)).toEqual(["n1"]);
  });

  it("ignores missing parents and stops that path", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.setActiveSession(agent, "s1");
    store.branches.replaceSessionBranches(sessionTarget, [branchInfo({ head_node_id: "n2" })], {
      explicitActiveBranchId: "main",
    });
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "n2", parent_id: "unknown-parent", timestamp: "t2", session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent);
    expect(visible.map((item) => item.id)).toEqual(["n2"]);
  });

  it("isolates same-name agents across projects via AgentKey", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.replaceAgentSessions(otherAgent, [
      sessionInfo({ agent_name: "beta", active_branch_id: "main" }),
    ]);
    store.sessions.setActiveSession(agent, "s1");
    store.sessions.setActiveSession(otherAgent, "s1");
    store.branches.replaceSessionBranches(
      sessionTarget,
      [branchInfo({ head_node_id: "p1-head" })],
      { explicitActiveBranchId: "main" },
    );
    store.branches.replaceSessionBranches(
      { ...otherAgent, sessionId: "s1" },
      [branchInfo({ head_node_id: "p2-head" })],
      { explicitActiveBranchId: "main" },
    );
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "p1-head", parent_id: null, timestamp: "t0", session_id: "s1" }),
    ]);
    store.chains.setChain({ ...otherAgent, sessionId: "s1", branchId: "main" }, [
      node({ id: "p2-head", parent_id: null, timestamp: "t0", session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent);
    expect(visible.map((item) => item.id)).toEqual(["p1-head"]);
  });

  it("excludes deleted branches from all-branches mode", () => {
    const store = setup();
    store.sessions.replaceAgentSessions(agent, [sessionInfo({ active_branch_id: "main" })]);
    store.sessions.setActiveSession(agent, "s1");
    store.branches.replaceSessionBranches(
      sessionTarget,
      [
        branchInfo({ branch_id: "main", head_node_id: "n1" }),
        branchInfo({ branch_id: "dead", name: "dead", head_node_id: "d1", lifecycle: "deleted" }),
      ],
      { explicitActiveBranchId: "main" },
    );
    store.chains.setChain({ ...sessionTarget, branchId: "main" }, [
      node({ id: "root", parent_id: null, timestamp: "t0", session_id: "s1" }),
      node({ id: "n1", parent_id: "root", timestamp: "t1", session_id: "s1" }),
    ]);
    store.chains.setChain({ ...sessionTarget, branchId: "dead" }, [
      node({ id: "d1", parent_id: null, timestamp: "t2", session_id: "s1" }),
    ]);
    const visible = getVisibleNodes(store.sessions, store.branches, store.chains, agent, {
      branchId: null,
    });
    expect(visible.map((item) => item.id)).toEqual(["root", "n1"]);
  });
});
