// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import InstanceSettings from "./instance-settings.vue";

const wrappers: Array<ReturnType<typeof mount>> = [];

function mountSettings(
  initialSection: "general" | "agents" | "abilities" | "archived" = "general",
) {
  const wrapper = mount(InstanceSettings, {
    attachTo: document.body,
    props: { initialSection },
    global: {
      stubs: {
        GeneralConfigPage: { template: '<div data-page="general">general</div>' },
        AgentConfigPage: {
          template:
            '<div data-page="agents"><button class="make-dirty" @click="$emit(\'dirty-change\', true)">dirty</button></div>',
        },
        AbilityConfigPage: { template: '<div data-page="abilities">abilities</div>' },
        ArchivedResourcesPage: { template: '<div data-page="archived">archived</div>' },
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount();
});

describe("InstanceSettings", () => {
  it("uses local section state without routes", async () => {
    const wrapper = mountSettings();
    expect(wrapper.find('[data-page="general"]').exists()).toBe(true);
    const agents = wrapper
      .findAll(".sidebar-row")
      .find((button) => button.text().includes("Agent Config"));
    await agents!.trigger("click");
    expect(wrapper.find('[data-page="agents"]').exists()).toBe(true);
  });

  it("opens archived resources from the shared settings sidebar", async () => {
    const wrapper = mountSettings();
    const archived = wrapper
      .findAll(".sidebar-row")
      .find((button) => button.text().includes("Archived Resources"));
    await archived!.trigger("click");
    expect(wrapper.find('[data-page="archived"]').exists()).toBe(true);
  });

  it("closes from the visible button and Escape", async () => {
    const byButton = mountSettings();
    await byButton.get(".settings-close").trigger("click");
    expect(byButton.emitted("close")).toHaveLength(1);

    const byEscape = mountSettings();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await byEscape.vm.$nextTick();
    expect(byEscape.emitted("close")).toHaveLength(1);
  });

  it("guards closing and section changes when a child is dirty", async () => {
    const wrapper = mountSettings("agents");
    await wrapper.get(".make-dirty").trigger("click");
    const ability = wrapper
      .findAll(".sidebar-row")
      .find((button) => button.text().includes("Ability Config"));
    await ability!.trigger("click");
    expect(wrapper.find('[data-page="abilities"]').exists()).toBe(false);
    const discard = wrapper.findAll("button").find((button) => button.text() === "Discard changes");
    await discard!.trigger("click");
    expect(wrapper.find('[data-page="abilities"]').exists()).toBe(true);
  });
});
