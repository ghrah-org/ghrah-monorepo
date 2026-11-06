// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AbilityConfigPage from "./ability-config-page.vue";

const api = vi.hoisted(() => ({
  listManifestAbilities: vi.fn(),
  getAbility: vi.fn(),
  putAbility: vi.fn(),
  deleteAbility: vi.fn(),
  validateManifest: vi.fn(),
  manifests: {
    abilityList: [{ full_name: "demo.read", title: "Read", permissions: {} }],
  },
}));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => api }));
vi.mock("vue-i18n", () => ({ useI18n: () => ({ t: (key: string) => key }) }));

const manifest = { metadata: { permissions: {} }, implementation: { type: "builtin" } };
async function openEditor() {
  const wrapper = mount(AbilityConfigPage);
  await flushPromises();
  await wrapper.get(".ability-card").trigger("click");
  await flushPromises();
  await wrapper.get(".ability-editor button").trigger("click");
  return wrapper;
}

describe("Ability YAML editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listManifestAbilities.mockResolvedValue({ success: true });
    api.getAbility.mockResolvedValue({ success: true, data: { manifest, source: "original" } });
    api.validateManifest.mockResolvedValue({ success: true, data: { valid: true, errors: [] } });
    api.putAbility.mockResolvedValue({ success: true, data: { manifest, source: "updated" } });
    api.deleteAbility.mockResolvedValue({ success: true });
  });

  it("loads source and discards edits on cancel without saving or closing the card", async () => {
    const wrapper = await openEditor();
    expect(wrapper.get("textarea").element.value).toBe("original");
    await wrapper.get("textarea").setValue("updated");
    await wrapper.get("textarea").trigger("click");
    expect(wrapper.find(".ability-detail").exists()).toBe(true);
    await wrapper.findAll(".ability-editor button")[2].trigger("click");
    await wrapper.get(".ability-editor button").trigger("click");
    expect(wrapper.get("textarea").element.value).toBe("original");
    expect(api.putAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("shows validation errors and does not save invalid input", async () => {
    const wrapper = await openEditor();
    api.validateManifest.mockResolvedValue({
      success: true,
      data: { valid: false, errors: ["Invalid YAML"] },
    });
    await wrapper.get("textarea").setValue("broken");
    await wrapper.findAll(".ability-editor button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Invalid YAML");
    expect(wrapper.get("textarea").element.value).toBe("broken");
    expect(api.putAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("validates without writing, then saves with overwrite and refreshes the list", async () => {
    const wrapper = await openEditor();
    await wrapper.get("textarea").setValue("updated");
    await wrapper.findAll(".ability-editor button")[0].trigger("click");
    await flushPromises();
    expect(api.validateManifest).toHaveBeenCalledWith("updated", "ability");
    expect(api.putAbility).not.toHaveBeenCalled();
    await wrapper.findAll(".ability-editor button")[1].trigger("click");
    await flushPromises();
    expect(api.putAbility).toHaveBeenCalledWith("demo.read", "updated", true);
    expect(api.listManifestAbilities).toHaveBeenCalledTimes(2);
    expect(wrapper.find("textarea").exists()).toBe(false);
    expect(wrapper.text()).toContain("config.ability.saved");
    wrapper.unmount();
  });

  it("keeps the draft when saving fails", async () => {
    const wrapper = await openEditor();
    api.putAbility.mockResolvedValue({ success: false, error: "Write failed" });
    await wrapper.get("textarea").setValue("updated");
    await wrapper.findAll(".ability-editor button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Write failed");
    expect(wrapper.get("textarea").element.value).toBe("updated");
    wrapper.unmount();
  });

  async function requestDelete() {
    const wrapper = mount(AbilityConfigPage);
    await flushPromises();
    await wrapper.get(".ability-card").trigger("click");
    await flushPromises();
    await wrapper.findAll(".ability-editor button")[1].trigger("click");
    return wrapper;
  }

  it("requires confirmation and allows cancelling without deleting", async () => {
    const wrapper = await requestDelete();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    expect(api.deleteAbility).not.toHaveBeenCalled();
    await wrapper.get('[role="dialog"] .btn-secondary').trigger("click");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    expect(wrapper.find(".ability-detail").exists()).toBe(true);
    expect(api.deleteAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("deletes the confirmed ability, closes details and refreshes the list", async () => {
    const wrapper = await requestDelete();
    await wrapper.get('[role="dialog"] .btn-primary').trigger("click");
    await flushPromises();
    expect(api.deleteAbility).toHaveBeenCalledExactlyOnceWith("demo.read");
    expect(api.listManifestAbilities).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".ability-detail").exists()).toBe(false);
    expect(wrapper.text()).toContain("config.ability.deleted");
    wrapper.unmount();
  });

  it("keeps details visible and prevents repeated deletion while the request is pending", async () => {
    let finish!: (result: { success: boolean; error: string }) => void;
    api.deleteAbility.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const wrapper = await requestDelete();
    await wrapper.get('[role="dialog"] .btn-primary').trigger("click");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    expect(wrapper.findAll(".ability-editor button")[1].attributes("disabled")).toBeDefined();
    finish({ success: false, error: "Delete failed" });
    await flushPromises();
    expect(wrapper.find(".ability-detail").exists()).toBe(true);
    expect(wrapper.get('[role="alert"]').text()).toBe("Delete failed");
    expect(api.listManifestAbilities).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
});
