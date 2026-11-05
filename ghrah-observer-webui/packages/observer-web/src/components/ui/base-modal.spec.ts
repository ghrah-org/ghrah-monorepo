// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { defineComponent, nextTick, ref } from "vue";
import BaseModal from "./base-modal.vue";

const Host = defineComponent({
  components: { BaseModal },
  setup: () => ({ open: ref(false) }),
  template: `
    <button class="opener" @click="open = true">open</button>
    <BaseModal v-if="open" title="Example" :show-close="false" @request-close="open = false">
      <button class="first" data-autofocus>first</button><button class="last">last</button>
    </BaseModal>`,
});

describe("BaseModal", () => {
  it("moves and traps focus, handles Escape, then restores focus", async () => {
    const wrapper = mount(Host, { attachTo: document.body });
    const opener = wrapper.get<HTMLButtonElement>(".opener");
    opener.element.focus();
    await opener.trigger("click");
    await nextTick();
    const first = wrapper.get<HTMLButtonElement>(".first");
    const last = wrapper.get<HTMLButtonElement>(".last");
    expect(document.activeElement).toBe(first.element);
    last.element.focus();
    await last.trigger("keydown", { key: "Tab" });
    expect(document.activeElement).toBe(first.element);
    await first.trigger("keydown", { key: "Escape" });
    await nextTick();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    expect(document.activeElement).toBe(opener.element);
    wrapper.unmount();
  });

  it("closes from a backdrop pointer press", async () => {
    const wrapper = mount(Host);
    await wrapper.get(".opener").trigger("click");
    await wrapper.get(".base-modal-backdrop").trigger("pointerdown");
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  });
});
