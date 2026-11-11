<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  useAbilityAdminStore,
  useAbilityPackagesStore,
  useAgentsStore,
  useProjectsStore,
} from "@ghrah/observer-core";
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AssemblyDetail from "./assembly-detail.vue";
import UiBadge from "@/components/ui/badge.vue";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{ projectId: string }>();
const admin = useAbilityAdminStore();
const packages = useAbilityPackagesStore();
const agents = useAgentsStore();
const projects = useProjectsStore();
const { t } = useI18n();
const selectedId = ref("");
let shown = false;

const project = computed(() => projects.projects.get(props.projectId) ?? null);
const rows = computed(() => {
  const fromProject = (project.value?.agents ?? [])
    .map((spec) => ({
      agentId: spec.agent_id ?? "",
      name: spec.name,
      runtime: spec.runtime_status ?? "",
    }))
    .filter((row) => row.agentId);
  const source = fromProject.length
    ? fromProject
    : agents.agentsForProject(props.projectId).map((agent) => ({
        agentId: agent.agentId,
        name: agent.agentName,
        runtime: agent.runtimeStatus,
      }));
  return source.map((row) => {
    const live = agents.getAgent({ projectId: props.projectId, agentId: row.agentId });
    const runtime = live?.runtimeStatus || row.runtime;
    const record = admin.record(props.projectId, row.agentId);
    return {
      agentId: row.agentId,
      name: live?.agentName || row.name,
      runtime,
      running: runtime === "running" || runtime === "active",
      grantCount: record?.admin?.grants.length ?? 0,
      toolCount: record?.admin?.surface.effective.length ?? 0,
      needsRestart: Boolean(record?.admin?.lifecycle.requires_respawn),
      stale: Boolean(record?.stale),
    };
  });
});
const runningCount = computed(() => rows.value.filter((row) => row.running).length);
const statusKey = computed(() => {
  const status = packages.status;
  if (!status) return "assembly.statusUnavailable";
  if (status.legacy_deprecated || status.ability_source === "legacy")
    return "assembly.statusLegacy";
  if (status.features.tree) return "assembly.statusBuiltin";
  return "assembly.statusUnavailable";
});

function show() {
  if (shown) return;
  shown = true;
  packages.setAssemblyVisible(true);
}

function hide() {
  if (!shown) return;
  shown = false;
  packages.setAssemblyVisible(false);
}

watch(
  () => admin.focus,
  (focus) => {
    if (focus?.projectId === props.projectId) selectedId.value = focus.agentId;
  },
  { immediate: true },
);

watch(
  () => props.projectId,
  (projectId, previous) => {
    if (previous === undefined) return;
    const focus = admin.focus;
    selectedId.value = focus?.projectId === projectId ? focus.agentId : "";
  },
);

onMounted(show);
onActivated(show);
onDeactivated(hide);
onUnmounted(hide);
</script>

<template>
  <section class="assembly-panel">
    <header class="assembly-status">
      <div class="assembly-status-facts">
        <p>{{ t(statusKey) }}</p>
        <p>{{ t("assembly.headerAgents", { count: rows.length }) }}</p>
        <p>{{ t("assembly.headerRunning", { count: runningCount }) }}</p>
      </div>
      <p v-if="packages.status?.reason" class="assembly-note">
        {{ t("assembly.reason", { reason: packages.status.reason }) }}
      </p>
      <p v-if="packages.loading" class="assembly-state">{{ t("assembly.loading") }}</p>
      <p v-if="packages.failed" class="assembly-state assembly-state-alert" role="alert">
        {{ t("assembly.loadFailed") }}
      </p>
      <UiButton v-if="packages.failed" type="button" variant="secondary" @click="packages.load()">
        {{ t("assembly.retry") }}
      </UiButton>
    </header>
    <div class="assembly-body">
      <div class="assembly-agents">
        <h2>{{ t("assembly.agents") }}</h2>
        <p v-if="rows.length === 0" class="assembly-state assembly-state-empty">
          {{ t("assembly.emptyAgents") }}
        </p>
        <ul v-else class="assembly-agent-list">
          <li v-for="row in rows" :key="row.agentId">
            <button
              type="button"
              class="assembly-agent"
              :class="{ 'assembly-agent-selected': selectedId === row.agentId }"
              :aria-pressed="selectedId === row.agentId"
              @click="selectedId = row.agentId"
            >
              <strong>{{ row.name }}</strong>
              <span>{{ row.runtime }}</span>
              <span>{{ t("assembly.grantCount", { count: row.grantCount }) }}</span>
              <span>{{ t("assembly.toolCount", { count: row.toolCount }) }}</span>
              <UiBadge v-if="row.needsRestart">{{ t("assembly.needsRestart") }}</UiBadge>
              <UiBadge v-if="row.stale" variant="accent">{{ t("assembly.unread") }}</UiBadge>
            </button>
          </li>
        </ul>
      </div>
      <div class="assembly-main">
        <p v-if="!selectedId" class="assembly-state assembly-state-empty">
          {{ t("assembly.selectAgent") }}
        </p>
        <AssemblyDetail v-else :project-id="projectId" :agent-id="selectedId" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.assembly-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  overflow: hidden;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--font-ui-body);
}

.assembly-status,
.assembly-agents,
.assembly-main {
  min-width: 0;
  overflow-wrap: anywhere;
}

.assembly-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem 1rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.assembly-status-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1rem;
}

.assembly-status-facts p,
.assembly-note,
.assembly-agents h2,
.assembly-state {
  margin: 0;
}

.assembly-status-facts p,
.assembly-note,
.assembly-agent span {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-state,
.assembly-note {
  flex-basis: 100%;
}

.assembly-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.assembly-state-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.assembly-state-empty {
  background: var(--color-surface);
  text-align: center;
}

.assembly-body {
  display: grid;
  grid-template-columns: 16rem minmax(0, 1fr);
  min-height: 0;
  flex: 1;
}

.assembly-agents,
.assembly-main {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  overflow: auto;
}

.assembly-agents {
  border-right: 1px solid var(--color-border);
}

.assembly-agents h2 {
  color: var(--color-text);
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.assembly-agent-list {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.assembly-agent {
  display: flex;
  width: 100%;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  text-align: start;
  cursor: pointer;
}

.assembly-agent:hover {
  border-color: var(--color-border-strong);
}

.assembly-agent-selected,
.assembly-agent-selected:hover {
  border-color: var(--color-selected-line);
  background: var(--color-selected);
}

.assembly-agent strong {
  min-width: 0;
  font-weight: 600;
}

@media (max-width: 960px) {
  .assembly-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .assembly-agents {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
}
</style>
