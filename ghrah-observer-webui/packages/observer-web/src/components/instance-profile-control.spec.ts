// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, reactive } from "vue";

const connection = reactive({ state: "disconnected", serverUrl: "ws://subject.test/ws" });
const autoConnect = vi.fn();
const disconnect = vi.fn();
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ connection, autoConnect, disconnect }),
}));

import InstanceProfileControl from "./instance-profile-control.vue";

enableAutoUnmount(afterEach);

describe("InstanceProfileControl", () => {
  beforeEach(() => {
    localStorage.clear();
    connection.state = "disconnected";
    autoConnect.mockReset();
    disconnect.mockReset();
  });

  it("persists an editable display name", async () => {
    const wrapper = mount(InstanceProfileControl);
    expect(wrapper.text()).toContain("User");
    expect(wrapper.text()).toContain("ws://subject.test/ws");
    expect(wrapper.get(".instance-avatar").text()).toContain("九");
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
  });

  it("opens the instance quick menu and emits the chosen section", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    await wrapper.get(".instance-avatar-button").trigger("click");
    await nextTick();
    const menu = wrapper.get('[role="menu"]');
    expect(menu.text()).toContain("User settings");
    expect(menu.text()).toContain("Agent templates");
    expect(menu.text()).toContain("Abilities");
    expect(menu.text()).toContain("Models & MCP");
    expect(menu.text()).toContain("System settings");
    expect(document.activeElement).toBe(menu.get('[role="menuitem"]').element);
    const abilities = menu
      .findAll('[role="menuitem"]')
      .find((item) => item.text().includes("Abilities"));
    await abilities?.trigger("click");
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    expect(wrapper.emitted("openSettings")).toEqual([["abilities"]]);
  });

  it("opens the same menu from the gear trigger", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    await wrapper.get(".instance-menu-trigger").trigger("click");
    await nextTick();
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
  });

  it("closes the menu with Escape and restores focus to its trigger", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    const trigger = wrapper.get<HTMLButtonElement>(".instance-menu-trigger");
    trigger.element.focus();
    await trigger.trigger("click");
    await nextTick();
    await wrapper.get('[role="menu"]').trigger("keydown", { key: "Escape" });
    await nextTick();
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    expect(document.activeElement).toBe(trigger.element);
  });

  it("moves focus between menu items with arrow keys", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    await wrapper.get(".instance-avatar-button").trigger("click");
    await nextTick();
    const items = wrapper.findAll('[role="menuitem"]');
    expect(document.activeElement).toBe(items[0]?.element);
    await wrapper.get('[role="menu"]').trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]?.element);
    await wrapper.get('[role="menu"]').trigger("keydown", { key: "End" });
    expect(document.activeElement).toBe(items.at(-1)?.element);
    await wrapper.get('[role="menu"]').trigger("keydown", { key: "Home" });
    expect(document.activeElement).toBe(items[0]?.element);
  });

  it("cycles focus through menu items with Tab and Shift+Tab", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    await wrapper.get(".instance-avatar-button").trigger("click");
    await nextTick();
    const menu = wrapper.get('[role="menu"]');
    const items = wrapper.findAll('[role="menuitem"]');
    await menu.trigger("keydown", { key: "Tab" });
    expect(document.activeElement).toBe(items[1]?.element);
    await menu.trigger("keydown", { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(items[0]?.element);
    await menu.trigger("keydown", { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(items.at(-1)?.element);
  });

  it("closes the menu on an outside pointerdown", async () => {
    const wrapper = mount(InstanceProfileControl, { attachTo: document.body });
    await wrapper.get(".instance-avatar-button").trigger("click");
    await nextTick();
    expect(wrapper.find('[role="menu"]').exists()).toBe(true);
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await nextTick();
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
  });
});
