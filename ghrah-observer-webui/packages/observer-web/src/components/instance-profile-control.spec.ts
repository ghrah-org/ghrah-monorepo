// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { reactive } from "vue";

const connection = reactive({ state: "disconnected", serverUrl: "ws://subject.test/ws" });
const autoConnect = vi.fn();
const disconnect = vi.fn();
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ connection, autoConnect, disconnect }),
}));

import InstanceProfileControl from "./instance-profile-control.vue";

describe("InstanceProfileControl", () => {
  beforeEach(() => {
    localStorage.clear();
    connection.state = "disconnected";
    autoConnect.mockReset();
    disconnect.mockReset();
  });

  it("persists an editable display name", async () => {
    const wrapper = mount(InstanceProfileControl);
    expect(wrapper.text()).toContain("Operator");
    expect(wrapper.text()).toContain("ws://subject.test/ws");
    await wrapper.get(".instance-display-name").trigger("click");
    const input = wrapper.get(".instance-name-input");
    await input.setValue("Yuki");
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.text()).toContain("Yuki");
    expect(localStorage.getItem("ghrah.instance-profile.display-name")).toBe("Yuki");
  });

  it("exposes accessible connection controls and animated pending state", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    const address = wrapper.get<HTMLButtonElement>(".instance-address");
    address.element.focus();
    await address.trigger("click");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    expect(document.activeElement).toBe(wrapper.get(".instance-connection-action").element);
    await wrapper.get(".instance-connection-action").trigger("keydown", { key: "Escape" });
    expect(document.activeElement).toBe(address.element);
    await address.trigger("click");
    await wrapper.get(".instance-connection-action").trigger("click");
    expect(autoConnect).toHaveBeenCalledOnce();
    connection.state = "reconnecting";
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".instance-status-dot").classes()).toContain("instance-status-pending");
    wrapper.unmount();
  });

  it("opens instance settings from the gear button", async () => {
    const wrapper = mount(InstanceProfileControl);
    await wrapper.get(".instance-settings-button").trigger("click");
    expect(wrapper.emitted("openSettings")).toHaveLength(1);
  });
});
