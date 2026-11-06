// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dialog from "./new-agent-manifest-dialog.vue";

const api = vi.hoisted(() => ({
  putAgent: vi.fn(),
  listManifestAgents: vi.fn(),
  validateManifest: vi.fn(),
}));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => api }));
vi.mock("vue-i18n", () => ({ useI18n: () => ({ t: (key: string) => key }) }));
const source = `manifest: agent
version: '2'
metadata:
  namespace: demo
  name: helper
  title: 'true'
model:
  endpoint_name: local
  top_p: 0.8
system_prompt: Help me
abilities:
  - type: read_file
    permissions:
      require_hitl: true
context:
  window:
    max_tokens: 8000
hooks:
  pre_run: []
`;
async function openYaml() {
  const wrapper = mount(Dialog);
  await wrapper.findAll(".manifest-modes button")[1].trigger("click");
  return wrapper;
}
describe("New Agent form and YAML modes", () => {
  it("returns to an unfinished form without contacting the backend", async () => {
    const wrapper = await openYaml();
    await wrapper.findAll(".manifest-modes button")[0].trigger("click");
    expect(wrapper.find("#new-agent-yaml").exists()).toBe(false);
    expect(wrapper.get<HTMLInputElement>('input[placeholder="e.g. designer"]').element.value).toBe(
      "",
    );
    expect(api.validateManifest).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  beforeEach(() => {
    vi.clearAllMocks();
    api.validateManifest.mockResolvedValue({ success: true, data: { valid: true, errors: [] } });
    api.putAgent.mockResolvedValue({ success: true });
    api.listManifestAgents.mockResolvedValue({ success: true });
  });
  it("keeps form and YAML drafts independent across switches", async () => {
    const wrapper = mount(Dialog);
    await wrapper.get('input[placeholder="e.g. designer"]').setValue("form_agent");
    await wrapper.findAll(".manifest-modes button")[1].trigger("click");
    expect(wrapper.get<HTMLTextAreaElement>("#new-agent-yaml").element.value).toBe("");
    await wrapper.get("#new-agent-yaml").setValue(source);
    await wrapper.findAll(".manifest-modes button")[0].trigger("click");
    expect(wrapper.get<HTMLInputElement>('input[placeholder="e.g. designer"]').element.value).toBe(
      "form_agent",
    );
    await wrapper.get('input[placeholder="e.g. Designer"]').setValue("Changed");
    await wrapper.findAll(".manifest-modes button")[1].trigger("click");
    expect(wrapper.get<HTMLTextAreaElement>("#new-agent-yaml").element.value).toBe(source);
    expect(api.validateManifest).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it("validates without writing then creates without overwriting", async () => {
    const wrapper = await openYaml();
    await wrapper.get("#new-agent-yaml").setValue(source);
    const validate = wrapper
      .findAll("button")
      .find((button) => button.text() === "config.agent.validate")!;
    await validate.trigger("click");
    await flushPromises();
    expect(api.putAgent).not.toHaveBeenCalled();
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(api.putAgent).toHaveBeenCalledWith("demo.helper", source, false);
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(api.listManifestAgents).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it("keeps invalid YAML on switching and keeps draft after a duplicate-name error", async () => {
    const wrapper = await openYaml();
    await wrapper.get("#new-agent-yaml").setValue("metadata: [");
    await wrapper.findAll(".manifest-modes button")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find("#new-agent-yaml").exists()).toBe(false);
    await wrapper.findAll(".manifest-modes button")[1].trigger("click");
    expect(wrapper.get<HTMLTextAreaElement>("#new-agent-yaml").element.value).toBe("metadata: [");
    api.putAgent.mockResolvedValue({ success: false, error: "Already exists" });
    await wrapper.get("#new-agent-yaml").setValue(source);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Already exists");
    expect(wrapper.get<HTMLTextAreaElement>("#new-agent-yaml").element.value).toBe(source);
    expect(wrapper.emitted("close")).toBeUndefined();
    wrapper.unmount();
  });
});
