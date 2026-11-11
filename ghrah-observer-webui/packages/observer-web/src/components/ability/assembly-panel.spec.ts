// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  setAbilityControlClientFactory,
  useAbilityAdminStore,
  useProjectsStore,
} from "@ghrah/observer-core";
import {
  AbilityAdminViewSchema,
  AbilityApiStatusSchema,
  AbilityChangesPageSchema,
  AbilityTreeChangeResultSchema,
  AbilityTreeViewSchema,
} from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AssemblyPanel from "./assembly-panel.vue";

const builtin = AbilityApiStatusSchema.parse({
  ability_source: "builtin",
  features: {
    tree: true,
    grants_write: true,
    versions_read: false,
    versions_write: false,
    packages_read: false,
    packages_install: false,
    surface_write: false,
  },
});

const legacy = AbilityApiStatusSchema.parse({
  ability_source: "legacy",
  legacy_deprecated: true,
  reason: "legacy",
  features: {
    tree: false,
    grants_write: false,
    versions_read: false,
    versions_write: false,
    packages_read: false,
    packages_install: false,
    surface_write: false,
  },
});

const admin = AbilityAdminViewSchema.parse({
  project_id: "p1",
  agent_id: "a1",
  revision: 4,
  lifecycle: { state: "running", requires_respawn: true, pending_reasons: ["respawn"] },
  grants: [
    {
      alias: "files",
      instance_id: "files",
      instance_revision: 1,
      version_digest: "sha256:abc",
      definition_digest: "sha256:def",
      members: ["read_file"],
      axes: [{ member: "operations/read_file", visible: true, available: false }],
      revision: 4,
      source: "admin",
    },
  ],
  surface: { current_runtime: null, pending_respawn: { added: ["write_file"], removed: [] } },
});

const restartAdmin = AbilityAdminViewSchema.parse({
  ...admin,
  surface: { current_runtime: [], pending_respawn: { added: ["write_file"], removed: [] } },
});

const tree = AbilityTreeViewSchema.parse({
  project_id: "p1",
  agent_id: "a1",
  run_id: "run-1",
  revision: 4,
  abilities: [
    {
      alias: "files",
      instance_id: "files",
      instance_revision: 1,
      definition_digest: "sha256:def",
      version_digest: "sha256:abc",
      revision: 4,
      allowed_roots: ["/workspace"],
      operations: [
        {
          key: "read_file",
          description: "Read",
          input_schema: {},
          output_schema: {},
          availability: "ready",
        },
      ],
    },
  ],
  bound_tools: [],
});

function setProjectAgents(agents: Array<{ name: string; agent_id: string }>) {
  useProjectsStore().setProjectsFromList([
    {
      project_id: "p1",
      name: "Project One",
      archived_at: null,
      agents: agents.map((agent) => ({ runtime_status: "running", ...agent })),
    } as never,
  ]);
}

function clientFactory(overrides: Record<string, unknown> = {}) {
  return () =>
    ({
      status: async () => builtin,
      admin: async () => admin,
      tree: async () => tree,
      changes: async () =>
        AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
      ...overrides,
    }) as never;
}

describe("AssemblyPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    setProjectAgents([{ name: "Coder", agent_id: "a1" }]);
  });
  afterEach(() => setAbilityControlClientFactory(null));

  it("shows a read-only builtin assembly and refreshes the selected agent", async () => {
    const adminRead = vi.fn(async () => admin);
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => builtin,
          admin: adminRead,
          tree: async () => tree,
          changes: async () =>
            AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
        }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    expect(wrapper.text()).toContain("Built-in ability control is available.");
    expect(wrapper.text()).toContain("Coder");
    expect(wrapper.text()).toContain("1 running");
    await wrapper.get("li button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    expect(adminRead).toHaveBeenCalledWith("a1");
    expect(wrapper.text()).not.toContain("Needs restart");
    expect(wrapper.find("form").exists()).toBe(false);
    wrapper.unmount();
  });

  it("edits an existing grant and selects a tool when writes are enabled", async () => {
    const putGrant = vi.fn(async (_agentId: string, request: unknown) => {
      void request;
      return AbilityTreeChangeResultSchema.parse({ view: tree, revision: 5 });
    });
    const bindTool = vi.fn(async () =>
      AbilityTreeChangeResultSchema.parse({ view: tree, revision: 6 }),
    );
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => builtin,
          admin: async () => admin,
          tree: async () => tree,
          changes: async () =>
            AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
          putGrant,
          bindTool,
        }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await wrapper.get("li button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    await wrapper
      .findAll('[role="tab"]')
      .find((item) => item.text() === "Grants")!
      .trigger("click");
    expect(wrapper.text()).toContain("Save grant");
    expect(wrapper.text()).toContain("Visible but not executable.");
    await wrapper.get("form").trigger("submit");
    expect(putGrant).toHaveBeenCalledTimes(1);
    expect(putGrant.mock.calls[0]?.[1]).toMatchObject({
      alias: "files",
      members: ["read_file"],
      expected_revision: 4,
      policy: null,
      whole_container: false,
      axes: [{ member: "operations/read_file", visible: true, available: false }],
    });
    await wrapper
      .findAll('[role="tab"]')
      .find((item) => item.text() === "Tools")!
      .trigger("click");
    expect(wrapper.text()).toContain("Selected for the model");
    expect(wrapper.find(".scheme-abilities form").exists()).toBe(false);
    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(bindTool).toHaveBeenCalledWith(
      "a1",
      expect.objectContaining({ alias: "files", member: "read_file", tool_name: "read_file" }),
    );
    wrapper.unmount();
  });

  it("hides grant controls when the server refuses writes", async () => {
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () =>
            AbilityApiStatusSchema.parse({
              ability_source: "builtin",
              features: {
                tree: true,
                grants_write: false,
                versions_read: false,
                versions_write: false,
                packages_read: false,
                packages_install: false,
                surface_write: false,
              },
            }),
          admin: async () => admin,
          tree: async () => tree,
          changes: async () =>
            AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
        }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await wrapper.get("li button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    await wrapper
      .findAll('[role="tab"]')
      .find((item) => item.text() === "Grants")!
      .trigger("click");
    expect(wrapper.text()).not.toContain("Save grant");
    expect(wrapper.text()).toContain("Members read_file");
    wrapper.unmount();
  });

  it("shows committed and ungranted package groups without assembly controls", async () => {
    const agentPackageGroups = vi.fn(async () => ({
      format: "ghrah.ability-control.package-groups.v1",
      scope: "agent",
      installed_available: false,
      project_id: "p1",
      agent_id: "a1",
      groups: [
        {
          namespace: "sample",
          name: "reader",
          full_name: "sample.reader",
          title: "Reader",
          versions: [
            {
              version: "1",
              origin: "builtin",
              lock_state: "builtin",
              problem: null,
              shadowed_builtin: false,
              member_count: 1,
              project: {
                committed: false,
                digest: null,
                instance_id: null,
                selected: false,
                instance_revision: null,
              },
              agent: { granted: false, alias: null },
            },
            {
              version: "2",
              origin: null,
              lock_state: null,
              problem: "definition_missing",
              shadowed_builtin: false,
              member_count: 1,
              project: {
                committed: true,
                digest: "sha256:missing",
                instance_id: "sample-reader",
                selected: true,
                instance_revision: 2,
              },
              agent: { granted: true, alias: "reader" },
            },
          ],
        },
      ],
    }));
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () =>
            AbilityApiStatusSchema.parse({
              ability_source: "builtin",
              features: {
                tree: true,
                grants_write: false,
                versions_read: true,
                versions_write: false,
                packages_read: true,
                packages_install: false,
                surface_write: false,
              },
            }),
          packageGroups: async () => ({
            scope: "distribution",
            installed_available: false,
            groups: [],
          }),
          admin: async () => admin,
          tree: async () => tree,
          changes: async () =>
            AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
          agentPackageGroups,
        }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await wrapper.get("li button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    await wrapper
      .findAll('[role="tab"]')
      .find((item) => item.text() === "Versions")!
      .trigger("click");
    await flushPromises();
    expect(agentPackageGroups).toHaveBeenCalledWith("a1");
    expect(wrapper.text()).toContain("Reader");
    expect(wrapper.text()).toContain("Not committed");
    expect(wrapper.text()).toContain("Not granted");
    expect(wrapper.text()).toContain("Committed");
    expect(wrapper.text()).toContain("Granted as reader");
    expect(wrapper.text()).toContain("no longer in the distribution");
    expect(wrapper.text()).not.toContain(
      "Version selection and the package catalog are not available.",
    );
    expect(wrapper.find("form").exists()).toBe(false);
    wrapper.unmount();
  });

  it("keeps a legacy source on the status explanation", async () => {
    const adminRead = vi.fn();
    setAbilityControlClientFactory(
      () => ({ status: async () => legacy, admin: adminRead }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await wrapper.get("li button").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Legacy ability source is outside the new grant controls.");
    expect(adminRead).not.toHaveBeenCalled();
    expect(wrapper.find("form").exists()).toBe(false);
    wrapper.unmount();
  });

  it("reads the list on open and never shows zero before an agent is loaded", async () => {
    const pending: Array<(value: typeof admin) => void> = [];
    const adminRead = vi.fn(
      () =>
        new Promise<typeof admin>((resolve) => {
          pending.push(resolve);
        }),
    );
    const treeRead = vi.fn(async () => tree);
    setAbilityControlClientFactory(clientFactory({ admin: adminRead, tree: treeRead }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledWith("a1"));
    expect(treeRead).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Reading…");
    expect(wrapper.text()).not.toContain("0 grants");

    pending.splice(0).forEach((resolve) => resolve(admin));
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    expect(wrapper.text()).toContain("0 tools");
    expect(wrapper.text()).not.toContain("Reading…");
    wrapper.unmount();
  });

  it("shows a reading row before the panel has requested anything", async () => {
    const adminRead = vi.fn(async () => admin);
    setAbilityControlClientFactory(
      () =>
        ({
          status: () => new Promise(() => undefined),
          admin: adminRead,
          tree: async () => tree,
          changes: async () =>
            AbilityChangesPageSchema.parse({ changes: [], next_after_seq: 0, has_more: false }),
        }) as never,
    );
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    expect(wrapper.text()).toContain("Coder");
    expect(wrapper.text()).toContain("Reading…");
    expect(wrapper.text()).not.toContain("0 grants");
    expect(wrapper.text()).not.toContain("—");
    expect(adminRead).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("shows an unavailable row and retries it in place", async () => {
    const adminRead = vi.fn(async () => Promise.reject(new Error("admin down")));
    const treeRead = vi.fn(async () => Promise.reject(new Error("no run")));
    setAbilityControlClientFactory(clientFactory({ admin: adminRead, tree: treeRead }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("Retry"));
    expect(wrapper.text()).toContain("—");
    expect(wrapper.text()).not.toContain("0 grants");
    expect(adminRead).toHaveBeenCalledTimes(1);

    await wrapper.get(".assembly-agent-retry").trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(2));
    expect(wrapper.find(".assembly-agent-retry").exists()).toBe(true);
    wrapper.unmount();
  });

  it("does not show a restart badge when the runtime surface is unobserved", async () => {
    setAbilityControlClientFactory(clientFactory());
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    expect(wrapper.text()).not.toContain("Needs restart");
    wrapper.unmount();
  });

  it("shows the restart badge when the tool surface needs a restart", async () => {
    setAbilityControlClientFactory(clientFactory({ admin: async () => restartAdmin }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("Needs restart"));
    wrapper.unmount();
  });

  it("keeps the out-of-date badge off rows that are still loading", async () => {
    const pending: Array<(value: typeof admin) => void> = [];
    const adminRead = vi.fn(
      () =>
        new Promise<typeof admin>((resolve) => {
          pending.push(resolve);
        }),
    );
    setAbilityControlClientFactory(clientFactory({ admin: adminRead }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(1));

    void useAbilityAdminStore().markStale("p1", "a1");
    await flushPromises();
    expect(wrapper.text()).toContain("Reading…");
    expect(wrapper.text()).not.toContain("Out of date");

    pending.splice(0).forEach((resolve) => resolve(admin));
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    expect(wrapper.text()).not.toContain("Out of date");
    wrapper.unmount();
  });

  it("shows the out-of-date badge on a loaded row after it is marked stale", async () => {
    setAbilityControlClientFactory(clientFactory());
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 grants"));
    expect(wrapper.text()).not.toContain("Out of date");

    void useAbilityAdminStore().markStale("p1", "a1");
    await flushPromises();
    await vi.waitFor(() => expect(wrapper.text()).toContain("Out of date"));
    wrapper.unmount();
  });

  it("prefetches at most the first twenty agents when the panel opens", async () => {
    const ids = Array.from({ length: 25 }, (_, index) => `a${index + 1}`);
    setProjectAgents(
      ids.map((agentId, index) => ({ name: `Agent ${index + 1}`, agent_id: agentId })),
    );
    const adminRead = vi.fn(async (_agentId: string) => admin);
    setAbilityControlClientFactory(clientFactory({ admin: adminRead }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(20));
    expect(adminRead.mock.calls.map(([agentId]) => agentId)).toEqual(ids.slice(0, 20));
    expect(wrapper.text()).toContain("Showing the first 20 of 25 agents.");
    wrapper.unmount();
  });

  it("prefetches agents added to the visible list while the panel is open", async () => {
    const adminRead = vi.fn(async (_agentId: string) => admin);
    setAbilityControlClientFactory(clientFactory({ admin: adminRead }));
    const wrapper = mount(AssemblyPanel, { props: { projectId: "p1" } });
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledWith("a1"));

    adminRead.mockClear();
    setProjectAgents([
      { name: "Coder", agent_id: "a1" },
      { name: "Runner", agent_id: "a2" },
    ]);
    await flushPromises();
    await vi.waitFor(() => expect(adminRead).toHaveBeenCalledTimes(1));
    expect(adminRead).toHaveBeenCalledWith("a2");
    expect(wrapper.text()).toContain("Runner");
    wrapper.unmount();
  });
});
