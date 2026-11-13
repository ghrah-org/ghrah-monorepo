// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { SessionInfoPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import type { AgentTarget } from "../scope.js";
import { useSessionsStore } from "./sessions.js";

const agent: AgentTarget = { projectId: "p1", agentId: "a1", agentName: "coder" };
const session = (id: string, overrides: Partial<SessionInfoPayload> = {}): SessionInfoPayload => ({
  project_id: "",
  agent_id: "",
  session_id: id,
  agent_name: "coder",
  name: `Session ${id}`,
  root_node_id: `root-${id}`,
  active_branch_id: `branch-${id}`,
  lifecycle: "open",
  system_prompt: "",
  created_at: "",
  metadata: {},
  message_count: 0,
  iteration_count: 0,
  ...overrides,
});

describe("useSessionsStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("stores multiple independent Root Sessions for one Agent", () => {
    const store = useSessionsStore();
    store.replaceAgentSessions(agent, [session("s1"), session("s2")], {
      explicitActiveSessionId: "s2",
    });
    expect(store.sessionsForAgent(agent).map((item) => item.target.sessionId)).toEqual([
      "s1",
      "s2",
    ]);
    expect(store.activeSessionId(agent)).toBe("s2");
  });

  it("isolates same session id across Agents and Projects", () => {
    const store = useSessionsStore();
    const other = { projectId: "p2", agentId: "a2", agentName: "coder" };
    store.replaceAgentSessions(agent, [session("s1")]);
    store.replaceAgentSessions(other, [session("s1")]);
    expect(store.sessions.size).toBe(2);
  });

  it("activation event updates the explicit active Session", () => {
    const store = useSessionsStore();
    store.onSessionActivated({
      project_id: "p1",
      agent_id: "a1",
      agent_name: "coder",
      session: session("s1"),
    });
    expect(store.activeSessionId(agent)).toBe("s1");
  });

  it("archive/delete removes only the exact Session", () => {
    const store = useSessionsStore();
    store.replaceAgentSessions(agent, [session("s1"), session("s2")], {
      explicitActiveSessionId: "s1",
    });
    store.removeSession({
      project_id: "p1",
      agent_id: "a1",
      agent_name: "coder",
      session_id: "s1",
    });
    expect(store.sessionsForAgent(agent).map((item) => item.target.sessionId)).toEqual(["s2"]);
    expect(store.activeSessionId(agent)).toBeNull();
  });
});
