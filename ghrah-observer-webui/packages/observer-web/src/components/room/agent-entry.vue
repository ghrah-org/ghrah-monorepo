<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { contentBlocksFromData, withTextContent } from "@ghrah/observer-core";
import type { TranscriptEntryView } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import EntryContent from "./entry-content.vue";
import EntryRow from "./entry-row.vue";
import { agentColorIndex } from "./turn-card-model.js";

const props = defineProps<{
  projectId: string;
  entry: TranscriptEntryView;
  agentId: string;
  agentName: string;
  anchor: boolean;
  targets?: readonly string[];
}>();
const { t } = useI18n();
const style = computed(() => ({
  "--entry-color": `var(--agent-a${agentColorIndex(props.agentId)})`,
}));
const blocks = computed(() =>
  withTextContent(contentBlocksFromData(props.entry), props.entry.content),
);
</script>

<template>
  <EntryRow
    class="entry-agent"
    :data-entry-id="entry.entry_id"
    :data-ability="entry.ability"
    :name="agentName"
    :time="entry.timestamp * 1000"
    :style="style"
  >
    <template #meta>
      <span v-if="anchor">{{ t("room.roleTag.initiator") }}</span>
      <span v-else-if="entry.ability === 'end_task'">{{ t("room.roleTag.finalReply") }}</span>
      <span v-if="targets?.length">{{ targets.map((name) => `@${name}`).join(" ") }}</span>
    </template>
    <EntryContent :project-id="projectId" :content="entry.content" :blocks="blocks" />
  </EntryRow>
</template>
