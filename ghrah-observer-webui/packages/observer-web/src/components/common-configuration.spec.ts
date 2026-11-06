// @vitest-environment happy-dom
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import {
  ProjectInfoPayloadSchema,
  ManifestResolveAgentResponsePayloadSchema,
} from "@ghrah/protocol";
import { useObserver } from "@/composables/useObserver";
import ProjectIsolationView from "./project-isolation-view.vue";
import WorkspaceManagement from "./workspace-management.vue";
import AgentManifestPreview from "./config/agent-manifest-preview.vue";
import fixture from "../../../protocol/src/__fixtures__/manifest-responses.json";
import workspace from "../../../protocol/src/__fixtures__/workspace-detail.json";
vi.mock("@/composables/usePlugins", () => ({ usePlugins: vi.fn() }));
let w: VueWrapper | undefined;
const project = () => ProjectInfoPayloadSchema.parse({ project_id: "p1", name: "Project" });
beforeEach(() => {
  setActivePinia(createPinia());
  useObserver().client.value = null;
  useObserver().connection.setConnected();
});
afterEach(() => {
  w?.unmount();
  w = undefined;
  useObserver().client.value = null;
});
it("distinguishes null effect inheritance, an empty allowlist and explicit false settings", async () => {
  const p = project();
  p.isolation = {
    agent_path_grants: {},
    agent_private_dir: false,
    effect_allowlist: null,
    hitl_override: null,
    task_scope: false,
  };
  w = mount(ProjectIsolationView, { props: { project: p } });
  expect(w.get('[data-field="effect_allowlist"]').text()).toContain("inherit the global");
  expect(w.get('[data-field="agent_private_dir"]').text()).toBe("Disabled");
  expect(w.get('[data-field="task_scope"]').text()).toBe("Disabled");
  await w.setProps({ project: { ...p, isolation: { ...p.isolation, effect_allowlist: [] } } });
  expect(w.get('[data-field="effect_allowlist"]').text()).toContain("no effect actions");
});
it("shows declaration and Agent override sources alongside server-resolved permissions", async () => {
  const observer = useObserver();
  const getAbility = vi.fn().mockResolvedValue({ success: true, data: fixture.ability });
  observer.client.value = { getAbility } as unknown as NonNullable<typeof observer.client.value>;
  w = mount(AgentManifestPreview, {
    props: {
      preview: ManifestResolveAgentResponsePayloadSchema.parse(fixture.resolved_agent),
      source:
        "abilities:\n  - ref: audit.fs.read_file\n    permissions:\n      shell_access: true\n",
    },
  });
  await flushPromises();
  expect(getAbility).toHaveBeenCalledWith("audit.fs.read_file");
  expect(w.text()).toContain("Ability Manifest declaration");
  expect(w.text()).toContain("Agent Manifest override");
  expect(w.text()).toContain("Merged declaration permissions");
  await w.setProps({
    preview: ManifestResolveAgentResponsePayloadSchema.parse(fixture.resolved_agent),
  });
  await flushPromises();
  expect(getAbility).toHaveBeenCalledTimes(2);
});
it("does not read legacy declarations when preview uses builtin source", async () => {
  const observer = useObserver();
  const getAbility = vi.fn();
  observer.client.value = { getAbility } as unknown as NonNullable<typeof observer.client.value>;
  w = mount(AgentManifestPreview, {
    props: {
      preview: ManifestResolveAgentResponsePayloadSchema.parse({
        ...fixture.resolved_agent,
        ability_source: "builtin",
      }),
      source: "abilities:\n  - ref: audit.fs.read_file\n",
    },
  });
  await flushPromises();
  expect(getAbility).not.toHaveBeenCalled();
  expect(w.text()).toContain("builtin resolver");
});
it("requires directory approval, then registers and rereads the Workspace record", async () => {
  const observer = useObserver();
  const row = {
    workspace_id: "w1",
    name: "Approved",
    provider_type: "plain",
    locator: workspace.locator,
  };
  const client = {
    listWorkspaces: vi.fn().mockResolvedValue({ success: true, data: { workspaces: [row] } }),
    getWorkspace: vi.fn().mockResolvedValue({ success: true, data: workspace }),
    registerWorkspace: vi.fn().mockResolvedValue({ success: true, data: { workspace_id: "w1" } }),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  w = mount(WorkspaceManagement, { props: { project: project() } });
  await flushPromises();
  const register = () =>
    w!.findAll("button").find((x) => x.text() === "Register approved directory")!;
  await w.findAll("input")[0].setValue("Approved");
  await w.findAll("input")[1].setValue("/workspace/docs");
  expect(register().attributes("disabled")).toBeDefined();
  await w.get('input[type="checkbox"]').setValue(true);
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(client.registerWorkspace).toHaveBeenCalledWith("/workspace/docs", "Approved");
  expect(client.getWorkspace).toHaveBeenCalledWith("w1");
  expect(w.text()).toContain("Registered and read back");
  expect(w.text()).toContain("Not referenced by this project");
  expect(w.text()).toContain("Directory exists");
  expect(w.text()).toContain("No");
});
it("preserves failed Workspace registration input and blocks a duplicate uncertain write", async () => {
  const observer = useObserver();
  const client = {
    listWorkspaces: vi.fn().mockResolvedValue({ success: true, data: { workspaces: [] } }),
    registerWorkspace: vi.fn().mockRejectedValue(new Error("Connection lost")),
  };
  observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
  w = mount(WorkspaceManagement, { props: { project: project() } });
  await flushPromises();
  await w.findAll("input")[1].setValue("/keep");
  await w.get('input[type="checkbox"]').setValue(true);
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(w.findAll("input")[1].element).toHaveProperty("value", "/keep");
  expect(
    w
      .findAll("button")
      .find((x) => x.text() === "Register approved directory")!
      .attributes("disabled"),
  ).toBeDefined();
  expect(client.registerWorkspace).toHaveBeenCalledTimes(1);
});
