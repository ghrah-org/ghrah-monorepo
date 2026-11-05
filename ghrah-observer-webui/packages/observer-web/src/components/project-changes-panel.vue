<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useChangesStore, useProjectsStore } from "@ghrah/observer-core";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useTabScrollRestore } from "@/composables/useTabScrollRestore";

const props = defineProps<{ projectId: string }>();
const { t, locale } = useI18n();
const changes = useChangesStore();
const projects = useProjectsStore();
const expandedKey = ref<string | null>(null);
const panelRoot = ref<HTMLElement | null>(null);
const { onScroll: onPanelScroll } = useTabScrollRestore(panelRoot);
const project = computed(() => projects.projects.get(props.projectId) ?? null);
const projectChanges = computed(() =>
  changes.changes.filter((change) => change.projectId === props.projectId),
);

function changeKey(change: (typeof projectChanges.value)[number]): string {
  return [
    change.projectId,
    change.agentId,
    change.sessionId,
    change.branchId,
    change.nodeId,
    change.abilityName,
    change.filePath ?? "",
  ].join("#");
}

function toggle(key: string) {
  expandedKey.value = expandedKey.value === key ? null : key;
}

function timeStr(timestamp: string): string {
  if (!timestamp) return "";
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString(locale.value);
}
</script>

<template>
  <div ref="panelRoot" class="project-panel project-changes-panel" @scroll.passive="onPanelScroll">
    <header class="project-panel-header">
      <div>
        <span class="section-eyebrow">{{ t("changes.eyebrow") }}</span>
        <h2>{{ t("changes.projectTitle", { project: project?.name ?? projectId }) }}</h2>
      </div>
      <span class="project-panel-count">{{ projectChanges.length }}</span>
    </header>
    <div v-if="projectChanges.length === 0" class="project-panel-empty">
      {{ t("changes.empty") }}
    </div>
    <div v-else class="project-changes-list">
      <article
        v-for="change in projectChanges"
        :key="changeKey(change)"
        :class="['project-change-row', { failed: !change.success }]"
      >
        <button type="button" @click="toggle(changeKey(change))">
          <span class="project-change-status" aria-hidden="true">{{
            change.success ? "✓" : "×"
          }}</span>
          <span class="project-change-path">{{ change.filePath ?? t("changes.unknownPath") }}</span>
          <span class="project-change-agent">@{{ change.agentName }}</span>
          <span class="project-change-scope">{{ change.sessionId }} / {{ change.branchId }}</span>
          <span class="project-change-ability">{{ change.abilityName }}</span>
          <time>{{ timeStr(change.timestamp) }}</time>
        </button>
        <pre v-if="expandedKey === changeKey(change)">{{
          JSON.stringify(change.result ?? { error: change.error, outcome: change.outcome }, null, 2)
        }}</pre>
      </article>
    </div>
  </div>
</template>
