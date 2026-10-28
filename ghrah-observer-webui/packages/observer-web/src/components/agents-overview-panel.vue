<script setup lang="ts">
import {
  type AgentTarget,
  type ChainTarget,
  useAgentsStore,
  useBranchesStore,
  useContextUsageStore,
  useProjectsStore,
  useRoomsStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";

import AgentEntityDialog from "./agent-entity-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import AgentCommunicationPanel from "./agent-communication-panel.vue";
import AgentAbilitiesDialog from "./agent-abilities-dialog.vue";

const props = defineProps<{ projectId: string }>();
const entityTarget = ref<string | null>(null);
const createEntity = ref(false);
const { listProjects, listAgents } = useObserver();
async function refreshEntities() {
  await Promise.all([listProjects(), listAgents(props.projectId)]);
}
const abilitiesTarget = ref<AgentTarget | null>(null);
watch(
  () => props.projectId,
  () => {
    abilitiesTarget.value = null;
    entityTarget.value = null;
    createEntity.value = false;
  },
);
const emit = defineEmits<{ openAgent: [target: ChainTarget] }>();
const projects = useProjectsStore();
const agents = useAgentsStore();
const sessions = useSessionsStore();
const branches = useBranchesStore();
const rooms = useRoomsStore();
const contextUsage = useContextUsageStore();
const { t } = useI18n();

const panelRoot = ref<HTMLElement | null>(null);
const { onScroll: onPanelScroll } = useTabScrollRestore(panelRoot);

const displayRuntimeStatuses = new Set(["active", "running", "stopped", "pending", "error"]);

function displayRuntimeStatus(status: string): string {
  return displayRuntimeStatuses.has(status) ? status : "unknown";
}

const project = computed(() => projects.projects.get(props.projectId) ?? null);
const rows = computed(() =>
  (project.value?.agents ?? []).map((spec) => {
    const agentId = spec.agent_id ?? "";
    const target = { projectId: props.projectId, agentId, agentName: spec.name };
    const runtime = agentId ? agents.getAgent(target) : undefined;
    const sessionId = agentId ? sessions.activeSessionId(target) : null;
    const session = sessionId ? { ...target, sessionId } : null;
    const branchId = session ? branches.activeBranchId(session) : null;
    const chainTarget =
      session && branchId ? ({ ...session, branchId } satisfies ChainTarget) : null;
    const roomNames = rooms
      .roomsForProject(props.projectId)
      .filter((room) =>
        room.members.some(
          (member) =>
            member.subject_type === "agent" &&
            (member.subject === agentId ||
              member.subject === spec.name ||
              member.subject_name === spec.name),
        ),
      )
      .map((room) => room.name);
    return {
      agentId,
      name: runtime?.agentName ?? spec.name,
      clusterId: runtime?.clusterId ?? spec.cluster_id ?? "",
      status: displayRuntimeStatus(runtime?.runtimeStatus ?? spec.runtime_status ?? "stopped"),
      runtimeError: runtime?.runtimeError ?? spec.runtime_error ?? "",
      roomNames,
      chainTarget,
      usage: agentId ? contextUsage.usageFor(target) : null,
    };
  }),
);

function openAgent(row: (typeof rows.value)[number]) {
  if (!row.chainTarget) return;
  const target = row.chainTarget;
  const agent = {
    projectId: target.projectId,
    agentId: target.agentId,
    agentName: target.agentName,
  };
  agents.selectAgent(agent);
  // 总览打开链路是查看选择（viewed），不得写运行态 active 指针（U1）。
  sessions.viewSession(agent, target.sessionId);
  branches.viewBranch(target, target.branchId);
  emit("openAgent", target);
}
</script>

<template>
  <div ref="panelRoot" class="project-panel agents-overview-panel" @scroll.passive="onPanelScroll">
    <header class="project-panel-header">
      <div>
        <span class="section-eyebrow">{{ t("agentsOverview.eyebrow") }}</span>
        <h2>{{ t("agentsOverview.title", { project: project?.name ?? projectId }) }}</h2>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="btn-primary" @click="createEntity = true">
          {{ t("agentEntity.create") }}
        </button>
        <span class="project-panel-count">{{ rows.length }}</span>
      </div>
    </header>
    <AgentCommunicationPanel :key="projectId" :project-id="projectId" />
    <div v-if="rows.length === 0" class="project-panel-empty">{{ t("agentsOverview.empty") }}</div>
    <ul v-else class="agents-overview-list">
      <li v-for="row in rows" :key="row.agentId || row.name">
        <button type="button" :disabled="!row.chainTarget" @click="openAgent(row)">
          <span :class="['agents-overview-avatar', `status-${row.status}`]">{{
            row.name.slice(0, 1).toUpperCase()
          }}</span>
          <span class="agents-overview-main"
            ><strong>{{ row.name }}</strong
            ><span>{{ row.agentId || t("agentsOverview.missingId") }}</span></span
          >
          <span class="agents-overview-rooms"
            ><span v-for="roomName in row.roomNames" :key="roomName"># {{ roomName }}</span></span
          >
          <span v-if="row.usage" :class="['agents-overview-usage', row.usage.mode]">
            <template v-if="row.usage.mode === 'ratio' && row.usage.percent != null">
              <span class="agents-overview-usage-bar">
                <span
                  class="agents-overview-usage-fill"
                  :style="{ width: `${row.usage.percent}%` }"
                />
              </span>
              <span class="agents-overview-usage-text"
                >{{ row.usage.occupiedTokens ?? 0 }} / {{ row.usage.budgetTokens }} ({{
                  row.usage.percent
                }}%)</span
              >
              <span
                v-if="row.usage.realInputTokens != null || row.usage.realOutputTokens != null"
                class="agents-overview-usage-breakdown"
                >{{
                  t("agentsOverview.usageBreakdownShort", {
                    input: row.usage.realInputTokens ?? 0,
                    output: row.usage.realOutputTokens ?? 0,
                    cacheRead: row.usage.realCacheReadTokens ?? 0,
                  })
                }}</span
              >
              <span
                v-if="row.usage.budgetSource"
                class="agents-overview-usage-tag"
                :title="t('agentsOverview.usageSource', { source: row.usage.budgetSource })"
                >{{ t(`agentsOverview.budgetSource.${row.usage.budgetSource}`) }}</span
              >
            </template>
            <template v-else>
              <span class="agents-overview-usage-text">{{
                t("agentsOverview.usageCumulative", { tokens: row.usage.cumulativeInputTokens })
              }}</span>
              <span
                v-if="row.usage.realInputTokens != null || row.usage.realOutputTokens != null"
                class="agents-overview-usage-breakdown"
                >{{
                  t("agentsOverview.usageBreakdownShort", {
                    input: row.usage.realInputTokens ?? 0,
                    output: row.usage.realOutputTokens ?? 0,
                    cacheRead: row.usage.realCacheReadTokens ?? 0,
                  })
                }}</span
              >
              <span class="agents-overview-usage-tag">{{ t("agentsOverview.usageNoWindow") }}</span>
            </template>
          </span>
          <span class="agents-overview-runtime"
            ><strong>{{ t(`agentsOverview.status.${row.status}`) }}</strong
            ><span>{{ row.clusterId }}</span></span
          >
        </button>
        <div class="flex gap-2 px-3 pb-3">
          <button
            type="button"
            class="btn-secondary"
            :disabled="!row.agentId"
            @click="entityTarget = row.agentId"
          >
            {{ t("agentEntity.manage") }}
          </button>
          <button
            type="button"
            class="btn-secondary"
            :disabled="!row.agentId"
            @click="abilitiesTarget = { projectId, agentId: row.agentId, agentName: row.name }"
          >
            {{ t("agentAbilities.manage") }}
          </button>
        </div>
        <p v-if="!row.chainTarget" class="agents-overview-chain-hint">
          {{ t("agentsOverview.noActiveChain") }}
        </p>
        <p v-if="row.runtimeError" class="agents-overview-error">{{ row.runtimeError }}</p>
      </li>
    </ul>
    <AgentEntityDialog
      v-if="entityTarget || createEntity"
      :key="entityTarget ?? 'create'"
      :project-id="projectId"
      :agent-id="entityTarget ?? undefined"
      @close="
        entityTarget = null;
        createEntity = false;
      "
      @changed="refreshEntities"
      @created="
        entityTarget = $event;
        createEntity = false;
      "
    />
    <AgentAbilitiesDialog
      v-if="abilitiesTarget"
      :target="abilitiesTarget"
      @close="abilitiesTarget = null"
    />
  </div>
</template>
