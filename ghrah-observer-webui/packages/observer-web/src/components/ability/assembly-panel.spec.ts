// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { setAbilityControlClientFactory, useProjectsStore } from "@ghrah/observer-core";
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

describe("AssemblyPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useProjectsStore().setProjectsFromList([
      {
        project_id: "p1",
        name: "Project One",
        archived_at: null,
        agents: [{ name: "Coder", agent_id: "a1", runtime_status: "running" }],
      } as never,
    ]);
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
    expect(wrapper.text()).toContain("Needs restart");
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
    expect(wrapper.text()).toContain("These changes apply the next time this agent starts.");
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
});
