// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useAgentsStore, useProjectsStore, useRoomsStore } from "@ghrah/observer-core";
import type { RoomInfoPayload } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const switchRoomMock = vi.fn();
const createRoomMock = vi.fn();
const archiveRoomMock = vi.fn();
const deleteRoomMock = vi.fn();
const errorRef = ref<string | null>(null);
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    switchRoom: switchRoomMock,
    createRoom: createRoomMock,
    archiveRoom: archiveRoomMock,
    deleteRoom: deleteRoomMock,
    error: errorRef,
  }),
}));

import RoomSelector from "./room-selector.vue";

function room(
  id: string,
  projectId: string,
  name: string,
  members: Array<{ subject: string; subjectName?: string }> = [],
  status: "active" | "archived" = "active",
): RoomInfoPayload {
  return {
    room_id: id,
    project_id: projectId,
    name,
    status,
    members: members.map(({ subject, subjectName }) => ({
      subject,
      subject_type: "agent" as const,
      subject_name: subjectName ?? subject,
      joined_at: "",
    })),
    seq_watermark: 0,
    version: 1,
    created_at: "",
    updated_at: "",
    archived_at: status === "archived" ? "2026-09-10T00:00:00Z" : null,
  };
}

function setProjects(activeProjectId: string | null = "p1") {
  const projects = useProjectsStore();
  projects.setProjectsFromList([
    { project_id: "p1", name: "Project One", archived_at: null } as never,
    { project_id: "p2", name: "Project Two", archived_at: null } as never,
  ]);
  projects.setActiveProject(activeProjectId);
}

function setActiveRooms(list: RoomInfoPayload[]) {
  const rooms = useRoomsStore();
  for (const projectId of new Set(list.map((item) => item.project_id))) {
    rooms.replaceProjectRooms(
      projectId,
      list.filter((item) => item.project_id === projectId),
      "active",
    );
  }
}

describe("RoomSelector", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    switchRoomMock.mockReset();
    createRoomMock.mockReset();
    archiveRoomMock.mockReset();
    deleteRoomMock.mockReset();
    errorRef.value = null;
  });

  it("requires a project before listing or creating rooms", () => {
    setProjects(null);
    setActiveRooms([room("r1", "p1", "architecture"), room("r2", "p2", "other")]);

    const wrapper = mount(RoomSelector);

    expect(wrapper.text()).toContain("Select a project first");
    expect(wrapper.findAll("li")).toHaveLength(0);
    expect(wrapper.find('button[title="New room"]').attributes("disabled")).toBeDefined();
  });

  it("shows an empty state for the active project", () => {
    setProjects();
    const wrapper = mount(RoomSelector);
    expect(wrapper.text()).toContain("No rooms");
  });

  it("lists only active rooms in the active project and emits a scoped target", async () => {
    setProjects("p1");
    setActiveRooms([
      room("r1", "p1", "architecture", [{ subject: "architect" }]),
      room("r2", "p2", "other"),
    ]);
    useRoomsStore().replaceProjectRooms(
      "p1",
      [room("archived", "p1", "old room", [], "archived")],
      "archived",
    );

    const wrapper = mount(RoomSelector);
    const rows = wrapper.findAll("li");
    expect(rows).toHaveLength(1);
    expect(rows[0].text()).toContain("architecture");
    expect(rows[0].text()).not.toContain("old room");

    await rows[0].find("button").trigger("click");

    expect(switchRoomMock).toHaveBeenCalledWith("r1");
    expect(wrapper.emitted("openRoom")?.[0]).toEqual([
      { room_id: "r1", project_id: "p1", name: "architecture" },
    ]);
  });

  it("calculates multi-room membership inside the active project only", () => {
    setProjects("p1");
    setActiveRooms([
      room("r1", "p1", "one", [{ subject: "stable-agent", subjectName: "Architect" }]),
      room("r2", "p1", "two", [{ subject: "stable-agent", subjectName: "Architect" }]),
      room("r3", "p2", "other", [{ subject: "p2-only", subjectName: "Architect" }]),
    ]);

    const wrapper = mount(RoomSelector);
    const badges = wrapper.findAll(".room-member");
    expect(badges).toHaveLength(2);
    expect(badges.every((badge) => badge.classes().includes("bg-amber-200"))).toBe(true);
  });

  it("derives member labels from subject_name and then the project agent store", () => {
    setProjects("p1");
    useAgentsStore().replaceProjectAgents(
      "p1",
      [{ project_id: "p1", agent_id: "stable-agent", name: "Architect", runtime_state: "running" }],
      { status: "active", archived_at: null },
    );
    setActiveRooms([
      room("r1", "p1", "one", [
        { subject: "named-id", subjectName: "Named member" },
        { subject: "stable-agent", subjectName: "" },
      ]),
    ]);

    const wrapper = mount(RoomSelector);
    const titles = wrapper.findAll(".room-member").map((badge) => badge.attributes("title"));
    expect(titles).toEqual(["Named member", "Architect"]);
  });

  it("highlights the active room", () => {
    setProjects("p1");
    setActiveRooms([room("r1", "p1", "one"), room("r2", "p1", "two")]);
    useRoomsStore().setActiveRoom("r2");

    const wrapper = mount(RoomSelector);
    const rows = wrapper.findAll(".sidebar-row");
    expect(rows[0].attributes("aria-current")).toBeUndefined();
    expect(rows[1].attributes("aria-current")).toBe("page");
  });

  it("offers scoped lifecycle actions from the room row menu", async () => {
    setProjects("p1");
    setActiveRooms([room("r1", "p1", "one")]);
    archiveRoomMock.mockResolvedValue({ success: true, data: {} });
    const wrapper = mount(RoomSelector);

    await wrapper.find('button[aria-label="Actions for one"]').trigger("click");
    await wrapper
      .findAll(".sidebar-row-menu button")
      .find((button) => button.text() === "Archive room")
      ?.trigger("click");
    await wrapper.find(".base-modal-footer .ui-button-primary").trigger("click");

    expect(archiveRoomMock).toHaveBeenCalledWith("r1", 1);
    expect(wrapper.emitted("roomInvalidated")?.[0]).toEqual([{ projectId: "p1", roomId: "r1" }]);
  });

  it("closes the room action menu on outside pointer interaction or Escape", async () => {
    setProjects("p1");
    setActiveRooms([room("r1", "p1", "one")]);
    const wrapper = mount(RoomSelector);
    const trigger = wrapper.find('button[aria-label="Actions for one"]');

    await trigger.trigger("click");
    expect(wrapper.find(".sidebar-row-menu").exists()).toBe(true);

    document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".sidebar-row-menu").exists()).toBe(false);

    await trigger.trigger("click");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".sidebar-row-menu").exists()).toBe(false);
    wrapper.unmount();
  });

  it("creates in the active project and opens the server-returned room", async () => {
    setProjects("p1");
    createRoomMock.mockResolvedValue({
      success: true,
      data: { room: { room_id: "server-room-id", name: "architecture" } },
    });
    const wrapper = mount(RoomSelector);

    await wrapper.find('button[title="New room"]').trigger("click");
    await wrapper.find('input[placeholder="Room name"]').setValue("architecture");
    await wrapper.find("form").trigger("submit.prevent");

    expect(createRoomMock).toHaveBeenCalledWith("p1", "architecture");
    expect(switchRoomMock).toHaveBeenCalledWith("server-room-id");
    expect(wrapper.emitted("openRoom")?.[0]).toEqual([
      { room_id: "server-room-id", project_id: "p1", name: "architecture" },
    ]);
    expect(wrapper.find('input[placeholder="Room name"]').exists()).toBe(false);
  });

  it("keeps the form open and exposes a creation failure", async () => {
    setProjects("p1");
    createRoomMock.mockResolvedValue({ success: false, error: "boom" });
    const wrapper = mount(RoomSelector);

    await wrapper.find('button[title="New room"]').trigger("click");
    await wrapper.find('input[placeholder="Room name"]').setValue("duplicate");
    await wrapper.find("form").trigger("submit.prevent");

    expect(errorRef.value).toBe("boom");
    expect(wrapper.find('input[placeholder="Room name"]').exists()).toBe(true);
  });
});
