// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { BranchInfoPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import type { SessionTarget } from "../scope.js";
import { useBranchesStore } from "./branches.js";

const session: SessionTarget = {
  projectId: "p1",
  agentId: "a1",
  agentName: "coder",
  sessionId: "s1",
};
const branch = (id: string): BranchInfoPayload => ({
  branch_id: id,
  session_id: "s1",
  name: id,
  head_node_id: `head-${id}`,
  lifecycle: "open",
  parent_branch_id: null,
  fork_point_node_id: null,
  created_at: "",
  metadata: {},
});

describe("useBranchesStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("stores multiple stable branches and active branch per Session", () => {
    const store = useBranchesStore();
    store.replaceSessionBranches(session, [branch("b1"), branch("b2")], {
      explicitActiveBranchId: "b2",
    });
    expect(store.branchesForSession(session).map((item) => item.target.branchId)).toEqual([
      "b1",
      "b2",
    ]);
    expect(store.activeBranchId(session)).toBe("b2");
  });

  it("isolates identical branch ids across Sessions", () => {
    const store = useBranchesStore();
    store.replaceSessionBranches(session, [branch("b1")]);
    const other = { ...session, sessionId: "s2" };
    store.replaceSessionBranches(other, [{ ...branch("b1"), session_id: "s2" }]);
    expect(store.branches.size).toBe(2);
  });

  it("activation event updates head and explicit active Branch", () => {
    const store = useBranchesStore();
    store.onBranchActivated({
      project_id: "p1",
      agent_id: "a1",
      cluster_id: "c1",
      agent_name: "coder",
      branch: branch("b1"),
    });
    expect(store.activeBranchId(session)).toBe("b1");
    expect(store.branchesForSession(session)[0].info.head_node_id).toBe("head-b1");
  });

  it("archive/delete removes only the exact Branch", () => {
    const store = useBranchesStore();
    store.replaceSessionBranches(session, [branch("b1"), branch("b2")], {
      explicitActiveBranchId: "b1",
    });
    store.removeBranch({
      project_id: "p1",
      agent_id: "a1",
      cluster_id: "c1",
      agent_name: "coder",
      session_id: "s1",
      branch_id: "b1",
    });
    expect(store.branchesForSession(session).map((item) => item.target.branchId)).toEqual(["b2"]);
    expect(store.activeBranchId(session)).toBeNull();
  });
});
