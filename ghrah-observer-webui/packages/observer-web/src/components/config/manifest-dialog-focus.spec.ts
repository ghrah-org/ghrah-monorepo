// @vitest-environment happy-dom

import type { AgentManifestInfo } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

const putAgent = vi.fn();
const listManifestAgents = vi.fn();
const spawnAgent = vi.fn();
const observerError = ref<string | null>(null);
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ putAgent, listManifestAgents, spawnAgent, error: observerError }),
}));

import NewAgentManifestDialog from "./new-agent-manifest-dialog.vue";
import SpawnFromManifestDialog from "./spawn-from-manifest-dialog.vue";

const manifest = {
  full_name: "demo.helper",
  namespace: "demo",
  name: "helper",
  title: "Helper",
  description: "",
  tags: [],
  endpoint_name: "default",
  system_prompt: "Help",
  ability_refs: [],
  max_iterations: 10,
} as AgentManifestInfo;

describe("manifest dialog focus", () => {
  it("focuses the first new-manifest field and handles Escape", async () => {
    const wrapper = mount(NewAgentManifestDialog, { attachTo: document.body });
    await nextTick();
    expect(document.activeElement).toBe(wrapper.get("[data-autofocus]").element);
    await wrapper.get("[data-autofocus]").trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("guards discarding a dirty manifest with the shared confirm dialog", async () => {
    const wrapper = mount(NewAgentManifestDialog, { attachTo: document.body });
    await nextTick();
    const input = wrapper.get("[data-autofocus]");
    await input.setValue("demo");
    await input.trigger("keydown", { key: "Escape" });
    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(2);
    const discard = wrapper.findAll("button").find((button) => button.text() === "Discard changes");
    await discard!.trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("focuses the runtime name in the spawn dialog", async () => {
    const wrapper = mount(SpawnFromManifestDialog, {
      attachTo: document.body,
      props: { manifest },
    });
    await nextTick();
    expect(document.activeElement).toBe(wrapper.get("[data-autofocus]").element);
    wrapper.unmount();
  });
});
