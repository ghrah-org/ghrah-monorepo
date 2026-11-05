// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import type { ChainTarget } from "../scope.js";
import { useChangesStore } from "./changes.js";

const target: ChainTarget = {
  projectId: "p1",
  agentId: "a1",
  agentName: "coder",
  sessionId: "s1",
  branchId: "b1",
};
const writeNode = ActionNodeSchema.parse({
  id: "n1",
  agent_name: "coder",
  session_id: "s1",
  created_on_branch_id: "b1",
  action_results: [
    {
      ability_name: "write_file",
      action_result: { outcome: "success", data: { file_path: "/a" } },
    },
  ],
});

describe("useChangesStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("records complete project/agent/session/branch scope", () => {
    const store = useChangesStore();
    store.onActionChainNode(target, writeNode);
    expect(store.changes[0]).toMatchObject({
      projectId: "p1",
      agentId: "a1",
      sessionId: "s1",
      branchId: "b1",
      agentName: "coder",
    });
  });

  it("deduplicates within scope but not across projects", () => {
    const store = useChangesStore();
    store.onActionChainNode(target, writeNode);
    store.onActionChainNode(target, writeNode);
    store.onActionChainNode({ ...target, projectId: "p2" }, writeNode);
    expect(store.changes).toHaveLength(2);
  });

  it("clears exact agent and project scopes", () => {
    const store = useChangesStore();
    store.onActionChainNode(target, writeNode);
    store.onActionChainNode({ ...target, projectId: "p2", agentId: "a2" }, writeNode);
    store.clearAgent(target);
    expect(store.changes.map((change) => change.projectId)).toEqual(["p2"]);
    store.clearProject("p2");
    expect(store.changes).toEqual([]);
  });
});
