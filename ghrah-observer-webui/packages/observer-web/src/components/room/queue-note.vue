<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import type { TranscriptEntryView } from "@ghrah/protocol";
import type { RoomAlign } from "@/composables/useRoomAlign";
import HumanEntry from "./human-entry.vue";
defineProps<{
  entry: TranscriptEntryView;
  projectId: string;
  align: RoomAlign;
  currentAuthor: string;
  targets: readonly string[];
}>();
</script>
<template>
  <HumanEntry
    class="queue-note"
    data-insert-mode="queue"
    :entry="entry"
    :project-id="projectId"
    :align="align"
    :current-author="currentAuthor"
    :targets="targets"
  >
    <span class="insert-actions" />
  </HumanEntry>
</template>
