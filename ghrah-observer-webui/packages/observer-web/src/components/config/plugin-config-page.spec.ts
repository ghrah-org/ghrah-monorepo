// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { usePluginsStore as useStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import PluginConfigPage from "./plugin-config-page.vue";

const manifestError = ref<string | null>(null);
const loadResults = ref<Array<{ pluginId: string; ok: boolean; error?: string }>>([]);
const resolveBadge = vi.fn(() => null);

vi.mock("@/composables/usePlugins", () => ({
  usePlugins: () => ({
    loadManifest: vi.fn(),
    tsHalfProvider: () => [],
    resolveBadge,
    manifestError,
    loadResults,
    negotiation: ref(null),
  }),
}));

describe("PluginConfigPage", () => {
  beforeEach(() => {
    manifestError.value = null;
    loadResults.value = [];
  });

  function mountPage() {
    const pinia = createPinia();
    setActivePinia(pinia);
    return { wrapper: mount(PluginConfigPage, { global: { plugins: [pinia] } }), pinia };
  }

  it("renders three negotiation states: matched, version conflicts with both versions, crashed", async () => {
    const { wrapper } = mountPage();
    const plugins = useStore();
    plugins.setNegotiationResult({
      matched: [{ plugin_id: "p1", version: "0.1.0", provides: [], instances: [] }],
      python_only: [],
      ts_only: [],
      version_conflicts: [{ plugin_id: "stale", python_version: "0.2.0", ts_version: "9.9.9" }],
      missing_capabilities: [],
      instances: {},
    });
    plugins.onCrashed({
      plugin_id: "p1",
      command: "task_verify",
      error: "boom",
      instance_id: null,
    });
    await nextTick();

    expect(wrapper.text()).toContain("p1");
    expect(wrapper.text()).toContain("0.2.0");
    expect(wrapper.text()).toContain("9.9.9");
    expect(wrapper.text()).toContain("task_verify");
    expect(wrapper.text()).toContain("boom");
  });

  it("shows pending placeholder before negotiation result", () => {
    const { wrapper } = mountPage();
    expect(wrapper.text()).toContain("Plugin negotiation has not run yet.");
  });

  it("shows empty placeholder when negotiation has no entries", async () => {
    const { wrapper } = mountPage();
    const plugins = useStore();
    plugins.setNegotiationResult({
      matched: [],
      python_only: [],
      ts_only: [],
      version_conflicts: [],
      missing_capabilities: [],
      instances: {},
    });
    await nextTick();
    expect(wrapper.text()).toContain("No plugins negotiated.");
  });

  it("shows manifest error when assembly output failed to load", () => {
    manifestError.value = "HTTP 404";
    const { wrapper } = mountPage();
    expect(wrapper.text()).toContain("Plugin manifest failed to load");
  });
});
