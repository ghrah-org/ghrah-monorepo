// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { HttpError, setAbilityControlClientFactory } from "@ghrah/observer-core";
import { AbilityApiStatusSchema, AbilityPackageVersionProfileViewSchema } from "@ghrah/protocol";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AbilityConfigPage from "./ability-config-page.vue";

function status(
  source: "builtin" | "legacy",
  tree: boolean,
  packagesRead = false,
  install = false,
) {
  return AbilityApiStatusSchema.parse({
    ability_source: source,
    legacy_deprecated: source === "legacy",
    features: {
      tree,
      grants_write: tree,
      versions_read: packagesRead,
      versions_write: false,
      packages_read: packagesRead,
      packages_install: install,
      surface_write: false,
    },
  });
}

function groupVersion(version = "1.0.0", origin: "builtin" | "installed" = "builtin") {
  return {
    version,
    origin,
    lock_state: origin === "builtin" ? "builtin" : "ok",
    problem: null,
    shadowed_builtin: false,
    member_count: 2,
    project: null,
    agent: null,
  };
}

function groups(version = "1.0.0", origin: "builtin" | "installed" = "builtin") {
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
        versions: [groupVersion(version, origin)],
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

function buttonByText(wrapper: VueWrapper, text: string) {
  return wrapper.findAll("button").find((button) => button.text().includes(text));
}

describe("AbilityConfigPage", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => setAbilityControlClientFactory(null));

  it("keeps the catalog closed when the server hides packages", async () => {
    const read = vi.fn(async () => status("builtin", true));
    setAbilityControlClientFactory(() => ({ status: read }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Built-in ability control is available.");
    expect(wrapper.text()).toContain("The package catalog is not available from this server.");
    expect(wrapper.findAll(".ability-package").length).toBe(0);
    wrapper.unmount();
  });

  it("lists package groups with a disabled install action", async () => {
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => groups());
    setAbilityControlClientFactory(() => ({ status: read, packageGroups }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Reader");
    expect(wrapper.text()).toContain("sample.reader");
    expect(wrapper.text()).toContain("2 members");
    expect(wrapper.findAll(".ability-package").length).toBe(1);

    const install = buttonByText(wrapper, "Install package");
    expect(install).toBeTruthy();
    expect(install?.attributes("disabled")).toBeDefined();
    expect(install?.attributes("title")).toBe("Installing a package is not available yet.");
    wrapper.unmount();
  });

  it("opens the import dialog when the server allows installs", async () => {
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => status("builtin", true, true, true),
          packageGroups: async () => groups(),
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    const install = buttonByText(wrapper, "Install package");
    expect(install?.attributes("disabled")).toBeUndefined();
    await install?.trigger("click");
    expect(wrapper.find("[role='dialog']").exists()).toBe(true);
    wrapper.unmount();
  });

  it("groups by origin by default and collapses a section", async () => {
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => groups());
    setAbilityControlClientFactory(() => ({ status: read, packageGroups }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    const sectionHead = wrapper.get(".ability-section-head");
    expect(sectionHead.attributes("aria-expanded")).toBe("true");
    expect(sectionHead.text()).toContain("Built in");

    await sectionHead.trigger("click");
    expect(wrapper.find(".ability-section-head").attributes("aria-expanded")).toBe("false");
    expect(wrapper.findAll(".ability-package").length).toBe(0);

    await wrapper.get(".ability-section-head").trigger("click");
    expect(wrapper.findAll(".ability-package").length).toBe(1);
    wrapper.unmount();
  });

  it("filters by search and clears filters from the empty state", async () => {
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => groups());
    setAbilityControlClientFactory(() => ({ status: read, packageGroups }) as never);
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    const search = wrapper.get("input[type='search']");
    await search.setValue("nothing-matches");
    expect(wrapper.findAll(".ability-package").length).toBe(0);
    expect(wrapper.text()).toContain("No package matches the current filter.");

    await buttonByText(wrapper, "Clear filters")?.trigger("click");
    expect(wrapper.findAll(".ability-package").length).toBe(1);
    wrapper.unmount();
  });

  it("loads an expanded version once and reloads it when the catalog is refreshed", async () => {
    const profile = versionProfile();
    const read = vi.fn(async () => status("builtin", true, true));
    const packageGroups = vi.fn(async () => groups());
    const packageVersion = vi.fn(async () => profile);
    setAbilityControlClientFactory(
      () => ({ status: read, packageGroups, packageVersion }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    await wrapper.get(".ability-package-head").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);
    expect(packageVersion).toHaveBeenCalledWith("sample", "reader", "1.0.0");
    expect(wrapper.text()).toContain("This version declares no operations.");

    await wrapper.get(".ability-package-head").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);

    await wrapper.get(".ability-package-head").trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(1);

    await buttonByText(wrapper, "Refresh status")?.trigger("click");
    await flushPromises();
    expect(packageVersion).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("keeps a failed version's copy fixed and lets the user retry", async () => {
    const packageVersion = vi.fn(async () => {
      throw new HttpError({ code: "not_found", status: 404, message: "missing /home/secret" });
    });
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => status("builtin", true, true),
          packageGroups: async () => groups(),
          packageVersion,
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    await wrapper.get(".ability-package-head").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("This version is not in the distribution.");
    expect(wrapper.text()).not.toContain("/home/secret");

    const attempted = packageVersion.mock.calls.length;
    await buttonByText(wrapper, "Retry")?.trigger("click");
    await flushPromises();
    expect(packageVersion.mock.calls.length).toBeGreaterThan(attempted);
    expect(wrapper.text()).toContain("This version is not in the distribution.");
    wrapper.unmount();
  });

  it("shows the tool and skill split once a profile has been read", async () => {
    const profile = AbilityPackageVersionProfileViewSchema.parse({
      ...versionProfile(),
      operations: [
        {
          key: "read",
          title: "Read",
          description: "Read one file",
          input_schema: { type: "object" },
          output_schema: {},
          effects: [],
          tags: [],
          resources: {},
          permissions: {},
          expose: false,
          execution: {
            mode: "request",
            timeout_ms: 1000,
            cancellation: "cooperative",
            on_revoke: "drain",
            retry: "never",
          },
        },
      ],
      skills: [
        {
          key: "guide",
          title: "Guide",
          description: "How to use the package",
          tags: [],
          body: { path: "skills/guide.md", media_type: "text/markdown", size: 1, digest: "x" },
          assets: [],
          references: [],
          scripts: [],
        },
      ],
    });
    setAbilityControlClientFactory(
      () =>
        ({
          status: async () => status("builtin", true, true),
          packageGroups: async () => groups(),
          packageVersion: async () => profile,
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    expect(wrapper.get(".ability-package-meta").text()).toContain("1 tools · 1 skills");
    wrapper.unmount();
  });

  it("opens a namespace-only package by reusing the namespace as the name segment", async () => {
    const degenerate = {
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
          versions: [groupVersion("0.1.0", "installed")],
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
          packageGroups: async () => degenerate,
          packageVersion,
        }) as never,
    );
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();

    await wrapper.get(".ability-package-head").trigger("click");
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
    expect(buttonByText(wrapper, "Install package")?.attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });
});
