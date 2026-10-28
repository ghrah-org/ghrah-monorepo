import type { ProjectInfoPayload } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useProjectsStore } from "./projects.js";

function makeProject(overrides: Partial<ProjectInfoPayload> = {}): ProjectInfoPayload {
  return {
    project_id: "p1",
    name: "proj-1",
    description: "",
    project_root_locator: "",
    manifest_ref: "",
    cluster_ids: [],
    workspaces: [],
    agents: [],
    task_ids: [],
    status: "active",
    recovery: "resume",
    version: 1,
    created_at: "",
    updated_at: "",
    archived_at: null,
    deleted_at: null,
    ...overrides,
    isolation: overrides.isolation ?? {
      agent_path_grants: {},
      agent_private_dir: true,
      effect_allowlist: null,
      hitl_override: null,
      task_scope: true,
    },
  };
}

describe("useProjectsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts empty", () => {
    const store = useProjectsStore();
    expect(store.projects.size).toBe(0);
    expect(store.activeProjectId).toBeNull();
    expect(store.activeProject).toBeNull();
  });

  it("onProjectEvent upserts for created/updated/paused/resumed/stopped", () => {
    const store = useProjectsStore();
    store.onProjectEvent({ project: makeProject() });
    expect(store.projects.size).toBe(1);
    store.onProjectEvent({ project: makeProject({ status: "paused", version: 2 }) });
    expect(store.projects.size).toBe(1);
    expect(store.projects.get("p1")?.status).toBe("paused");
  });

  it("onProjectEvent handles agent envelope {project, agent_name}", () => {
    const store = useProjectsStore();
    store.onProjectEvent({
      project: makeProject({
        agents: [
          {
            name: "agent-1",
            cluster_id: "c1",
            manifest_ref: "",
            instance_manifest_path: "",
            system_prompt: "",
            path_grants: [],
            desired_state: "running",
            runtime_status: "pending",
            runtime_error: "",
          },
        ],
      }),
      agent_name: "agent-1",
    });
    expect(store.projects.get("p1")?.agents).toHaveLength(1);
  });

  it("onProjectDeleted removes project and resets activeProjectId", () => {
    const store = useProjectsStore();
    store.onProjectEvent({ project: makeProject() });
    store.setActiveProject("p1");
    store.onProjectDeleted({ project: makeProject() });
    expect(store.projects.size).toBe(0);
    expect(store.activeProjectId).toBeNull();
  });

  it("setProjectsFromList replaces map", () => {
    const store = useProjectsStore();
    store.onProjectEvent({ project: makeProject({ project_id: "old" }) });
    store.setProjectsFromList([
      makeProject({ project_id: "p1" }),
      makeProject({ project_id: "p2" }),
    ]);
    expect([...store.projects.keys()].sort()).toEqual(["p1", "p2"]);
  });

  it("activeProject follows activeProjectId", () => {
    const store = useProjectsStore();
    store.setProjectsFromList([
      makeProject({ project_id: "p1" }),
      makeProject({ project_id: "p2", name: "two" }),
    ]);
    store.setActiveProject("p2");
    expect(store.activeProject?.name).toBe("two");
  });

  it("treats stopped Projects as definition-operable but archived Projects as unavailable", () => {
    const store = useProjectsStore();
    store.setProjectsFromList([
      makeProject({ project_id: "stopped", status: "stopped" }),
      makeProject({ project_id: "archived", status: "stopped", archived_at: "2026-09-09" }),
    ]);
    expect(store.isProjectOperable("stopped")).toBe(true);
    expect(store.isProjectOperable("archived")).toBe(false);
  });

  it("replaces active and archived Project result buckets independently", () => {
    const store = useProjectsStore();
    store.replaceProjects([makeProject({ project_id: "active" })], false);
    store.replaceProjects(
      [makeProject({ project_id: "archived", archived_at: "2026-09-09" })],
      true,
    );
    store.setActiveProject("active");
    store.replaceProjects([], false);
    expect(store.projectList).toEqual([]);
    expect(store.archivedProjectList.map((project) => project.project_id)).toEqual(["archived"]);
    expect(store.activeProjectId).toBeNull();
  });

  it("clearAll resets", () => {
    const store = useProjectsStore();
    store.onProjectEvent({ project: makeProject() });
    store.setActiveProject("p1");
    store.clearAll();
    expect(store.projects.size).toBe(0);
    expect(store.activeProjectId).toBeNull();
  });
});
