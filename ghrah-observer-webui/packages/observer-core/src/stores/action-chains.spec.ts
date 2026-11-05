// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { ActionNodeSchema } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import type { ChainTarget } from "../scope.js";
import { useActionChainsStore } from "./action-chains.js";

const target = (overrides: Partial<ChainTarget> = {}): ChainTarget => ({
  projectId: "p1",
  agentId: "a1",
  agentName: "coder",
  sessionId: "s1",
  branchId: "b1",
  ...overrides,
});
const node = (id: string, sessionId = "s1", branchId = "b1") =>
  ActionNodeSchema.parse({
    id,
    agent_name: "coder",
    session_id: sessionId,
    created_on_branch_id: branchId,
  });

describe("useActionChainsStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("isolates histories by full ChainTarget", () => {
    const store = useActionChainsStore();
    const targets = [
      target(),
      target({ projectId: "p2" }),
      target({ sessionId: "s2" }),
      target({ branchId: "b2" }),
    ];
    targets.forEach((value, index) => {
      store.setChain(value, [node(`n${index}`, value.sessionId, value.branchId)]);
    });
    expect(targets.map((value) => store.getChain(value)[0]?.id)).toEqual(["n0", "n1", "n2", "n3"]);
  });

  it("replaces readback snapshots and merges increments idempotently", () => {
    const store = useActionChainsStore();
    store.setChain(target(), [node("n1"), node("n2")]);
    store.setChain(target(), [node("n2")]);
    const payload = {
      project_id: "p1",
      agent_id: "a1",
      cluster_id: "c1",
      agent_name: "coder",
      node: node("n3"),
    };
    store.onActionChainUpdated(payload);
    store.onActionChainUpdated(payload);
    expect(store.getChain(target()).map((item) => item.id)).toEqual(["n2", "n3"]);
  });

  it("routes an incremental node using envelope and node scope", () => {
    const store = useActionChainsStore();
    expect(
      store.onActionChainUpdated({
        project_id: "p1",
        agent_id: "a1",
        cluster_id: "c1",
        agent_name: "coder",
        node: node("n1"),
      }),
    ).toBe(true);
    expect(store.getChain(target())).toHaveLength(1);
  });

  it("rejects incomplete incremental scope and exposes diagnostics", () => {
    const store = useActionChainsStore();
    expect(
      store.onActionChainUpdated({
        project_id: "p1",
        agent_id: "a1",
        cluster_id: "c1",
        agent_name: "coder",
        node: node("n1", "", ""),
      }),
    ).toBe(false);
    expect(store.chains.size).toBe(0);
    expect(store.diagnostics[0]).toMatchObject({ reason: "incomplete_chain_target", nodeId: "n1" });
  });

  it("clears only the requested session, agent, or project", () => {
    const store = useActionChainsStore();
    const keep = target({ projectId: "p2", agentId: "a2" });
    const otherSession = target({ sessionId: "s2" });
    store.setChain(target(), [node("n1")]);
    store.setChain(otherSession, [node("n2", "s2")]);
    store.setChain(keep, [node("n3")]);
    store.clearSession(target());
    expect(store.getChain(target())).toEqual([]);
    expect(store.getChain(otherSession)).toHaveLength(1);
    store.clearAgent(otherSession);
    expect(store.getChain(otherSession)).toEqual([]);
    expect(store.getChain(keep)).toHaveLength(1);
  });
});
