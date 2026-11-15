<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useProjectsStore } from "@ghrah/observer-core";
import { useI18n } from "vue-i18n";
import SidebarList from "@/components/ui/sidebar-list.vue";
import SidebarRow from "@/components/ui/sidebar-row.vue";

type ProjectViewKind = "changes" | "agents" | "assembly" | "artifacts" | "project";
const emit = defineEmits<{
  openProjectView: [view: { kind: ProjectViewKind; projectId: string }];
}>();
const projects = useProjectsStore();
const { t } = useI18n();

function open(kind: ProjectViewKind) {
  const projectId = projects.activeProjectId;
  if (projectId) emit("openProjectView", { kind, projectId });
}
</script>

<template>
  <nav class="project-fixed-nav" :aria-label="t('projectNav.label')">
    <SidebarList>
      <SidebarRow :disabled="!projects.activeProjectId" @select="open('changes')">
        <template #leading>△</template>{{ t("projectNav.changes") }}
      </SidebarRow>
      <SidebarRow :disabled="!projects.activeProjectId" @select="open('agents')">
        <template #leading>◆</template>{{ t("projectNav.agents") }}
      </SidebarRow>
      <SidebarRow :disabled="!projects.activeProjectId" @select="open('assembly')">
        <template #leading>◇</template>{{ t("projectNav.assembly") }}
      </SidebarRow>
      <SidebarRow :disabled="!projects.activeProjectId" @select="open('artifacts')">
        <template #leading>▤</template>{{ t("projectNav.artifacts") }}
      </SidebarRow>
      <SidebarRow :disabled="!projects.activeProjectId" @select="open('project')">
        <template #leading>▣</template>{{ t("projectNav.settings") }}
      </SidebarRow>
    </SidebarList>
    <p v-if="!projects.activeProjectId" class="project-fixed-nav-hint">
      {{ t("projectNav.selectProject") }}
    </p>
  </nav>
</template>
