// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SidebarRow from "./sidebar-row.vue";

describe("SidebarRow", () => {
  it("renders a full-width accessible active button", async () => {
    const wrapper = mount(SidebarRow, { props: { active: true }, slots: { default: "General" } });
    const button = wrapper.get("button");
    expect(button.attributes("aria-current")).toBe("page");
    expect(button.classes()).toContain("active");
    await button.trigger("click");
    expect(wrapper.emitted("select")).toHaveLength(1);
  });
});
