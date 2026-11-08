// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Editor from "./edit-agent-manifest-dialog.vue";

const api = vi.hoisted(() => ({ getAgent: vi.fn(), putAgent: vi.fn(), validateManifest: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => api }));
vi.mock("vue-i18n", () => ({ useI18n: () => ({ t: (key: string) => key }) }));
const source =
  "manifest: agent\nversion: '2'\nmetadata:\n  namespace: demo\n  name: agent\nsystem_prompt: original\n";
const updated = source.replace("original", "updated");
async function open() {
  const wrapper = mount(Editor, { props: { fullName: "demo.agent" } });
  await flushPromises();
  return wrapper;
}
describe("Agent YAML editing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgent.mockResolvedValue({ success: true, data: { source } });
    api.validateManifest.mockResolvedValue({ success: true, data: { valid: true, errors: [] } });
    api.putAgent.mockResolvedValue({ success: true, data: { source: updated } });
  });
  it("loads the original source, validates without writing, and saves with overwrite", async () => {
    const wrapper = await open();
    expect(wrapper.get("textarea").element.value).toBe(source);
    await wrapper.get("textarea").setValue(updated);
    await wrapper.findAll(".base-modal-footer button")[1].trigger("click");
    await flushPromises();
    expect(api.validateManifest).toHaveBeenCalledWith(updated, "agent");
    expect(api.putAgent).not.toHaveBeenCalled();
    await wrapper.findAll(".base-modal-footer button")[2].trigger("click");
    await flushPromises();
    expect(api.putAgent).toHaveBeenCalledWith("demo.agent", updated, true);
    expect(wrapper.emitted("saved")).toHaveLength(1);
    expect(wrapper.text()).toContain("config.agent.saved");
    wrapper.unmount();
  });
  it("rejects renaming and backend validation errors without saving", async () => {
    const wrapper = await open();
    await wrapper.get("textarea").setValue(source.replace("name: agent", "name: other"));
    await wrapper.findAll(".base-modal-footer button")[2].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("config.agent.identityUnchanged");
    await wrapper.get("textarea").setValue(updated);
    api.validateManifest.mockResolvedValue({
      success: true,
      data: { valid: false, errors: ["Missing model"] },
    });
    await wrapper.findAll(".base-modal-footer button")[2].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Missing model");
    expect(api.putAgent).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it("preserves draft after save failure", async () => {
    api.putAgent.mockResolvedValue({ success: false, error: "Write failed" });
    const wrapper = await open();
    await wrapper.get("textarea").setValue(updated);
    await wrapper.findAll(".base-modal-footer button")[2].trigger("click");
    await flushPromises();
    expect(wrapper.get("textarea").element.value).toBe(updated);
    expect(wrapper.get('[role="alert"]').text()).toBe("Write failed");
    expect(wrapper.emitted("saved")).toBeUndefined();
    wrapper.unmount();
  });
  it("confirms discarding changes without saving", async () => {
    const wrapper = await open();
    await wrapper.get("textarea").setValue(updated);
    await wrapper.findAll(".base-modal-footer button")[0].trigger("click");
    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(2);
    const dialogs = wrapper.findAll('[role="dialog"]');
    await dialogs[dialogs.length - 1].get(".ui-button-danger").trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(api.putAgent).not.toHaveBeenCalled();
    wrapper.unmount();
  });
  it("allows retry after a failed source request", async () => {
    api.getAgent.mockResolvedValueOnce(null);
    const wrapper = await open();
    expect(wrapper.get('[role="alert"]').text()).toBe("config.agent.notConnected");
    expect(wrapper.find("textarea").exists()).toBe(false);
    await wrapper.get(".agent-yaml-editor button").trigger("click");
    await flushPromises();
    expect(wrapper.get("textarea").element.value).toBe(source);
    wrapper.unmount();
  });
});
