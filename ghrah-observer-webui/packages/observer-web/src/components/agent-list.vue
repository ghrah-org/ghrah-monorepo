<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  type AgentInfo,
  type AgentTarget,
  agentKey,
  type ContextUsageDisplay,
  sameAgent,
  useAgentsStore,
  useContextUsageStore,
  useProjectsStore,
  useRoomsStore,
} from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import AgentActionMenu from "@/components/agent-action-menu.vue";
import AgentUsageRing from "@/components/agent-usage-ring.vue";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();

const agents = useAgentsStore();
const projects = useProjectsStore();
const rooms = useRoomsStore();
const contextUsage = useContextUsageStore();
const emit = defineEmits<{
  openAgent: [target: AgentTarget];
  openSettings: [section: "agents"];
}>();

const visibleAgents = computed(() => {
  const projectId = projects.activeProjectId;
  return projectId ? agents.activeAgentsForProject(projectId) : [];
});

/** agent 名 → 所属 room 列表（RoomMember.subject_type === "agent"）。 */
const agentRooms = computed<Map<string, string[]>>(() => {
  const map = new Map<string, string[]>();
  for (const room of rooms.roomList) {
    for (const member of room.members) {
      if (member.subject_type !== "agent") continue;
      const identities = new Set<string>([member.subject]);
      if (member.subject_name) identities.add(member.subject_name);
      for (const identity of identities) {
        const list = map.get(identity) ?? [];
        list.push(room.name);
        map.set(identity, list);
      }
    }
  }
  return map;
});

function roomsOf(agent: AgentInfo): string[] {
  return agentRooms.value.get(agent.agentId) ?? agentRooms.value.get(agent.agentName) ?? [];
}

function usageOf(agent: AgentInfo): ContextUsageDisplay | null {
  return contextUsage.usageFor(agent);
}

function selectAgent(agent: AgentInfo) {
  const target: AgentTarget = {
    projectId: agent.projectId,
    agentId: agent.agentId,
    agentName: agent.agentName,
  };
  agents.selectAgent(target);
  emit("openAgent", target);
}
</script>

<template>
  <div class="agent-list sidebar-section h-full flex flex-col">
    <div class="section-heading">
      <div>
        <span class="section-eyebrow">{{ t("agents.runtime") }}</span>
        <h3>{{ t("agents.title") }}</h3>
      </div>
      <UiButton type="button" variant="primary" @click="emit('openSettings', 'agents')">
        {{ t("agents.spawn") }}
      </UiButton>
    </div>

    <ul v-if="visibleAgents.length > 0" class="flex-1 overflow-y-auto space-y-1">
      <li
        v-for="agent in visibleAgents"
        :key="agentKey(agent)"
        :class="[
          'flex items-center justify-between px-2 py-1.5 rounded cursor-pointer text-sm transition-colors',
          sameAgent(agents.selectedAgentTarget, agent)
            ? 'bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100 font-medium'
            : 'hover:bg-gray-100 dark:hover:bg-gray-800',
        ]"
        @click="selectAgent(agent)"
      >
        <span class="min-w-0 truncate">{{ agent.agentName }}</span>
        <div class="flex shrink-0 items-center gap-1">
          <span
            v-for="roomName in roomsOf(agent)"
            :key="roomName"
            :title="roomName"
            class="agent-room-badge inline-flex items-center justify-center min-w-5 h-5 px-1 rounded text-xs font-semibold bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300"
            >{{ roomName.slice(0, 1).toUpperCase() }}</span
          >
          <AgentUsageRing v-if="usageOf(agent)" :usage="usageOf(agent)!" />
          <AgentActionMenu v-if="sameAgent(agents.selectedAgentTarget, agent)" />
          <span class="w-2 h-2 rounded-full bg-green-500 flex-shrink-0" />
        </div>
      </li>
    </ul>

    <p v-else class="text-gray-400 dark:text-gray-600 text-sm italic">{{ t("agents.empty") }}</p>
  </div>
</template>
