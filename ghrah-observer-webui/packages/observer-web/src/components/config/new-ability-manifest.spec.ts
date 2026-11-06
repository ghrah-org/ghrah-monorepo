// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import NewAbilityManifest from "./new-ability-manifest.vue";

const api = vi.hoisted(() => ({ validateManifest: vi.fn(), putAbility: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => api }));
vi.mock("vue-i18n", () => ({ useI18n: () => ({ t: (key: string) => key }) }));
const source = `manifest: ability
version: '1'
metadata:
  namespace: 'my_project'
  name: "announce"
  description: Broadcast an announcement
  permissions: {}
implementation:
  type: builtin
  handler: broadcast_message
`;

describe("New ability manifest", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.validateManifest.mockResolvedValue({ success: true, data: { valid: true, errors: [] } });
    api.putAbility.mockResolvedValue({ success: true });
  });

  it("validates without saving and creates using the YAML identity without overwrite", async () => {
    const wrapper = mount(NewAbilityManifest);
    await wrapper.get("textarea").setValue(source);
    await wrapper.findAll("button")[0].trigger("click");
    await flushPromises();
    expect(api.validateManifest).toHaveBeenCalledWith(source, "ability");
    expect(api.putAbility).not.toHaveBeenCalled();
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(api.putAbility).toHaveBeenCalledWith("my_project.announce", source, false);
    expect(wrapper.emitted("created")).toEqual([["my_project.announce"]]);
    wrapper.unmount();
  });

  it("preserves the draft and shows a duplicate-name error", async () => {
    api.putAbility.mockResolvedValue({ success: false, error: "Manifest already exists" });
    const wrapper = mount(NewAbilityManifest);
    await wrapper.get("textarea").setValue(source);
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("already exists");
    expect(wrapper.get("textarea").element.value).toBe(source);
    expect(wrapper.emitted("created")).toBeUndefined();
    wrapper.unmount();
  });

  it("blocks invalid YAML and backend validation errors", async () => {
    const wrapper = mount(NewAbilityManifest);
    await wrapper.get("textarea").setValue("metadata: [");
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    expect(api.validateManifest).not.toHaveBeenCalled();
    await wrapper.get("textarea").setValue(source);
    api.validateManifest.mockResolvedValue({
      success: true,
      data: { valid: false, errors: ["Invalid metadata"] },
    });
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Invalid metadata");
    expect(api.putAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("rejects other manifest kinds and an empty builtin handler", async () => {
    const wrapper = mount(NewAbilityManifest);
    await wrapper.get("textarea").setValue(source.replace("manifest: ability", "manifest: agent"));
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("config.ability.abilityRequired");
    await wrapper
      .get("textarea")
      .setValue(source.replace("handler: broadcast_message", "handler: ''"));
    await wrapper.findAll("button")[1].trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("config.ability.handlerRequired");
    expect(api.putAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("cancels without writing", async () => {
    const wrapper = mount(NewAbilityManifest);
    await wrapper.findAll("button")[2].trigger("click");
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    expect(api.putAbility).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
