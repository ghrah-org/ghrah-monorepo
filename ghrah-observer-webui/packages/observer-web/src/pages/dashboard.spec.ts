// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  useAgentsStore,
  useBranchesStore,
  useProjectsStore,
  useRoomsStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import type { ProjectInfoPayload, RoomInfoPayload } from "@ghrah/protocol";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

const switchProjectMock = vi.fn();
const switchRoomMock = vi.fn();
const selectAgentMock = vi.fn();
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    switchProject: switchProjectMock,
    switchRoom: switchRoomMock,
    selectAgent: selectAgentMock,
  }),
}));
vi.mock("@/components/action-chain/action-chain-panel.vue", () => ({
  default: {
    props: ["agent"],
    template: '<div class="chain-panel-stub">{{ agent.agentName }}</div>',
  },
}));
vi.mock("@/components/agent-action-menu.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/chat/chat-panel.vue", () => ({
  default: {
    props: ["projectId", "roomId"],
    template: '<div class="chat-panel-stub">{{ projectId }}/{{ roomId }}</div>',
  },
}));
vi.mock("@/components/hitl/hitl-bar.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/instance-profile-control.vue", () => ({
  default: { emits: ["openSettings"], template: "<div />" },
}));
vi.mock("@/components/nav/project-selector.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("@/components/project-changes-panel.vue", () => ({
  default: { template: "<div>changes panel</div>" },
}));
vi.mock("@/components/project-settings-panel.vue", () => ({
  default: { template: "<div>settings panel</div>" },
}));
vi.mock("@/components/agents-overview-panel.vue", () => ({
  default: { emits: ["openAgent"], template: "<div>agents panel</div>" },
}));

import { useSidebarLayout } from "@/composables/useSidebarLayout";
import Dashboard from "./dashboard.vue";

const mediaListeners = new Set<() => void>();
let viewportRem = 100;

function installMatchMedia() {
  viewportRem = 100;
  mediaListeners.clear();
  window.matchMedia = ((query: string) => {
    const min = Number(/min-width:\s*([\d.]+)rem/.exec(query)?.[1] ?? NaN);
    return {
      media: query,
      get matches() {
        return Number.isFinite(min) && viewportRem >= min;
      },
      addEventListener(_type: string, listener: () => void) {
        mediaListeners.add(listener);
      },
      removeEventListener(_type: string, listener: () => void) {
        mediaListeners.delete(listener);
      },
      addListener(listener: () => void) {
        mediaListeners.add(listener);
      },
      removeListener(listener: () => void) {
        mediaListeners.delete(listener);
      },
      dispatchEvent() {
        return false;
      },
      onchange: null,
    };
  }) as unknown as typeof window.matchMedia;
}

function setViewportRem(rem: number) {
  viewportRem = rem;
  for (const listener of mediaListeners) listener();
}

enableAutoUnmount(afterEach);

const RoomSelectorStub = {
  emits: ["openRoom"],
  template: `<div>
    <button id="open-p1-room" @click="$emit('openRoom', { room_id: 'r1', project_id: 'p1', name: 'Room One' })">p1 room</button>
    <button id="open-p2-room" @click="$emit('openRoom', { room_id: 'r2', project_id: 'p2', name: 'Room Two' })">p2 room</button>
  </div>`,
};
const ProjectFixedNavStub = {
  emits: ["openProjectView"],
  template: `<div>
    <button id="open-changes" @click="$emit('openProjectView', { kind: 'changes', projectId: 'p1' })">changes</button>
  </div>`,
};
const AgentListStub = {
  emits: ["openAgent", "openSettings"],
  template: `<div>
    <button id="open-agent-context" @click="$emit('openAgent', {
      projectId: 'p2', agentId: 'a2', agentName: 'Coder'
    })">agent context</button>
    <button id="open-chain" @click="$emit('openAgent', {
      projectId: 'p2', agentId: 'a2', agentName: 'Coder', sessionId: 's2', branchId: 'b2'
    })">agent chain</button>
  </div>`,
};

function project(id: string, name: string): ProjectInfoPayload {
  return {
    project_id: id,
    name,
    status: "active",
    archived_at: null,
    agents: [],
  } as never;
}

function room(id: string, projectId: string, name: string): RoomInfoPayload {
  return {
    room_id: id,
    project_id: projectId,
    name,
    status: "active",
    members: [],
    seq_watermark: 0,
    version: 1,
    created_at: "",
    updated_at: "",
    archived_at: null,
  };
}

function mountDashboard() {
  return mount(Dashboard, {
    global: {
      stubs: {
        ProjectSelector: true,
        RoomSelector: RoomSelectorStub,
        ProjectFixedNav: ProjectFixedNavStub,
        InstanceProfileControl: true,
        HitlInbox: true,
        RoomAdminPanel: true,
        AgentList: AgentListStub,
        ChatPanel: {
          props: ["projectId", "roomId"],
          template: '<div class="chat-panel-stub">{{ projectId }}/{{ roomId }}</div>',
        },
        ActionChainPanel: {
          props: ["agent"],
          template: '<div class="chain-panel-stub">{{ agent.agentName }}</div>',
        },
        ProjectChangesPanel: { template: "<div>changes panel</div>" },
        ProjectSettingsPanel: { template: "<div>settings panel</div>" },
        AgentsOverviewPanel: { template: "<div>agents panel</div>" },
      },
    },
  });
}

describe("Dashboard workspace tabs", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    switchProjectMock.mockReset();
    switchRoomMock.mockReset();
    selectAgentMock.mockReset();

    installMatchMedia();
    // 侧边栏状态是模块级单例；每个用例前恢复为展开，避免相互污染。
    localStorage.clear();
    const sidebarLayout = useSidebarLayout();
    if (sidebarLayout.roomSidebarCollapsed.value) sidebarLayout.toggleRoomSidebar();
    if (sidebarLayout.contextSidebarCollapsed.value) sidebarLayout.toggleContextSidebar();
    localStorage.clear();

    const projects = useProjectsStore();
    projects.setProjectsFromList([project("p1", "Project One"), project("p2", "Project Two")]);
    projects.setActiveProject("p1");
    useRoomsStore().replaceProjectRooms("p1", [room("r1", "p1", "Room One")], "active");
    useRoomsStore().replaceProjectRooms("p2", [room("r2", "p2", "Room Two")], "active");

    const agent = { projectId: "p2", agentId: "a2", agentName: "Coder" };
    useAgentsStore().replaceProjectAgents(
      "p2",
      [{ project_id: "p2", agent_id: "a2", name: "Coder", runtime_state: "running" }],
      { status: "active", archived_at: null },
    );
    useSessionsStore().replaceAgentSessions(
      agent,
      [{ session_id: "s2", agent_name: "Coder", lifecycle: "open" } as never],
      { explicitActiveSessionId: "s2" },
    );
    useBranchesStore().replaceSessionBranches(
      { ...agent, sessionId: "s2" },
      [{ branch_id: "b2", session_id: "s2", name: "main" } as never],
      { explicitActiveBranchId: "b2" },
    );

    switchProjectMock.mockImplementation(async (projectId: string) => {
      useProjectsStore().setActiveProject(projectId);
      return { success: true };
    });
    switchRoomMock.mockImplementation(async (roomId: string | null) => {
      useRoomsStore().setActiveRoom(roomId);
      return { success: true };
    });
    selectAgentMock.mockImplementation((target: never) => useAgentsStore().selectAgent(target));
  });

  it("switches project before opening a room from another project", async () => {
    const order: string[] = [];
    switchProjectMock.mockImplementation(async (projectId: string) => {
      order.push(`project:${projectId}`);
      useProjectsStore().setActiveProject(projectId);
      return { success: true };
    });
    switchRoomMock.mockImplementation(async (roomId: string) => {
      order.push(`room:${roomId}`);
      useRoomsStore().setActiveRoom(roomId);
      return { success: true };
    });
    const wrapper = mountDashboard();

    await wrapper.find("#open-p2-room").trigger("click");
    await nextTick();

    expect(order).toEqual(["project:p2", "room:r2"]);
    expect(useProjectsStore().activeProjectId).toBe("p2");
    expect(useRoomsStore().activeRoomId).toBe("r2");
    expect(wrapper.find(".workspace-tab .tab-label").text()).toBe("Room Two");
  });

  it("derives room and project labels from stores instead of tab snapshots", async () => {
    const wrapper = mountDashboard();
    await wrapper.find("#open-p1-room").trigger("click");
    await wrapper.find("#open-changes").trigger("click");

    useRoomsStore().onRoomUpdated({ room: room("r1", "p1", "Renamed Room") });
    useProjectsStore().onProjectEvent({ project: project("p1", "Renamed Project") });
    await nextTick();

    const labels = wrapper.findAll(".tab-label").map((node) => node.text());
    expect(labels).toContain("Renamed Room");
    expect(labels).toContain("Renamed Project · Changes");
  });

  it("passes tab-scoped props (projectId/roomId, agent) to the cached panels", async () => {
    const wrapper = mountDashboard();

    await wrapper.find("#open-p1-room").trigger("click");
    await nextTick();
    expect(wrapper.find(".chat-panel-stub").text()).toBe("p1/r1");

    await wrapper.find("#open-chain").trigger("click");
    await nextTick();
    expect(wrapper.find(".chain-panel-stub").text()).toBe("Coder");
  });

  it("restores a complete chain target after switching projects", async () => {
    const order: string[] = [];
    switchProjectMock.mockImplementation(async (projectId: string) => {
      order.push(`project:${projectId}`);
      useProjectsStore().setActiveProject(projectId);
      return { success: true };
    });
    selectAgentMock.mockImplementation((target: never) => {
      order.push("agent:a2");
      useAgentsStore().selectAgent(target);
    });
    const wrapper = mountDashboard();

    await wrapper.find("#open-chain").trigger("click");
    await nextTick();

    expect(order).toEqual(["project:p2", "agent:a2"]);
    expect(useAgentsStore().selectedAgentTarget?.agentId).toBe("a2");
    expect(
      useSessionsStore().activeSessionId({
        projectId: "p2",
        agentId: "a2",
        agentName: "Coder",
      }),
    ).toBe("s2");
    expect(wrapper.find(".workspace-tab .tab-label").text()).toBe("Coder");
  });

  it("upgrades an Agent context tab when a complete ChainTarget becomes available", async () => {
    const agent = { projectId: "p2", agentId: "a2", agentName: "Coder" };
    useBranchesStore().clearAgent(agent);
    useSessionsStore().clearAgent(agent);
    const wrapper = mountDashboard();

    await wrapper.find("#open-agent-context").trigger("click");
    expect(wrapper.findAll(".workspace-tab")).toHaveLength(1);

    useSessionsStore().replaceAgentSessions(
      agent,
      [{ session_id: "s2", agent_name: "Coder", lifecycle: "open" } as never],
      { explicitActiveSessionId: "s2" },
    );
    useBranchesStore().replaceSessionBranches(
      { ...agent, sessionId: "s2" },
      [{ branch_id: "b2", session_id: "s2", name: "main" } as never],
      { explicitActiveBranchId: "b2" },
    );
    await wrapper.find("#open-chain").trigger("click");

    expect(wrapper.findAll(".workspace-tab")).toHaveLength(1);
  });

  it("closes room and chain tabs when remote state invalidates their targets", async () => {
    const wrapper = mountDashboard();
    await wrapper.find("#open-p1-room").trigger("click");
    await wrapper.find("#open-chain").trigger("click");
    expect(wrapper.findAll(".workspace-tab")).toHaveLength(2);

    useRoomsStore().onRoomArchived({
      room: { ...room("r1", "p1", "Room One"), status: "archived" },
    });
    useSessionsStore().removeSession({
      project_id: "p2",
      agent_id: "a2",
      agent_name: "Coder",
      session_id: "s2",
    });
    await nextTick();
    await nextTick();

    expect(wrapper.findAll(".workspace-tab")).toHaveLength(0);
  });

  it("reactivates a surviving tab only once during cascaded Project invalidation", async () => {
    const wrapper = mountDashboard();
    await wrapper.find("#open-changes").trigger("click");
    await wrapper.find("#open-p2-room").trigger("click");
    switchProjectMock.mockClear();
    switchRoomMock.mockClear();

    useProjectsStore().onProjectDeleted({ project: project("p2", "Project Two") });
    useRoomsStore().clearProject("p2");
    useAgentsStore().clearProject("p2");
    useSessionsStore().clearProject("p2");
    useBranchesStore().clearProject("p2");
    await nextTick();
    await nextTick();

    expect(wrapper.findAll(".workspace-tab")).toHaveLength(1);
    expect(wrapper.find(".workspace-tab .tab-label").text()).toBe("Project One · Changes");
    expect(switchProjectMock).toHaveBeenCalledTimes(1);
    expect(switchProjectMock).toHaveBeenCalledWith("p1");
    expect(switchRoomMock).not.toHaveBeenCalled();
  });

  it("collapses the room sidebar into a rail and persists the state", async () => {
    const wrapper = mountDashboard();
    const toggle = wrapper.get('[aria-controls="room-sidebar-panel"]');
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(toggle.attributes("aria-label")).toBe("Collapse rooms sidebar");

    await toggle.trigger("click");
    await nextTick();

    expect(wrapper.get(".workspace-shell").classes()).toContain("room-sidebar-collapsed");
    expect(wrapper.get("#room-sidebar-panel").attributes("style")).toContain("display: none");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.attributes("aria-label")).toBe("Expand rooms sidebar");
    expect(localStorage.getItem("ghrah.workspace.room-sidebar.collapsed")).toBe("1");

    await toggle.trigger("click");
    await nextTick();

    expect(wrapper.get(".workspace-shell").classes()).not.toContain("room-sidebar-collapsed");
    expect(wrapper.get("#room-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(localStorage.getItem("ghrah.workspace.room-sidebar.collapsed")).toBe("0");
  });

  it("collapses the context sidebar independently from the room sidebar", async () => {
    const wrapper = mountDashboard();
    const contextToggle = wrapper.get('[aria-controls="context-sidebar-panel"]');

    await contextToggle.trigger("click");
    await nextTick();

    expect(wrapper.get(".workspace-shell").classes()).toContain("context-sidebar-collapsed");
    expect(wrapper.get(".workspace-shell").classes()).not.toContain("room-sidebar-collapsed");
    expect(wrapper.get("#context-sidebar-panel").attributes("style")).toContain("display: none");
    expect(wrapper.get("#room-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBe("1");
    expect(localStorage.getItem("ghrah.workspace.room-sidebar.collapsed")).toBeNull();
  });

  it("restores each sidebar scroll position after re-expanding", async () => {
    // happy-dom 不会在 display:none 后把 scrollTop 归零；真实浏览器会。故在面板
    // 变为不可见后手动归零，模拟该行为：没有展开后的回填，下面两条断言必失败。
    let contextPanel: HTMLElement | null = null;
    const hideScrollTop = (el: HTMLElement) => {
      if (el.style.display === "none" && el.scrollTop !== 0) el.scrollTop = 0;
    };

    const wrapper = mountDashboard();
    const roomPanel = wrapper.get(".room-sidebar-scroll").element as HTMLElement;
    contextPanel = wrapper.get("#context-sidebar-panel").element as HTMLElement;
    const contextObserver = new MutationObserver(() => hideScrollTop(contextPanel as HTMLElement));
    contextObserver.observe(contextPanel, { attributes: true, attributeFilter: ["style"] });

    roomPanel.scrollTop = 120;
    contextPanel.scrollTop = 64;

    const roomToggle = wrapper.get('[aria-controls="room-sidebar-panel"]');
    const contextToggle = wrapper.get('[aria-controls="context-sidebar-panel"]');

    await roomToggle.trigger("click");
    await contextToggle.trigger("click");
    await nextTick();
    // 左侧容器随面板一同 display:none，这里同样归零，以检验其回填路径。
    if (roomPanel.scrollTop !== 0) roomPanel.scrollTop = 0;

    expect(contextPanel.scrollTop).toBe(0);

    await roomToggle.trigger("click");
    await contextToggle.trigger("click");
    await nextTick();

    expect(roomPanel.scrollTop).toBe(120);
    expect(contextPanel.scrollTop).toBe(64);
    contextObserver.disconnect();
  });

  it("opens the context drawer from the rail without saving a collapse", async () => {
    const wrapper = mountDashboard();
    setViewportRem(64);
    await nextTick();

    const shell = wrapper.get(".workspace-shell");
    expect(shell.classes()).not.toContain("context-drawer-open");
    expect(wrapper.get("#context-sidebar-panel").attributes("style")).toContain("display: none");

    await wrapper.get('[aria-controls="context-sidebar-panel"]').trigger("click");
    await nextTick();

    expect(shell.classes()).toContain("context-drawer-open");
    expect(shell.classes()).not.toContain("context-sidebar-collapsed");
    expect(wrapper.get("#context-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBeNull();

    setViewportRem(100);
    await nextTick();

    expect(shell.classes()).not.toContain("context-drawer-open");
    expect(shell.classes()).not.toContain("context-sidebar-collapsed");
    expect(wrapper.get("#context-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBeNull();
  });

  it("restores a saved context collapse after the viewport rail goes away", async () => {
    const wrapper = mountDashboard();
    await wrapper.get('[aria-controls="context-sidebar-panel"]').trigger("click");
    await nextTick();
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBe("1");

    setViewportRem(64);
    await nextTick();
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBe("1");
    expect(wrapper.get(".workspace-shell").classes()).not.toContain("context-sidebar-collapsed");

    setViewportRem(100);
    await nextTick();
    expect(wrapper.get(".workspace-shell").classes()).toContain("context-sidebar-collapsed");
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBe("1");
  });

  it("shows the context drawer over the room column when a room is open below 1024", async () => {
    const wrapper = mountDashboard();
    setViewportRem(60);
    await nextTick();

    expect(wrapper.get("#room-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(wrapper.get("#context-sidebar-panel").attributes("style")).toContain("display: none");

    await wrapper.get("#open-p1-room").trigger("click");
    await nextTick();

    const shell = wrapper.get(".workspace-shell");
    expect(shell.classes()).toContain("context-drawer-open");
    expect(shell.classes()).not.toContain("room-sidebar-collapsed");
    expect(wrapper.get("#room-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(wrapper.get("#context-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBeNull();

    await wrapper.get('[aria-controls="context-sidebar-panel"]').trigger("click");
    await nextTick();

    expect(shell.classes()).not.toContain("context-drawer-open");
    expect(wrapper.get("#room-sidebar-panel").attributes("style") ?? "").not.toContain(
      "display: none",
    );
    expect(wrapper.get("#context-sidebar-panel").attributes("style")).toContain("display: none");
    expect(localStorage.getItem("ghrah.workspace.context-sidebar.collapsed")).toBeNull();
  });
});
