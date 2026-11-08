// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { setAbilityControlClientFactory } from "@ghrah/observer-core";
import { AbilityApiStatusSchema } from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AbilityConfigPage from "./ability-config-page.vue";

function status(source: "builtin" | "legacy", tree: boolean) {
  return AbilityApiStatusSchema.parse({
    ability_source: source,
    legacy_deprecated: source === "legacy",
    features: {
      tree,
      grants_write: tree,
      versions_read: false,
      versions_write: false,
      packages_read: false,
      packages_install: false,
      surface_write: false,
    },
  });
}

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
