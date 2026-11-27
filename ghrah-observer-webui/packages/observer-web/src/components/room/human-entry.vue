<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import {
  contentBlocksFromData,
  withTextContent,
  type OptimisticChatEntry,
} from "@ghrah/observer-core";
import type { TranscriptEntryView } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { RoomAlign } from "@/composables/useRoomAlign";
import EntryContent from "./entry-content.vue";
import EntryRow from "./entry-row.vue";
import { humanStateText } from "./turn-card-model.js";

const props = defineProps<{
  projectId: string;
  align: RoomAlign;
  currentAuthor: string;
  entry?: TranscriptEntryView;
  outgoing?: OptimisticChatEntry;
  targets?: readonly string[];
}>();
const { t } = useI18n();
const author = computed(() => props.entry?.author ?? props.outgoing?.from ?? props.currentAuthor);
const right = computed(() => props.align === "self-right" && author.value === props.currentAuthor);
const state = computed(() =>
  props.entry
    ? humanStateText(props.entry, false, t)
    : props.outgoing?.pending
      ? t("room.humanState.pending")
      : "",
);
const content = computed(() => props.entry?.content ?? props.outgoing?.content ?? "");
const blocks = computed(() =>
  props.entry
    ? withTextContent(contentBlocksFromData(props.entry), content.value)
    : props.outgoing?.blocks,
);
const time = computed(() =>
  props.entry ? props.entry.timestamp * 1000 : (props.outgoing?.timestamp ?? ""),
);
</script>

<template>
  <EntryRow
    :name="author"
    :time="time"
    :right="right"
    :class="`entry-human--${right ? 'right' : 'left'}`"
    :data-pending="outgoing?.pending || undefined"
    :data-entry-id="entry?.entry_id"
  >
    <template #meta>
      <span class="role-tag">{{ t("room.roleTag.human") }}</span>
      <span v-if="state" class="human-state">{{ state }}</span>
      <slot name="meta" />
    </template>
    <EntryContent :project-id="projectId" :content="content" :blocks="blocks" />
    <p v-if="targets?.length" class="targets">{{ targets.map((name) => `@${name}`).join(" ") }}</p>
    <p v-if="outgoing?.error" class="send-error" role="alert">{{ outgoing.error }}</p>
    <slot />
  </EntryRow>
</template>

<style scoped>
.targets,
.send-error {
  margin: 0;
  font-size: var(--font-ui-note);
}
.targets {
  color: var(--color-text-muted);
}
.send-error {
  color: var(--color-danger);
}
</style>
