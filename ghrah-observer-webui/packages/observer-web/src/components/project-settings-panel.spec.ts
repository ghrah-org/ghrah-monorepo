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

const updateProject = vi.fn();
const archiveProject = vi.fn();
const deleteProject = vi.fn();
const restoreRoom = vi.fn();
const deleteRoom = vi.fn();
const listProjects = vi.fn();
const listRooms = vi.fn();
const observerError = ref<string | null>(null);

vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    updateProject,
    archiveProject,
    deleteProject,
    restoreRoom,
    deleteRoom,
    listProjects,
    listRooms,
    error: observerError,
  }),
}));

import ProjectSettingsPanel from "./project-settings-panel.vue";

function project(archived = false): ProjectInfoPayload {
  return {
    project_id: "p1",
    name: "Alpha",
    description: "Original",
    project_root_locator: "/private/p1",
    manifest_ref: "demo/project",
    workspaces: [{ workspace_id: "w1", role: "default", default_for_agents: true }],
    agents: [{ name: "worker", agent_id: "a1" }],
    status: archived ? "stopped" : "active",
    version: 4,
    archived_at: archived ? "2026-09-09T08:00:00Z" : null,
  } as never;
}

function room(id: string, status: "active" | "archived"): RoomInfoPayload {
  return {
    room_id: id,
    project_id: "p1",
    name: id === "r1" ? "Architecture" : "Frozen Room",
    status,
    members: [],
    seq_watermark: 0,
    version: status === "archived" ? 3 : 1,
    created_at: "",
    updated_at: "",
    archived_at: status === "archived" ? "2026-09-09T08:00:00Z" : null,
  };
}

function mountPanel(options: { archived?: boolean; rooms?: boolean } = {}) {
  useProjectsStore().setProjectsFromList([project(options.archived)]);
  if (options.rooms) {
    useRoomsStore().replaceProjectRooms("p1", [room("r1", "active")], "active");
    useRoomsStore().replaceProjectRooms("p1", [room("r2", "archived")], "archived");
  }
  return mount(ProjectSettingsPanel, { props: { projectId: "p1" } });
}

function confirmButton(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper
    .get('[role="dialog"]')
    .findAll("button")
    .find((button) => button.text() === label);
}

describe("ProjectSettingsPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const mock of [
      updateProject,
      archiveProject,
      deleteProject,
      restoreRoom,
      deleteRoom,
      listProjects,
      listRooms,
    ]) {
      mock.mockReset();
      mock.mockResolvedValue({ success: true });
    }
    observerError.value = null;
  });

  it("loads active and archived Rooms only for the requested Project", async () => {
    const wrapper = mountPanel({ rooms: true });
    await flushPromises();
    expect(listRooms).toHaveBeenCalledWith("p1", "active");
    expect(listRooms).toHaveBeenCalledWith("p1", "archived");
    expect(wrapper.text()).toContain("Frozen Room");
    expect(wrapper.findAll("dd").map((node) => node.text())).toEqual([
      "/private/p1",
      "1",
      "1",
      "2",
    ]);
  });

  it("updates editable fields with the current optimistic version", async () => {
    const wrapper = mountPanel();
    await wrapper.get("#project-settings-name").setValue("Alpha 2");
    await wrapper.get("#project-settings-description").setValue("Updated");
    await wrapper.get(".project-settings-form").trigger("submit");
    expect(updateProject).toHaveBeenCalledWith("p1", {
      name: "Alpha 2",
      description: "Updated",
      manifestRef: "demo/project",
      expectedVersion: 4,
    });
  });

  it("uses stable conflict codes, shows detail, and preserves drafts across refresh", async () => {
    updateProject.mockResolvedValue({
      success: false,
      error: "project_version_conflict",
      error_detail: "Project changed on the server",
    });
    const wrapper = mountPanel();
    await wrapper.get("#project-settings-name").setValue("Alpha draft");
    await wrapper.get(".project-settings-form").trigger("submit");
    expect(wrapper.text()).toContain("Project changed on the server");
    await wrapper.get(".project-panel-error button").trigger("click");
    await flushPromises();
    expect(listProjects).toHaveBeenCalledWith({ archived: false });
    expect(wrapper.get<HTMLInputElement>("#project-settings-name").element.value).toBe(
      "Alpha draft",
    );
  });

  it("restores and permanently deletes only the archived Room definition and log", async () => {
    const wrapper = mountPanel({ rooms: true });
    await flushPromises();
    const actions = wrapper.findAll(".archived-room-list button");
    await actions[0].trigger("click");
    await confirmButton(wrapper, "Restore Room")!.trigger("click");
    await flushPromises();
    expect(restoreRoom).toHaveBeenCalledWith("r2", 3);

    await actions[1].trigger("click");
    expect(wrapper.get(".confirm-dialog-message").text()).toContain("Its Agents remain");
    await confirmButton(wrapper, "Delete Room permanently")!.trigger("click");
    await flushPromises();
    expect(deleteRoom).toHaveBeenCalledWith("r2", 3);
  });

  it("disables Room restore while the parent Project is archived", async () => {
    const wrapper = mountPanel({ archived: true, rooms: true });
    await flushPromises();
    const restore = wrapper.get<HTMLButtonElement>(".archived-room-list .btn-secondary");
    expect(restore.element.disabled).toBe(true);
    expect(restore.attributes("title")).toContain("Restore the Project");
  });

  it("archives with expected_version and leaves store invalidation to server events", async () => {
    const wrapper = mountPanel();
    await wrapper.get(".project-lifecycle-section button").trigger("click");
    await confirmButton(wrapper, "Archive Project")!.trigger("click");
    expect(archiveProject).toHaveBeenCalledWith("p1", 4);
    expect(useProjectsStore().projects.has("p1")).toBe(true);
  });

  it("requires exact name and explicit cascade before deleting a Project with Rooms", async () => {
    const wrapper = mountPanel({ rooms: true });
    await flushPromises();
    expect(wrapper.get(".project-danger-zone").text()).toContain(
      "External workspaces remain untouched",
    );
    await wrapper.get("#project-delete-name").setValue("Alpha");
    const deleteButton = wrapper.get<HTMLButtonElement>(".project-danger-button");
    expect(deleteButton.element.disabled).toBe(true);
    await wrapper.get('.project-cascade-check input[type="checkbox"]').setValue(true);
    await deleteButton.trigger("click");
    await confirmButton(wrapper, "Delete Project permanently")!.trigger("click");
    expect(deleteProject).toHaveBeenCalledWith("p1", 4, true);
  });
});
