<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import type { TranscriptEntryView } from "@ghrah/protocol";
import EntryContent from "./entry-content.vue";
defineProps<{ projectId: string; entry: TranscriptEntryView }>();
</script>
<template>
  <div class="entry-system" :data-entry-id="entry.entry_id" data-room-item>
    <EntryContent :project-id="projectId" :content="entry.content" />
  </div>
</template>
<style scoped>
.entry-system {
  color: var(--color-text-subtle);
  text-align: center;
  font-size: var(--font-ui-note);
}
</style>
