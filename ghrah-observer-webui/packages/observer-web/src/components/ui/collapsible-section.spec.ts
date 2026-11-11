// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h } from "vue";
import CollapsibleSection from "./collapsible-section.vue";

const PERSIST_KEY = "assembly:project-1:agent-1:grants";
const STORAGE_KEY = `ghrah-collapse:${PERSIST_KEY}`;

interface SectionProps {
  title?: string;
  headingLevel?: 2 | 3 | 4;
  defaultOpen?: boolean;
  persistKey?: string;
  disabled?: boolean;
}

const wrappers: ReturnType<typeof mount>[] = [];

function track<T extends ReturnType<typeof mount>>(wrapper: T): T {
  wrappers.push(wrapper);
  return wrapper;
}

function mountSection(
  props: SectionProps = {},
  slots: Record<string, string | (() => ReturnType<typeof h>)> = {},
) {
  return track(
    mount(CollapsibleSection, {
      attachTo: document.body,
      props: { title: "Grants", ...props },
      slots: { default: '<p class="body">Rows</p>', ...slots },
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  while (wrappers.length > 0) wrappers.pop()?.unmount();
  vi.restoreAllMocks();
});

describe("CollapsibleSection", () => {
  it("renders a section whose heading button controls collapsed content", () => {
    const wrapper = mountSection();
    const section = wrapper.get("section.collapsible-section");
    const heading = section.get("h3.collapsible-heading");
    const button = heading.get("button.collapsible-trigger");
    const content = wrapper.get(".collapsible-content");

    expect(button.attributes("type")).toBe("button");
    expect(button.attributes("aria-expanded")).toBe("false");
    expect(button.attributes("aria-controls")).toBe(content.attributes("id"));
    expect(content.attributes("id")).toBeTruthy();
    expect(content.isVisible()).toBe(false);
    expect(wrapper.get(".collapsible-title").text()).toBe("Grants");
    expect(content.text()).toBe("Rows");
    expect(wrapper.emitted("toggle")).toBeUndefined();
  });

  it("honours the requested heading level", () => {
    expect(mountSection({ headingLevel: 2 }).find("h2.collapsible-heading").exists()).toBe(true);
    expect(mountSection({ headingLevel: 4 }).find("h4.collapsible-heading").exists()).toBe(true);
    expect(mountSection().find("h3.collapsible-heading").exists()).toBe(true);
  });

  it("starts expanded when defaultOpen is set", () => {
    const wrapper = mountSection({ defaultOpen: true });
    expect(wrapper.get("button.collapsible-trigger").attributes("aria-expanded")).toBe("true");
    expect(wrapper.get(".collapsible-content").isVisible()).toBe(true);
  });

  it("gives each instance a unique content id", () => {
    const Host = defineComponent({
      components: { CollapsibleSection },
      template: `<div>
        <CollapsibleSection title="Grants" />
        <CollapsibleSection title="Tools" />
      </div>`,
    });
    const wrapper = track(mount(Host, { attachTo: document.body }));
    const [first, second] = wrapper.findAll("button.collapsible-trigger");
    expect(first.attributes("aria-controls")).not.toBe(second.attributes("aria-controls"));
  });

  it("toggles on click, emits the new state, and keeps focus on the trigger", async () => {
    const wrapper = mountSection();
    const button = wrapper.get<HTMLButtonElement>("button.collapsible-trigger");

    expect(document.activeElement).not.toBe(button.element);
    button.element.focus();
    await button.trigger("click");
    expect(document.activeElement).toBe(button.element);
    expect(button.attributes("aria-expanded")).toBe("true");
    expect(wrapper.get(".collapsible-content").isVisible()).toBe(true);

    await button.trigger("click");
    expect(button.attributes("aria-expanded")).toBe("false");
    expect(wrapper.get(".collapsible-content").isVisible()).toBe(false);
    expect(wrapper.emitted("toggle")).toEqual([[true], [false]]);
  });

  it("persists the open state under the ghrah-collapse prefix and restores it", async () => {
    const first = mountSection({ persistKey: PERSIST_KEY });
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    await first.get("button").trigger("click");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1");
    first.unmount();
    wrappers.splice(wrappers.indexOf(first), 1);

    const restored = mountSection({ persistKey: PERSIST_KEY });
    expect(restored.get("button").attributes("aria-expanded")).toBe("true");
    await restored.get("button").trigger("click");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("0");
    restored.unmount();
    wrappers.splice(wrappers.indexOf(restored), 1);

    const closed = mountSection({ persistKey: PERSIST_KEY });
    expect(closed.get("button").attributes("aria-expanded")).toBe("false");
  });

  it("leaves storage untouched without a persist key", async () => {
    const wrapper = mountSection();
    await wrapper.get("button").trigger("click");
    expect(localStorage.length).toBe(0);
  });

  it("degrades silently when localStorage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });

    const wrapper = mountSection({ persistKey: PERSIST_KEY });
    expect(wrapper.get("button").attributes("aria-expanded")).toBe("false");
  });

  it("does not toggle or emit when disabled", async () => {
    const wrapper = mountSection({ disabled: true });
    const button = wrapper.get("button.collapsible-trigger");
    expect(button.attributes("disabled")).toBeDefined();

    await button.trigger("click");
    expect(button.attributes("aria-expanded")).toBe("false");
    expect(wrapper.get(".collapsible-content").isVisible()).toBe(false);
    expect(wrapper.emitted("toggle")).toBeUndefined();
  });

  it("renders the actions slot in the heading row, outside the toggle button", () => {
    const wrapper = mountSection(
      {},
      { actions: () => h("button", { class: "refresh" }, "Refresh") },
    );
    const heading = wrapper.get("h3.collapsible-heading");
    expect(heading.get(".collapsible-actions").find("button.refresh").exists()).toBe(true);
    expect(wrapper.get("button.collapsible-trigger").find(".refresh").exists()).toBe(false);
  });

  it("lets heading actions work without toggling the section", async () => {
    const wrapper = mountSection(
      {},
      { actions: () => h("button", { class: "refresh" }, "Refresh") },
    );
    await wrapper.get(".collapsible-actions .refresh").trigger("click");
    expect(wrapper.emitted("toggle")).toBeUndefined();
    expect(wrapper.get("button.collapsible-trigger").attributes("aria-expanded")).toBe("false");
  });
});
