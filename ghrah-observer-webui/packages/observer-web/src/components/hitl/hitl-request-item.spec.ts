// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment happy-dom
import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
import type { HitlRequest } from "@ghrah/observer-core";
const { save, respond } = vi.hoisted(() => ({ save: vi.fn(), respond: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ rememberShellCommand: save, sendHitlResponse: respond }),
}));
import Item from "./hitl-request-item.vue";
const request: HitlRequest = {
  promiseId: "h1",
  projectId: "p1",
  agentId: "a1",
  agentName: "coder",
  abilityName: "execute_command",
  toolArgs: { command: "pnpm run lint" },
  context: {},
  reason: "",
  shellRemember: {
    exact_key: "pnpm run lint",
    exact_label: "pnpm run lint",
    class_key: "pnpm:script:lint",
    class_label: "pnpm:script:lint",
  },
};
beforeEach(() => {
  save.mockReset().mockResolvedValue(undefined);
  respond.mockReset().mockResolvedValue(undefined);
});
function button(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll("button").find((b) => b.text() === text)!;
}
it("saves before approving, using request project and exact candidate", async () => {
  let complete!: () => void;
  save.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        complete = resolve;
      }),
  );
  const wrapper = mount(Item, { props: { request } });
  await button(wrapper, "Remember this command and allow").trigger("click");
  expect(save).toHaveBeenCalledWith("p1", "exact", "pnpm run lint", "pnpm run lint");
  expect(respond).not.toHaveBeenCalled();
  expect(button(wrapper, "Allow once").attributes("disabled")).toBeDefined();
  complete();
  await flushPromises();
  expect(respond).toHaveBeenCalledWith("h1", true);
});
it("does not approve after failed save and permits retry", async () => {
  save.mockRejectedValueOnce(new Error("offline"));
  const wrapper = mount(Item, { props: { request } });
  await button(wrapper, "Remember this class and allow").trigger("click");
  await flushPromises();
  expect(save).toHaveBeenCalledWith("p1", "class", "pnpm:script:lint", "pnpm:script:lint");
  expect(respond).not.toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toContain("offline");
  await button(wrapper, "Remember this class and allow").trigger("click");
  await flushPromises();
  expect(respond).toHaveBeenCalledWith("h1", true);
});
it("hides class for scripts and does not save when allowing once", async () => {
  const wrapper = mount(Item, {
    props: {
      request: {
        ...request,
        shellRemember: { ...request.shellRemember!, class_key: null, class_label: null },
      },
    },
  });
  expect(wrapper.text()).not.toContain("Remember this class and allow");
  await button(wrapper, "Allow once").trigger("click");
  await flushPromises();
  expect(save).not.toHaveBeenCalled();
  expect(respond).toHaveBeenCalledWith("h1", true);
});
it("keeps legacy approval and rejection without storing memory", async () => {
  const wrapper = mount(Item, { props: { request: { ...request, shellRemember: null } } });
  expect(wrapper.text()).not.toContain("Remember this");
  await button(wrapper, "Reject").trigger("click");
  await wrapper.get("textarea").setValue("not allowed");
  await button(wrapper, "Confirm Reject").trigger("click");
  await flushPromises();
  expect(save).not.toHaveBeenCalled();
  expect(respond).toHaveBeenCalledWith("h1", false, "not allowed");
});
