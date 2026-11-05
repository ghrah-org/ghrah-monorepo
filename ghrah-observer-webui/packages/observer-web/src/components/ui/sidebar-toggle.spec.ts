// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SidebarToggle from "./sidebar-toggle.vue";

describe("SidebarToggle", () => {
  it("renders a collapse button pointing at the sidebar edge", () => {
    const wrapper = mount(SidebarToggle, {
      props: {
        side: "left",
        collapsed: false,
        controls: "room-sidebar-panel",
        label: "Collapse rooms sidebar",
      },
    });
    const button = wrapper.get("button");
    expect(button.attributes("aria-expanded")).toBe("true");
    expect(button.attributes("aria-controls")).toBe("room-sidebar-panel");
    expect(button.attributes("aria-label")).toBe("Collapse rooms sidebar");
    expect(button.attributes("title")).toBe("Collapse rooms sidebar");
    expect(button.text()).toBe("«");
  });

  it("flips to an expand button when collapsed", () => {
    const wrapper = mount(SidebarToggle, {
      props: {
        side: "left",
        collapsed: true,
        controls: "room-sidebar-panel",
        label: "Expand rooms sidebar",
      },
    });
    expect(wrapper.get("button").attributes("aria-expanded")).toBe("false");
    expect(wrapper.get("button").text()).toBe("»");
  });

  it("mirrors the icon direction for the right sidebar", () => {
    const wrapper = mount(SidebarToggle, {
      props: {
        side: "right",
        collapsed: false,
        controls: "context-sidebar-panel",
        label: "Collapse context sidebar",
      },
    });
    expect(wrapper.get("button").text()).toBe("»");
    expect(wrapper.get("button").attributes("aria-controls")).toBe("context-sidebar-panel");
  });

  it("emits toggle on click", async () => {
    const wrapper = mount(SidebarToggle, {
      props: {
        side: "right",
        collapsed: true,
        controls: "context-sidebar-panel",
        label: "Expand context sidebar",
      },
    });
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("toggle")).toHaveLength(1);
  });
});
