// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { afterEach, expect, it } from "vitest";
import { initRoomAlign, roomAlign } from "@/composables/useRoomAlign";
import GeneralConfigPage from "./general-config-page.vue";

afterEach(() => {
  localStorage.clear();
  initRoomAlign();
});

it("lets the viewer change and persist the positioning of their Room messages", async () => {
  const wrapper = mount(GeneralConfigPage);
  expect(wrapper.findAll("section")).toHaveLength(3);
  const setting = wrapper.get("#general-room-align");
  expect(setting.findAll("option").map((option) => option.attributes("value"))).toEqual([
    "self-right",
    "left-all",
  ]);
  await setting.setValue("left-all");
  expect(roomAlign.value).toBe("left-all");
  expect(localStorage.getItem("ghrah-room-align")).toBe("left-all");
  wrapper.unmount();
});
