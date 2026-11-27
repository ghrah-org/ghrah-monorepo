<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import type { TranscriptEntryView } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";
import type { RoomAlign } from "@/composables/useRoomAlign";
import HumanEntry from "./human-entry.vue";
import { firstLine } from "./turn-card-model.js";

defineProps<{
  mode: "above" | "inline";
  entry: TranscriptEntryView;
  projectId: string;
  step?: number;
  align?: RoomAlign;
  currentAuthor?: string;
  targets?: readonly string[];
}>();
const { t } = useI18n();
</script>

<template>
  <HumanEntry
    v-if="mode === 'above'"
    class="insert-note"
    data-insert-mode="above"
    :entry="entry"
    :project-id="projectId"
    :align="align ?? 'left-all'"
    :current-author="currentAuthor ?? ''"
    :targets="targets"
  >
    <template #meta
      ><span>{{ t("room.insert.above") }}</span></template
    >
    <span class="insert-actions" />
  </HumanEntry>
  <p v-else class="insert-note--inline" data-insert-mode="inline" :data-injection-step="step">
    <span class="insert-anchor">{{
      t("room.insert.inline", { n: step, excerpt: firstLine(entry.content) })
    }}</span>
  </p>
</template>
<style scoped>
.insert-note {
  border-inline-start: 2px dashed var(--color-border-strong);
}
.insert-note.entry--right {
  border-inline-start: 0;
  border-inline-end: 2px dashed var(--color-border-strong);
}
.insert-note--inline {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}
</style>
