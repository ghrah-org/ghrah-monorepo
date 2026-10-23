// @vitest-environment happy-dom
import { useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import AgentAbilitiesDialog from "./agent-abilities-dialog.vue";

const api = vi.hoisted(() => ({ getAgentInfo: vi.fn(), registerAbility: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => ({ ...api, error: ref(null) }) }));
const target = { projectId: "p1", agentId: "a1", agentName: "worker" };
function createDialog() {
  return mount(AgentAbilitiesDialog, { props: { target }, global: { stubs: { Teleport: true } } });
}
beforeEach(() => {
  setActivePinia(createPinia());
  useConnectionStore().setConnected();
  useProjectsStore().setProjectsFromList([
    { project_id: "p1", name: "Project", status: "active", agents: [] } as never,
  ]);
  api.getAgentInfo.mockReset().mockResolvedValue({
    success: true,
    data: {
      abilities: ["conversation", "end_task"],
      available_ability_types: [
        "conversation",
        "end_task",
        "send_message",
        "bad",
        "custom_deployed_type",
      ],
    },
  });
  api.registerAbility
    .mockReset()
    .mockResolvedValue({ success: true, data: { registered: true, ability: "send_message" } });
});

describe("AgentAbilitiesDialog", () => {
  it("shows the selected runtime's actual abilities and stable identity", async () => {
    const wrapper = createDialog();
    await flushPromises();
    expect(api.getAgentInfo).toHaveBeenCalledWith(target);
    expect(wrapper.text()).toContain("a1");
    expect(wrapper.findAll("code").map((entry) => entry.text())).toEqual([
      "conversation",
      "end_task",
    ]);
    expect(wrapper.text()).toContain("current running instance only");
  });
  it("registers explicitly and refreshes from authority", async () => {
    const wrapper = createDialog();
    await flushPromises();
    await wrapper.find("select").setValue("send_message");
    api.getAgentInfo.mockResolvedValue({
      success: true,
      data: {
        abilities: ["conversation", "end_task", "send_message"],
        available_ability_types: [
          "conversation",
          "end_task",
          "send_message",
          "custom_deployed_type",
        ],
      },
    });
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(api.registerAbility).toHaveBeenCalledWith(target, {
      ability_type: "send_message",
      params: {},
    });
    expect(api.getAgentInfo).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll("code").map((entry) => entry.text())).toContain("send_message");
  });
  it.each(["[]", "null", "broken"])(
    "rejects invalid constructor parameters: %s",
    async (params) => {
      const wrapper = createDialog();
      await flushPromises();
      await wrapper.find("select").setValue("send_message");
      await wrapper.find("textarea").setValue(params);
      await wrapper.find("form").trigger("submit");
      expect(wrapper.find('[type="submit"]').attributes("disabled")).toBeDefined();
      expect(wrapper.text()).toContain("valid JSON object");
      expect(api.registerAbility).not.toHaveBeenCalled();
    },
  );
  it("shows backend errors and leaves the form editable", async () => {
    api.registerAbility.mockResolvedValue({ success: false, error: "Unknown ability type: bad" });
    const wrapper = createDialog();
    await flushPromises();
    await wrapper.find("select").setValue("bad");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("Unknown ability type: bad");
    expect(wrapper.find("select").attributes("disabled")).toBeUndefined();
    expect(api.getAgentInfo).toHaveBeenCalledTimes(1);
    expect(wrapper.findAll("code")).toHaveLength(2);
  });
  it("distinguishes stopped definitions and prevents implicitly reviving the agent", async () => {
    api.getAgentInfo.mockResolvedValue({
      success: true,
      data: { runtime_state: "stopped", abilities: [{ ref: "ghrah.cluster.send_message" }] },
    });
    const wrapper = createDialog();
    await flushPromises();
    expect(wrapper.text()).toContain("Configured ability definitions");
    expect(wrapper.text()).toContain("agent is stopped");
    await wrapper.find("select").setValue("send_message");
    await wrapper.find("form").trigger("submit");
    expect(api.registerAbility).not.toHaveBeenCalled();
  });
  it("disables registration if the authoritative refresh fails", async () => {
    const wrapper = createDialog();
    await flushPromises();
    api.getAgentInfo.mockResolvedValue({ success: false, error: "agent is offline" });
    await wrapper.find("select").setValue("send_message");
    await wrapper.find(".btn-secondary").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("agent is offline");
    expect(wrapper.findAll("code")).toHaveLength(0);
    expect(wrapper.find('[type="submit"]').attributes("disabled")).toBeDefined();
  });
});

it("offers every server-provided type including custom types without a frontend whitelist", async () => {
  const wrapper = createDialog();
  await flushPromises();
  const options = wrapper.findAll("option").map((option) => option.attributes("value"));
  expect(options).toContain("conversation");
  expect(options).toContain("custom_deployed_type");
  await wrapper.find("select").setValue("custom_deployed_type");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
  expect(api.registerAbility).toHaveBeenCalledWith(target, {
    ability_type: "custom_deployed_type",
    params: {},
  });
});

it("does not fall back to a hardcoded type list if discovery is unavailable", async () => {
  api.getAgentInfo.mockResolvedValue({ success: true, data: { abilities: ["conversation"] } });
  const wrapper = createDialog();
  await flushPromises();
  expect(wrapper.text()).toContain("Unable to load available ability types");
  expect(wrapper.find("select").attributes("disabled")).toBeDefined();
  expect(wrapper.find('[type="submit"]').attributes("disabled")).toBeDefined();
});
