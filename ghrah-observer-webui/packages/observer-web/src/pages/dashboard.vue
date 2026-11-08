<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  type AgentTarget,
  agentKey,
  branchKey,
  type ChainTarget,
  sameAgent,
  sessionKey,
  useAbilityAdminStore,
  useAgentsStore,
  useBranchesStore,
  useProjectsStore,
  useRoomsStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AssemblyPanel from "@/components/ability/assembly-panel.vue";
import ActionChainPanel from "@/components/action-chain/action-chain-panel.vue";
import AgentList from "@/components/agent-list.vue";
import AgentsOverviewPanel from "@/components/agents-overview-panel.vue";
import ChatPanel from "@/components/chat/chat-panel.vue";
import HitlBar from "@/components/hitl/hitl-bar.vue";
import InstanceProfileControl from "@/components/instance-profile-control.vue";
import ProjectFixedNav from "@/components/nav/project-fixed-nav.vue";
import ProjectSelector from "@/components/nav/project-selector.vue";
import RoomSelector from "@/components/nav/room-selector.vue";
import ProjectChangesPanel from "@/components/project-changes-panel.vue";
import ProjectSettingsPanel from "@/components/project-settings-panel.vue";
import UiIconButton from "@/components/ui/icon-button.vue";
import SidebarToggle from "@/components/ui/sidebar-toggle.vue";
import { useObserver } from "@/composables/useObserver";
import { useSidebarLayout } from "@/composables/useSidebarLayout";
import { useWorkspaceColumns } from "@/composables/useWorkspaceColumns";

type RoomTab = {
  id: string;
  kind: "room";
  target: { projectId: string; roomId: string };
  fallbackLabel: string;
};
type AgentTab = {
  id: string;
  kind: "agent";
  target: { agent: AgentTarget; chain: ChainTarget | null };
  fallbackLabel: string;
};
type ProjectTab = {
  id: string;
  kind: "changes" | "project" | "agents" | "assembly";
  target: { projectId: string };
  fallbackLabel: string;
};
type WorkspaceTab = RoomTab | AgentTab | ProjectTab;
type ProjectViewKind = ProjectTab["kind"];

const emit = defineEmits<{
  openSettings: [section: "general" | "agents" | "abilities"];
}>();
const abilityAdmin = useAbilityAdminStore();
const rooms = useRoomsStore();
const agents = useAgentsStore();
const projects = useProjectsStore();
const sessions = useSessionsStore();
const branches = useBranchesStore();
const { t } = useI18n();
const { switchRoom, switchProject, selectAgent } = useObserver();
const { roomSidebarCollapsed, contextSidebarCollapsed, toggleRoomSidebar, toggleContextSidebar } =
  useSidebarLayout();
const columnMode = useWorkspaceColumns();
const contextDrawerOpen = ref(false);
const tabs = ref<WorkspaceTab[]>([]);
const activeTabId = ref<string | null>(null);
const roomScroll = ref<HTMLElement | null>(null);
const contextScroll = ref<HTMLElement | null>(null);
const roomScrollTop = ref(0);
const contextScrollTop = ref(0);

const contextUsesDrawer = computed(
  () => columnMode.value === "rail" || columnMode.value === "exclusive",
);
const contextVisuallyCollapsed = computed(() =>
  contextUsesDrawer.value ? !contextDrawerOpen.value : contextSidebarCollapsed.value,
);
const roomToggleLabel = computed(() =>
  roomSidebarCollapsed.value ? t("dashboard.expandRooms") : t("dashboard.collapseRooms"),
);
const contextToggleLabel = computed(() =>
  contextVisuallyCollapsed.value ? t("dashboard.expandContext") : t("dashboard.collapseContext"),
);

async function onToggleRoomSidebar() {
  if (!roomSidebarCollapsed.value) roomScrollTop.value = roomScroll.value?.scrollTop ?? 0;
  toggleRoomSidebar();
  await nextTick();
  if (!roomSidebarCollapsed.value && roomScroll.value) {
    roomScroll.value.scrollTop = roomScrollTop.value;
  }
}

async function onToggleContextSidebar() {
  if (contextUsesDrawer.value) {
    contextDrawerOpen.value = !contextDrawerOpen.value;
    return;
  }
  if (!contextSidebarCollapsed.value) {
    contextScrollTop.value = contextScroll.value?.scrollTop ?? 0;
  }
  toggleContextSidebar();
  await nextTick();
  if (!contextSidebarCollapsed.value && contextScroll.value) {
    contextScroll.value.scrollTop = contextScrollTop.value;
  }
}

const activeTab = computed(() => tabs.value.find((tab) => tab.id === activeTabId.value) ?? null);

watch(
  () => [columnMode.value, activeTab.value?.id ?? "", activeTab.value?.kind ?? ""] as const,
  ([mode, id, kind], previous) => {
    if (mode === "wide" || mode === "mid") {
      contextDrawerOpen.value = false;
      return;
    }
    if (mode === "rail") {
      if (!previous || previous[0] !== "rail") contextDrawerOpen.value = false;
      return;
    }
    const entered = !previous || previous[0] !== "exclusive";
    const selectionChanged = !previous || previous[1] !== id || previous[2] !== kind;
    if (entered || selectionChanged) {
      contextDrawerOpen.value = kind === "room" || kind === "agent";
    }
  },
);

function projectIdOf(tab: WorkspaceTab): string {
  return tab.kind === "agent" ? tab.target.agent.projectId : tab.target.projectId;
}

function tabLabel(tab: WorkspaceTab): string {
  if (tab.kind === "room") {
    const room = rooms.activeRooms.get(tab.target.roomId);
    return room?.project_id === tab.target.projectId ? room.name : tab.fallbackLabel;
  }
  if (tab.kind === "agent") {
    return agents.getAgent(tab.target.agent)?.agentName ?? tab.fallbackLabel;
  }
  const projectName = projects.projects.get(tab.target.projectId)?.name ?? tab.fallbackLabel;
  if (tab.kind === "changes") return t("dashboard.tabChanges", { project: projectName });
  if (tab.kind === "agents") return t("dashboard.tabAgents", { project: projectName });
  if (tab.kind === "assembly") return t("dashboard.tabAssembly", { project: projectName });
  return t("dashboard.tabProject", { project: projectName });
}

function tabIcon(kind: WorkspaceTab["kind"]): string {
  return { room: "#", agent: "◎", changes: "△", agents: "◆", assembly: "◇", project: "▣" }[kind];
}

function openAssembly(target: { projectId: string; agentId: string }) {
  abilityAdmin.focusAgent(target.projectId, target.agentId);
  openProjectView({ kind: "assembly", projectId: target.projectId });
}

function currentChain(agent: AgentTarget): ChainTarget | null {
  const sessionId = sessions.activeSessionId(agent);
  if (!sessionId) return null;
  const session = { ...agent, sessionId };
  const branchId = branches.activeBranchId(session);
  return branchId ? { ...session, branchId } : null;
}

function openRoom(room: { room_id: string; project_id: string; name: string }) {
  const id = `room:${room.project_id}:${room.room_id}`;
  let tab = tabs.value.find((item) => item.id === id);
  if (!tab) {
    tab = {
      id,
      kind: "room",
      target: { projectId: room.project_id, roomId: room.room_id },
      fallbackLabel: room.name,
    };
    tabs.value.push(tab);
  }
  void activateTab(tab);
}

function openAgent(target: AgentTarget | ChainTarget) {
  const resolved = agents.getAgent(target) ?? target;
  const agent: AgentTarget = {
    projectId: resolved.projectId,
    agentId: resolved.agentId,
    agentName: resolved.agentName,
  };
  const chain: ChainTarget | null =
    "sessionId" in target
      ? { ...agent, sessionId: target.sessionId, branchId: target.branchId }
      : currentChain(agent);
  const id = chain ? `chain:${branchKey(chain)}` : `agent:${agentKey(agent)}`;
  let tab = tabs.value.find((item) => item.id === id);
  if (!tab && chain) {
    const contextTab = tabs.value.find(
      (item) =>
        item.kind === "agent" && item.target.chain === null && sameAgent(item.target.agent, agent),
    );
    if (contextTab) {
      const wasActive = activeTabId.value === contextTab.id;
      contextTab.id = id;
      contextTab.target = { agent, chain };
      contextTab.fallbackLabel = agent.agentName;
      if (wasActive) activeTabId.value = id;
      tab = contextTab;
    }
  }
  if (!tab) {
    tab = { id, kind: "agent", target: { agent, chain }, fallbackLabel: agent.agentName };
    tabs.value.push(tab);
  }
  void activateTab(tab);
}

function openProjectView(view: { kind: ProjectViewKind; projectId: string }) {
  const id = `${view.kind}:${view.projectId}`;
  let tab = tabs.value.find((item) => item.id === id);
  if (!tab) {
    tab = {
      id,
      kind: view.kind,
      target: { projectId: view.projectId },
      fallbackLabel: projects.projects.get(view.projectId)?.name ?? view.projectId,
    };
    tabs.value.push(tab);
  }
  void activateTab(tab);
}

async function activateTab(tab: WorkspaceTab) {
  const projectId = projectIdOf(tab);
  if (projects.activeProjectId !== projectId) await switchProject(projectId);
  activeTabId.value = tab.id;
  if (tab.kind === "room") {
    if (rooms.activeRoomId !== tab.target.roomId) await switchRoom(tab.target.roomId);
    return;
  }
  if (tab.kind === "agent") {
    const resolved = agents.getAgent(tab.target.agent) ?? tab.target.agent;
    const currentAgent: AgentTarget = {
      projectId: resolved.projectId,
      agentId: resolved.agentId,
      agentName: resolved.agentName,
    };
    if (!sameAgent(agents.selectedAgentTarget, currentAgent)) selectAgent(currentAgent);
    if (tab.target.chain) {
      // 标签页定位是查看选择（viewed），不得写运行态 active 指针（U1）。
      sessions.viewSession(currentAgent, tab.target.chain.sessionId);
      branches.viewBranch(tab.target.chain, tab.target.chain.branchId);
    }
  }
}

async function closeTab(tab: WorkspaceTab) {
  const index = tabs.value.findIndex((item) => item.id === tab.id);
  if (index < 0) return;
  const wasActive = activeTabId.value === tab.id;
  tabs.value.splice(index, 1);
  if (!wasActive) return;
  if (tab.kind === "room" && rooms.activeRoomId === tab.target.roomId) await switchRoom(null);
  if (tab.kind === "agent" && sameAgent(agents.selectedAgentTarget, tab.target.agent)) {
    selectAgent(null);
  }
  const next = tabs.value[Math.min(index, tabs.value.length - 1)];
  activeTabId.value = next?.id ?? null;
  if (next) await activateTab(next);
}

async function removeTabs(isInvalid: (tab: WorkspaceTab) => boolean) {
  const previous = tabs.value;
  const activeIndex = previous.findIndex((tab) => tab.id === activeTabId.value);
  const activeInvalid = activeIndex >= 0 && isInvalid(previous[activeIndex]);
  const remaining = previous.filter((tab) => !isInvalid(tab));
  if (remaining.length === previous.length) return;
  tabs.value = remaining;
  if (!activeInvalid) return;
  const next =
    previous.slice(activeIndex + 1).find((tab) => !isInvalid(tab)) ??
    previous
      .slice(0, activeIndex)
      .reverse()
      .find((tab) => !isInvalid(tab));
  activeTabId.value = next?.id ?? null;
  if (next) await activateTab(next);
}

function invalidateRoom(target: { projectId: string; roomId: string }) {
  return removeTabs(
    (tab) =>
      tab.kind === "room" &&
      tab.target.projectId === target.projectId &&
      tab.target.roomId === target.roomId,
  );
}

watch(
  () => ({
    roomKeys: rooms.roomList.map((room) => `${room.project_id}:${room.room_id}`),
    projectIds: projects.projectList.map((project) => project.project_id),
    agentKeys: [...agents.agents.keys()],
    sessionKeys: [...sessions.sessions.keys()],
    branchKeys: [...branches.branches.keys()],
  }),
  ({ roomKeys, projectIds, agentKeys, sessionKeys, branchKeys }) => {
    const validRooms = new Set(roomKeys);
    const validProjects = new Set(projectIds);
    const validAgents = new Set(agentKeys);
    const validSessions = new Set(sessionKeys);
    const validBranches = new Set(branchKeys);
    void removeTabs((tab) => {
      if (!validProjects.has(projectIdOf(tab))) return true;
      if (tab.kind === "room") {
        return !validRooms.has(`${tab.target.projectId}:${tab.target.roomId}`);
      }
      if (tab.kind !== "agent") return false;
      if (!validAgents.has(agentKey(tab.target.agent))) return true;
      if (!tab.target.chain) return false;
      return (
        !validSessions.has(sessionKey(tab.target.chain)) ||
        !validBranches.has(branchKey(tab.target.chain))
      );
    });
  },
);
</script>

<template>
  <div
    class="workspace-shell"
    :class="{
      'room-sidebar-collapsed': roomSidebarCollapsed,
      'context-sidebar-collapsed': !contextUsesDrawer && contextSidebarCollapsed,
      'context-drawer-open': contextUsesDrawer && contextDrawerOpen,
    }"
  >
    <aside class="project-rail workspace-column" :aria-label="t('dashboard.projects')">
      <ProjectSelector />
    </aside>
    <aside class="room-sidebar workspace-column" :aria-label="t('dashboard.rooms')">
      <div class="sidebar-head">
        <SidebarToggle
          side="left"
          :collapsed="roomSidebarCollapsed"
          controls="room-sidebar-panel"
          :label="roomToggleLabel"
          @toggle="onToggleRoomSidebar"
        />
      </div>
      <div v-show="!roomSidebarCollapsed" id="room-sidebar-panel" class="room-sidebar-panel">
        <div ref="roomScroll" class="room-sidebar-scroll">
          <RoomSelector @open-room="openRoom" @room-invalidated="invalidateRoom" />
        </div>
        <ProjectFixedNav @open-project-view="openProjectView" />
        <InstanceProfileControl @open-settings="emit('openSettings', 'general')" />
      </div>
    </aside>
    <section class="workspace-main" :aria-label="t('dashboard.workspaceTabs')">
      <div class="workspace-tabs" role="tablist" :aria-label="t('dashboard.openViews')">
        <div class="tabs-scroll">
          <div
            v-for="tab in tabs"
            :key="tab.id"
            :class="['workspace-tab', { active: activeTabId === tab.id }]"
            role="tab"
            tabindex="0"
            :aria-selected="activeTabId === tab.id"
            @click="activateTab(tab)"
            @keydown.enter.prevent="activateTab(tab)"
            @keydown.space.prevent="activateTab(tab)"
          >
            <span :class="['tab-kind', tab.kind]">{{ tabIcon(tab.kind) }}</span>
            <span class="tab-label">{{ tabLabel(tab) }}</span>
            <UiIconButton
              variant="ghost"
              size="xs"
              :label="t('dashboard.closeTab', { label: tabLabel(tab) })"
              @click.stop="void closeTab(tab)"
              @keydown.enter.stop
              @keydown.space.stop
            >
              ×
            </UiIconButton>
          </div>
        </div>
        <span class="tab-count">{{ t("dashboard.openCount", { count: tabs.length }) }}</span>
      </div>
      <div class="workspace-content">
        <KeepAlive>
          <ChatPanel
            v-if="activeTab?.kind === 'room'"
            :key="activeTab.id"
            :project-id="activeTab.target.projectId"
            :room-id="activeTab.target.roomId"
            @room-invalidated="invalidateRoom"
          />
          <ActionChainPanel
            v-else-if="activeTab?.kind === 'agent'"
            :key="activeTab.id"
            :agent="activeTab.target.agent"
          />
          <ProjectChangesPanel
            v-else-if="activeTab?.kind === 'changes'"
            :key="activeTab.id"
            :project-id="activeTab.target.projectId"
          />
          <ProjectSettingsPanel
            v-else-if="activeTab?.kind === 'project'"
            :key="activeTab.id"
            :project-id="activeTab.target.projectId"
          />
          <AgentsOverviewPanel
            v-else-if="activeTab?.kind === 'agents'"
            :key="activeTab.id"
            :project-id="activeTab.target.projectId"
            @open-agent="openAgent"
            @open-assembly="openAssembly"
          />
          <AssemblyPanel
            v-else-if="activeTab?.kind === 'assembly'"
            :key="activeTab.id"
            :project-id="activeTab.target.projectId"
          />
        </KeepAlive>
        <div v-if="!activeTab" class="workspace-empty">
          <div class="empty-mark">⌘</div>
          <h2>{{ t("dashboard.readyTitle") }}</h2>
          <p>{{ t("dashboard.readyBody") }}</p>
          <div class="empty-shortcuts">
            <span class="shortcut-pill"><kbd>#</kbd> {{ t("dashboard.roomConversation") }}</span
            ><span class="shortcut-pill"><kbd>◎</kbd> {{ t("dashboard.agentActionChain") }}</span>
          </div>
        </div>
      </div>
      <HitlBar />
    </section>
    <aside class="right-sidebar workspace-column" :aria-label="t('dashboard.contextSidebar')">
      <div class="sidebar-head">
        <SidebarToggle
          side="right"
          :collapsed="contextVisuallyCollapsed"
          controls="context-sidebar-panel"
          :label="contextToggleLabel"
          @toggle="onToggleContextSidebar"
        />
      </div>
      <div
        v-show="!contextVisuallyCollapsed"
        id="context-sidebar-panel"
        ref="contextScroll"
        class="right-sidebar-panel"
      >
        <section class="agent-region" :aria-label="t('dashboard.agents')">
          <AgentList @open-agent="openAgent" @open-settings="emit('openSettings', $event)" />
        </section>
      </div>
    </aside>
  </div>
</template>
