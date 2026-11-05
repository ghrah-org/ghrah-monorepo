// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";

const autoConnect = vi.fn();
const disconnect = vi.fn();
const observerError = ref<string | null>(null);
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ error: observerError, autoConnect, disconnect }),
}));
vi.mock("@/pages/dashboard.vue", () => ({
  default: {
    emits: ["openSettings"],
    data: () => ({ draft: "" }),
    template:
      '<section data-dashboard><input class="dashboard-draft" v-model="draft"><button class="open-settings" @click="$emit(\'openSettings\', \'agents\')">open</button></section>',
  },
}));
vi.mock("@/pages/instance-settings.vue", () => ({
  default: {
    props: ["initialSection"],
    emits: ["close"],
    template:
      '<section data-settings><span>{{ initialSection }}</span><button class="close-settings" @click="$emit(\'close\')">close</button></section>',
  },
}));

import App from "./app.vue";

describe("App shell", () => {
  beforeEach(() => {
    autoConnect.mockReset();
    disconnect.mockReset();
    observerError.value = null;
  });

  it("keeps Dashboard mounted while full-screen settings opens and closes", async () => {
    const wrapper = mount(App, { attachTo: document.body });
    expect(autoConnect).toHaveBeenCalledOnce();
    expect(wrapper.find("header").exists()).toBe(false);
    await wrapper.get(".dashboard-draft").setValue("keep me");
    const opener = wrapper.get<HTMLButtonElement>(".open-settings");
    opener.element.focus();
    await opener.trigger("click");
    expect(wrapper.find("[data-settings]").text()).toContain("agents");
    expect(wrapper.find("[data-dashboard]").exists()).toBe(true);

    await wrapper.get(".close-settings").trigger("click");
    expect(wrapper.find("[data-settings]").exists()).toBe(false);
    expect(wrapper.get<HTMLInputElement>(".dashboard-draft").element.value).toBe("keep me");
    expect(document.activeElement).toBe(opener.element);
    wrapper.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("allows connection errors to be dismissed and expires them automatically", async () => {
    vi.useFakeTimers();
    const wrapper = mount(App);
    observerError.value = "temporary failure";
    await nextTick();
    expect(wrapper.get(".app-error-toast").text()).toContain("temporary failure");
    await wrapper.get(".app-error-dismiss").trigger("click");
    expect(wrapper.find(".app-error-toast").exists()).toBe(false);

    observerError.value = null;
    await nextTick();
    observerError.value = "another failure";
    await nextTick();
    vi.advanceTimersByTime(8000);
    await nextTick();
    expect(wrapper.find(".app-error-toast").exists()).toBe(false);
    wrapper.unmount();
    vi.useRealTimers();
  });
});
