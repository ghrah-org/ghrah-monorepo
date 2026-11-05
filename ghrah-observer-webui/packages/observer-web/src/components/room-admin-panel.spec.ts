// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { useAgentsStore, useRoomsStore } from "@ghrah/observer-core";
import type { RoomInfoPayload } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

const updateRoomMock = vi.fn();
const archiveRoomMock = vi.fn();
const deleteRoomMock = vi.fn();
const joinRoomMock = vi.fn();
const leaveRoomMock = vi.fn();
const listRoomsMock = vi.fn();
const errorRef = ref<string | null>(null);
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    updateRoom: updateRoomMock,
    archiveRoom: archiveRoomMock,
    deleteRoom: deleteRoomMock,
    joinRoom: joinRoomMock,
    leaveRoom: leaveRoomMock,
    listRooms: listRoomsMock,
    error: errorRef,
  }),
}));

import RoomAdminPanel from "./room-admin-panel.vue";

function room(): RoomInfoPayload {
  return {
    room_id: "r1",
    project_id: "p1",
    name: "Architecture",
    status: "active",
    members: [
      {
        subject: "stable-architect",
        subject_type: "agent",
        subject_name: "Architect",
        joined_at: "",
      },
    ],
    seq_watermark: 0,
    version: 7,
    created_at: "",
    updated_at: "",
    archived_at: null,
  };
}

function seed() {
  const rooms = useRoomsStore();
  rooms.replaceProjectRooms("p1", [room()], "active");
  rooms.setActiveRoom("r1");
  useAgentsStore().replaceProjectAgents(
    "p1",
    [
      {
        project_id: "p1",
        agent_id: "stable-architect",
        name: "Architect",
        runtime_state: "running",
      },
      {
        project_id: "p1",
        agent_id: "stable-tester",
        name: "Tester",
        runtime_state: "running",
      },
      {
        project_id: "p2",
        agent_id: "foreign",
        name: "Foreign",
        runtime_state: "running",
      },
    ],
    { status: "active", archived_at: null },
  );
}

describe("RoomAdminPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const mock of [
      updateRoomMock,
      archiveRoomMock,
      deleteRoomMock,
      joinRoomMock,
      leaveRoomMock,
      listRoomsMock,
    ]) {
      mock.mockReset();
    }
    errorRef.value = null;
  });

  it("shows a neutral state until an active room is selected", () => {
    const wrapper = mount(RoomAdminPanel);
    expect(wrapper.text()).toContain("Select a room to manage it");
  });

  it("renames with the current version", async () => {
    seed();
    updateRoomMock.mockResolvedValue({ success: true, data: {} });
    const wrapper = mount(RoomAdminPanel);

    await wrapper.find("#active-room-name").setValue("Architecture v2");
    await wrapper.find(".room-rename-form").trigger("submit.prevent");

    expect(updateRoomMock).toHaveBeenCalledWith("r1", "Architecture v2", 7);
  });

  it("adds and removes members using stable agent IDs", async () => {
    seed();
    joinRoomMock.mockResolvedValue({ success: true, data: {} });
    leaveRoomMock.mockResolvedValue({ success: true, data: {} });
    const wrapper = mount(RoomAdminPanel);

    expect(wrapper.text()).toContain("Architect");
    expect(wrapper.text()).not.toContain("stable-architect");
    expect(wrapper.find('option[value="foreign"]').exists()).toBe(false);

    await wrapper.find("select").setValue("stable-tester");
    await wrapper.find(".room-member-add").trigger("submit.prevent");
    expect(joinRoomMock).toHaveBeenCalledWith("r1", "stable-tester", "agent", "Tester");

    await wrapper.find('button[aria-label="Remove Architect from room"]').trigger("click");
    expect(leaveRoomMock).toHaveBeenCalledWith("r1", "stable-architect");
  });

  it("confirms archive and emits invalidation after success", async () => {
    seed();
    archiveRoomMock.mockResolvedValue({ success: true, data: {} });
    const wrapper = mount(RoomAdminPanel);

    await wrapper.find(".room-lifecycle-section > button").trigger("click");
    await wrapper.find(".base-modal-footer .btn-primary").trigger("click");

    expect(archiveRoomMock).toHaveBeenCalledWith("r1", 7);
    expect(wrapper.emitted("roomInvalidated")?.[0]).toEqual([{ projectId: "p1", roomId: "r1" }]);
  });

  it("confirms deletion and emits invalidation after success", async () => {
    seed();
    deleteRoomMock.mockResolvedValue({ success: true, data: {} });
    const wrapper = mount(RoomAdminPanel);

    await wrapper.find(".room-danger-button").trigger("click");
    await wrapper.find(".base-modal-footer .btn-primary").trigger("click");

    expect(deleteRoomMock).toHaveBeenCalledWith("r1", 7);
    expect(wrapper.emitted("roomInvalidated")?.[0]).toEqual([{ projectId: "p1", roomId: "r1" }]);
  });

  it("preserves the rename draft while refreshing the version after a conflict", async () => {
    seed();
    updateRoomMock
      .mockResolvedValueOnce({
        success: false,
        error: "room_version_conflict",
        error_detail: "Room changed on the server",
      })
      .mockResolvedValueOnce({ success: true, data: {} });
    listRoomsMock.mockImplementation(async () => {
      useRoomsStore().replaceProjectRooms(
        "p1",
        [{ ...room(), name: "Remote name", version: 8 }],
        "active",
      );
      return { success: true, data: {} };
    });
    const wrapper = mount(RoomAdminPanel);

    await wrapper.find("#active-room-name").setValue("Conflicting name");
    await wrapper.find(".room-rename-form").trigger("submit.prevent");
    expect(wrapper.text()).toContain("Room changed on the server");

    const refresh = wrapper.findAll("button").find((button) => button.text() === "Refresh");
    await refresh?.trigger("click");
    expect(listRoomsMock).toHaveBeenCalledWith("p1", "active");
    expect(wrapper.find<HTMLInputElement>("#active-room-name").element.value).toBe(
      "Conflicting name",
    );

    await wrapper.find(".room-rename-form").trigger("submit.prevent");
    expect(updateRoomMock).toHaveBeenLastCalledWith("r1", "Conflicting name", 8);
  });
});
