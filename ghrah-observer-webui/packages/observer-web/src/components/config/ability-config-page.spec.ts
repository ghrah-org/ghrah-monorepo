// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { HttpError, setAbilityControlClientFactory } from "@ghrah/observer-core";
import { AbilityApiStatusSchema, AbilityPackageVersionProfileViewSchema } from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AbilityConfigPage from "./ability-config-page.vue";

function status(source: "builtin" | "legacy", tree: boolean, packagesRead = false) {
  return AbilityApiStatusSchema.parse({
    ability_source: source,
    legacy_deprecated: source === "legacy",
    features: {
      tree,
      grants_write: tree,
      versions_read: packagesRead,
      versions_write: false,
      packages_read: packagesRead,
      packages_install: false,
      surface_write: false,
    },
  });
}

function groups(version = "1") {
  return {
    format: "ghrah.ability-control.package-groups.v1",
    scope: "distribution",
    installed_available: false,
    project_id: null,
    agent_id: null,
    groups: [
      {
        namespace: "sample",
        name: "reader",
        full_name: "sample.reader",
        title: "Reader",
        versions: [
          {
            version,
            origin: "builtin",
            lock_state: "builtin",
            problem: null,
            shadowed_builtin: false,
            member_count: 2,
            project: null,
            agent: null,
          },
        ],
      },
    ],
  };
}

function versionProfile(version = "1.0.0") {
  return AbilityPackageVersionProfileViewSchema.parse({
    namespace: "sample",
    name: "reader",
    full_name: "sample.reader",
    version,
    origin: "builtin",
    definition_origin: "source",
    lock_state: "builtin",
    title: "Reader",
  });
}

const distributionGroups = groups();

describe("AbilityConfigPage", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => setAbilityControlClientFactory(null));

  it("shows builtin status and keeps package management closed", async () => {
    const read = vi.fn(async () => status("builtin", true));
    setAbilityControlClientFactory(() => ({ status: read }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("Built-in ability control is available.");
    expect(wrapper.text()).toContain("The package catalog is not available from this server.");
    expect(wrapper.text()).toContain("Package install is not available.");
    expect(wrapper.text()).toContain("No package list is shown.");
    expect(wrapper.find("textarea").exists()).toBe(false);
    expect(wrapper.find("form").exists()).toBe(false);
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(read).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("lists distribution package groups without install controls", async () => {
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => distributionGroups);
    setAbilityControlClientFactory(() => ({ status: read, packageGroups }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("Reader");
    expect(wrapper.text()).toContain("sample.reader");
    expect(wrapper.text()).toContain("Built in");
    expect(wrapper.text()).toContain("Lock builtin");
    expect(wrapper.text()).toContain("2 members");
    expect(wrapper.text()).not.toContain("No package list is shown.");
    expect(wrapper.find("form").exists()).toBe(false);
    expect(wrapper.findAll("button").map((button) => button.text())).toEqual([
      "Refresh status",
      "Details",
    ]);
    wrapper.unmount();
  });

  it("does not treat an unloaded catalog as empty or unavailable", async () => {
    let release: (value: ReturnType<typeof status>) => void = () => undefined;
    const read = vi.fn(
      () =>
        new Promise<ReturnType<typeof status>>((resolve) => {
          release = resolve;
        }),
    );
    setAbilityControlClientFactory(() => ({ status: read }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("Loading ability assembly…");
    expect(wrapper.text()).not.toContain("No package list is shown.");
    expect(wrapper.text()).not.toContain("The package catalog is not available from this server.");
    expect(wrapper.text()).not.toContain("Package management is not enabled.");
    expect(wrapper.text()).not.toContain("Ability assembly is unavailable.");
    release(status("builtin", true));
    await flushPromises();
    expect(wrapper.text()).toContain("The package catalog is not available from this server.");
    wrapper.unmount();
  });

  it("loads an expanded version once and reloads it when the catalog is refreshed", async () => {
    const profile = versionProfile();
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => groups("1.0.0"));
    const packageVersion = vi.fn(async () => profile);
    setAbilityControlClientFactory(
      () => ({ status: read, packageGroups, packageVersion }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    await wrapper.get("button[aria-expanded='false']").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);
    expect(packageVersion).toHaveBeenCalledWith("sample", "reader", "1.0.0");
    expect(wrapper.text()).toContain("This version declares no operations.");
    expect(wrapper.text()).toContain("This version is not an MCP package.");
    expect(wrapper.text()).toContain("No install verification is recorded for this version.");
    expect(wrapper.findAll("button").some((button) => button.text() === "Test connection")).toBe(
      false,
    );
    await wrapper.get("button[aria-expanded='true']").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);
    await wrapper.get("button[aria-expanded='false']").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);
    await wrapper.get("button").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[aria-expanded='true']").exists()).toBe(true);
    wrapper.unmount();
  });

  it("retries a failed version without showing the transport message", async () => {
    const profile = versionProfile();
    const packageVersion = vi
      .fn()
      .mockRejectedValueOnce(
        new HttpError({ code: "not_found", status: 404, message: "missing /home/secret" }),
      )
      .mockResolvedValueOnce(profile);
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => status("builtin", true, true),
          packageGroups: async () => groups("1.0.0"),
          packageVersion,
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    await wrapper.get("button[aria-expanded='false']").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("This version is not in the distribution.");
    expect(wrapper.text()).not.toContain("/home/secret");
    const retry = wrapper.findAll("button").find((button) => button.text() === "Retry");
    expect(retry).toBeTruthy();
    await retry?.trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("This version declares no operations.");
    wrapper.unmount();
  });

  it("opens a namespace-only package by reusing the namespace as the name segment", async () => {
    const degenerateGroups = {
      format: "ghrah.ability-control.package-groups.v1",
      scope: "distribution",
      installed_available: true,
      project_id: null,
      agent_id: null,
      groups: [
        {
          namespace: "research",
          name: null,
          full_name: "research",
          title: "research",
          versions: [
            {
              version: "0.1.0",
              origin: "installed",
              lock_state: "ok",
              problem: null,
              shadowed_builtin: false,
              member_count: 1,
              project: null,
              agent: null,
            },
          ],
        },
      ],
    };
    const profile = AbilityPackageVersionProfileViewSchema.parse({
      namespace: "research",
      name: "research",
      full_name: "research.research",
      version: "0.1.0",
      origin: "installed",
      definition_origin: "source",
      lock_state: "ok",
      title: "research",
    });
    const packageVersion = vi.fn(async () => profile);
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => status("builtin", true, true),
          packageGroups: async () => degenerateGroups,
          packageVersion,
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("research");
    await wrapper.get("button[aria-expanded='false']").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledWith("research", "research", "0.1.0");
    wrapper.unmount();
  });

  it("explains a legacy source without offering install", async () => {
    setAbilityControlClientFactory(
      () => ({ status: async () => status("legacy", false) }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    expect(wrapper.text()).toContain("Legacy ability source is outside the new grant controls.");
    expect(wrapper.text()).toContain(
      "Package management stays closed while the legacy source is in use.",
    );
    expect(wrapper.find("textarea").exists()).toBe(false);
    wrapper.unmount();
  });
});
