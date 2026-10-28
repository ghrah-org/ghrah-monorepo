import type { AgentConfigPayload, ProjectInfoPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { agentKey } from "../scope.js";
import { useAgentsStore } from "./agents.js";

const config = (name: string): AgentConfigPayload => ({
  name,
  endpoint_name: null,
  description: "",
  system_prompt: "",
  max_iterations: 10,
});

const project = (overrides: Partial<ProjectInfoPayload> = {}) =>
  ({ status: "active", archived_at: null, ...overrides }) as ProjectInfoPayload;

describe("useAgentsStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("isolates same-name agents by project and stable id", () => {
    const store = useAgentsStore();
    store.replaceProjectAgents(
      "p1",
      [{ name: "coder", agent_id: "a1", runtime_state: "running" }],
      project(),
    );
    store.replaceProjectAgents(
      "p2",
      [{ name: "coder", agent_id: "a2", runtime_state: "running" }],
      project(),
    );
    expect(store.agents.size).toBe(2);
    expect(store.agents.get(agentKey({ projectId: "p1", agentId: "a1" }))?.agentName).toBe("coder");
    expect(store.agents.get(agentKey({ projectId: "p2", agentId: "a2" }))?.agentName).toBe("coder");
  });

  it("replaces only one project bucket and reports disappeared agents", () => {
    const store = useAgentsStore();
    store.replaceProjectAgents(
      "p1",
      [
        { name: "one", agent_id: "a1" },
        { name: "two", agent_id: "a2" },
      ],
      project(),
    );
    store.replaceProjectAgents("p2", [{ name: "other", agent_id: "b1" }], project());
    const removed = store.replaceProjectAgents("p1", [{ name: "two", agent_id: "a2" }], project());
    expect(removed).toEqual([{ projectId: "p1", agentId: "a1", agentName: "one" }]);
    expect(store.agentsForProject("p1")).toHaveLength(1);
    expect(store.agentsForProject("p2")).toHaveLength(1);
  });

  it("an empty list clears exactly its project bucket", () => {
    const store = useAgentsStore();
    store.replaceProjectAgents("p1", [{ name: "one", agent_id: "a1" }], project());
    store.replaceProjectAgents("p2", [{ name: "two", agent_id: "a2" }], project());
    store.replaceProjectAgents("p1", [], project());
    expect(store.agentsForProject("p1")).toEqual([]);
    expect(store.agentsForProject("p2")).toHaveLength(1);
  });

  it("defaults missing runtime state to stopped", () => {
    const store = useAgentsStore();
    store.replaceProjectAgents("p1", [{ name: "one", agent_id: "a1" }], project());
    expect(store.getAgent({ projectId: "p1", agentId: "a1" })?.runtimeStatus).toBe("stopped");
    expect(store.activeAgents).toEqual([]);
  });

  it("never projects running for stopped or archived projects", () => {
    const store = useAgentsStore();
    store.replaceProjectAgents(
      "p1",
      [{ name: "one", agent_id: "a1", runtime_state: "running" }],
      project({ status: "stopped" }),
    );
    store.replaceProjectAgents(
      "p2",
      [{ name: "two", agent_id: "a2", runtime_state: "running" }],
      project({ archived_at: "2026-09-09" }),
    );
    store.onAgentSpawned(
      {
        project_id: "p3",
        agent_id: "a3",
        cluster_id: "c1",
        name: "three",
        incarnation_id: "i1",
        recovery_mode: "fresh",
        config: config("three"),
      },
      project({ status: "stopped" }),
    );
    expect(store.activeAgents).toEqual([]);
  });

  it("selection is a structured target and is cleared on exact removal", () => {
    const store = useAgentsStore();
    const target = { projectId: "p1", agentId: "a1", agentName: "coder" };
    store.replaceProjectAgents("p1", [{ name: "coder", agent_id: "a1" }], project());
    store.selectAgent(target);
    expect(store.selectedAgentTarget).toEqual(target);
    store.removeAgent(target);
    expect(store.selectedAgentTarget).toBeNull();
  });

  it("spawn and terminate events require complete project-scoped identity", () => {
    const store = useAgentsStore();
    store.onAgentSpawned({
      project_id: "",
      agent_id: "",
      cluster_id: "",
      name: "bad",
      incarnation_id: "",
      recovery_mode: "",
      config: config("bad"),
    });
    expect(store.agents.size).toBe(0);
    store.onAgentSpawned({
      project_id: "p1",
      agent_id: "a1",
      cluster_id: "c1",
      name: "coder",
      incarnation_id: "i1",
      recovery_mode: "fresh",
      config: config("coder"),
    });
    expect(store.activeAgents).toHaveLength(1);
    store.onAgentTerminated({
      project_id: "p1",
      agent_id: "a1",
      cluster_id: "c1",
      name: "coder",
      incarnation_id: "i1",
    });
    expect(store.getAgent({ projectId: "p1", agentId: "a1" })?.runtimeStatus).toBe("stopped");
  });
});
