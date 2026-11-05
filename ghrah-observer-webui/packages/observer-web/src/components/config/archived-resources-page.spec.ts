// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useProjectsStore, useRoomsStore } from "@ghrah/observer-core";
import type { ProjectInfoPayload, RoomInfoPayload } from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const listProjects = vi.fn();
const restoreProject = vi.fn();
const deleteProject = vi.fn();
const observerError = ref<string | null>(null);

vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    listProjects,
    restoreProject,
    deleteProject,
    error: observerError,
  }),
}));

import ArchivedResourcesPage from "./archived-resources-page.vue";

function project(): ProjectInfoPayload {
  return {
    project_id: "p1",
    name: "Archived Alpha",
    status: "stopped",
    version: 4,
    archived_at: "2026-09-09T08:00:00Z",
    project_root_locator: "/private/p1",
    workspaces: [],
    agents: [{ name: "worker", agent_id: "a1" }],
  } as never;
}

function archivedRoom(): RoomInfoPayload {
  return {
    room_id: "r1",
    project_id: "p1",
    name: "Frozen Room",
    status: "archived",
    members: [],
    seq_watermark: 0,
    version: 2,
    created_at: "",
    updated_at: "",
    archived_at: "2026-09-09T08:00:00Z",
  };
}

function mountPage(withKnownRoom = false) {
  useProjectsStore().setProjectsFromList([project()]);
  if (withKnownRoom) useRoomsStore().replaceProjectRooms("p1", [archivedRoom()], "archived");
  return mount(ArchivedResourcesPage);
}

function confirmButton(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper
    .get('[role="dialog"]')
    .findAll("button")
    .find((button) => button.text() === label);
}

describe("ArchivedResourcesPage", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const mock of [listProjects, restoreProject, deleteProject]) {
      mock.mockReset();
      mock.mockResolvedValue({ success: true });
    }
    observerError.value = null;
  });

  it("loads only the archived Project bucket using the structured filter", async () => {
    const wrapper = mountPage();
    await flushPromises();
    expect(listProjects).toHaveBeenCalledWith({ archived: true });
    expect(wrapper.text()).toContain("Archived Alpha");
    expect(wrapper.text()).not.toContain("Archived Rooms");
  });

  it("restores a Project and refreshes both Project buckets", async () => {
    const wrapper = mountPage();
    await flushPromises();
    await wrapper.get(".archived-resource-actions .btn-primary").trigger("click");
    await confirmButton(wrapper, "Restore Project")!.trigger("click");
    await flushPromises();
    expect(restoreProject).toHaveBeenCalledWith("p1", 4);
    expect(listProjects).toHaveBeenCalledWith({ archived: false });
    expect(listProjects).toHaveBeenCalledWith({ archived: true });
  });

  it("requires exact name and explicit cascade when known Rooms exist", async () => {
    const wrapper = mountPage(true);
    await flushPromises();
    await wrapper.get(".archived-danger-toggle").trigger("click");
    await wrapper.get('input[type="text"]').setValue("Archived Alpha");
    const button = wrapper.get<HTMLButtonElement>(".project-danger-button");
    expect(button.element.disabled).toBe(true);
    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(button.element.disabled).toBe(false);
    await button.trigger("click");
    await confirmButton(wrapper, "Delete Project permanently")!.trigger("click");
    await flushPromises();
    expect(deleteProject).toHaveBeenCalledWith("p1", 4, true);
  });

  it("turns project_has_rooms into a retained cascade retry instead of guessing inventory", async () => {
    deleteProject
      .mockResolvedValueOnce({
        success: false,
        error: "project_has_rooms",
        error_detail: "Project still owns Rooms",
      })
      .mockResolvedValueOnce({ success: true });
    const wrapper = mountPage();
    await flushPromises();
    await wrapper.get(".archived-danger-toggle").trigger("click");
    await wrapper.get('input[type="text"]').setValue("Archived Alpha");
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
    await wrapper.get(".project-danger-button").trigger("click");
    await confirmButton(wrapper, "Delete Project permanently")!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Project still owns Rooms");
    expect(wrapper.get<HTMLInputElement>('input[type="text"]').element.value).toBe(
      "Archived Alpha",
    );
    await wrapper.get('input[type="checkbox"]').setValue(true);
    await wrapper.get(".project-danger-button").trigger("click");
    await confirmButton(wrapper, "Delete Project permanently")!.trigger("click");
    await flushPromises();
    expect(deleteProject).toHaveBeenLastCalledWith("p1", 4, true);
  });
});
