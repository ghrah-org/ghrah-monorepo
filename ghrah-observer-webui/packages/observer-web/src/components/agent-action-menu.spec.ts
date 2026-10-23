// @vitest-environment happy-dom

import { useAgentsStore } from "@ghrah/observer-core";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentActionMenu from "./agent-action-menu.vue";

const agentCompactContext = vi.fn();
const terminateAgent = vi.fn();

vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    agentCompactContext,
    terminateAgent,
    error: { value: null },
  }),
}));

function mountMenu() {
  return mount(AgentActionMenu);
}

async function openMenu(wrapper: ReturnType<typeof mount>) {
  await wrapper.get("button").trigger("click");
}

describe("AgentActionMenu compact context", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    agentCompactContext.mockReset();
    agentCompactContext.mockResolvedValue({ success: true, data: { executed: true } });
    terminateAgent.mockReset();
    terminateAgent.mockResolvedValue({ success: true, data: {} });
    const agents = useAgentsStore();
    agents.selectAgent({ projectId: "p1", agentId: "a1", agentName: "coder" });
  });

  it("sends agent_compact_context with the selected agent target", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    const compactButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Compact Context"));
    expect(compactButton).toBeDefined();
    await compactButton!.trigger("click");
    expect(agentCompactContext).toHaveBeenCalledWith({
      projectId: "p1",
      agentId: "a1",
      agentName: "coder",
    });
  });

  it("shows server error when the command fails", async () => {
    agentCompactContext.mockResolvedValue({ success: false, error: "agent context not found" });
    const wrapper = mountMenu();
    await openMenu(wrapper);
    const compactButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Compact Context"));
    await compactButton!.trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("agent context not found");
  });

  it("shows fallback error when not connected", async () => {
    agentCompactContext.mockResolvedValue(null);
    const wrapper = mountMenu();
    await openMenu(wrapper);
    const compactButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Compact Context"));
    await compactButton!.trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Not connected to server");
  });
});

describe("AgentActionMenu menu chrome", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    agentCompactContext.mockReset();
    agentCompactContext.mockResolvedValue({ success: true, data: { executed: true } });
    terminateAgent.mockReset();
    terminateAgent.mockResolvedValue({ success: true, data: {} });
    const agents = useAgentsStore();
    agents.selectAgent({ projectId: "p1", agentId: "a1", agentName: "coder" });
  });

  it("offers ability management alongside lifecycle actions", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    const labels = wrapper.findAll(".agent-menu-item").map((b) => b.text());
    expect(labels).toEqual(["Compact Context", "Abilities", "Terminate Agent"]);
    expect(wrapper.find(".agent-menu-header").text()).toBe("coder");
  });

  it("closes on Escape", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(wrapper.find(".agent-menu").exists()).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".agent-menu").exists()).toBe(false);
  });

  it("closes on outside click", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await wrapper.find(".fixed.inset-0").trigger("click");
    expect(wrapper.find(".agent-menu").exists()).toBe(false);
  });
});

it("opens ability management for the selected stable agent identity", async () => {
  setActivePinia(createPinia());
  const target = { projectId: "p1", agentId: "a1", agentName: "coder" };
  useAgentsStore().selectAgent(target);
  const wrapper = mount(AgentActionMenu, { global: { stubs: { AgentAbilitiesDialog: true } } });
  await openMenu(wrapper);
  await wrapper
    .findAll(".agent-menu-item")
    .find((button) => button.text() === "Abilities")!
    .trigger("click");
  expect(wrapper.findComponent({ name: "AgentAbilitiesDialog" }).props("target")).toEqual(target);
  expect(wrapper.find(".agent-menu").exists()).toBe(false);
});
