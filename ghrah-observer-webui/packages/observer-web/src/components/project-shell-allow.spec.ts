// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
// SPDX-License-Identifier: Apache-2.0
// @vitest-environment happy-dom
import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, expect, it, vi } from "vitest";
const { list, remove } = vi.hoisted(() => ({ list: vi.fn(), remove: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ listShellAllow: list, deleteShellAllow: remove }),
}));
import Panel from "./project-shell-allow.vue";
beforeEach(() => {
  list
    .mockReset()
    .mockImplementation(async () => ({
      project_id: "p1",
      entries: [{ kind: "exact", key: "python a/b.py", label: "python a/b.py", created_at: "" }],
      builtins: [{ key: "test", label: "pnpm test", readonly: true }],
    }));
  remove.mockReset().mockResolvedValue(undefined);
});
it("loads current project and only offers deletion for stored entries", async () => {
  const wrapper = mount(Panel, { props: { projectId: "p1" } });
  await flushPromises();
  expect(list).toHaveBeenCalledWith("p1");
  expect(wrapper.text()).toContain("pnpm test");
  expect(wrapper.findAll("button[aria-label]")).toHaveLength(1);
  await wrapper.get("button[aria-label]").trigger("click");
  await flushPromises();
  expect(remove).toHaveBeenCalledWith("p1", "exact", "python a/b.py");
  expect(wrapper.text()).not.toContain("python a/b.py");
  expect(wrapper.text()).toContain("pnpm test");
  expect(wrapper.emitted("changed")).toHaveLength(1);
});
it("retains the entry on deletion failure", async () => {
  remove.mockRejectedValue(new Error("offline"));
  const wrapper = mount(Panel, { props: { projectId: "p1" } });
  await flushPromises();
  await wrapper.get("button[aria-label]").trigger("click");
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("offline");
  expect(wrapper.text()).toContain("python a/b.py");
  expect(wrapper.emitted("changed")).toBeUndefined();
});
it("can retry a failed load", async () => {
  list.mockRejectedValueOnce(new Error("offline"));
  const wrapper = mount(Panel, { props: { projectId: "p2" } });
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain("offline");
  await wrapper.get("button").trigger("click");
  await flushPromises();
  expect(list).toHaveBeenLastCalledWith("p2");
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
});
