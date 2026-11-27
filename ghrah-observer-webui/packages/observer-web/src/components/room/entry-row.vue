<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { formatClock } from "./turn-card-model.js";

const props = defineProps<{ name: string; time: string | number; right?: boolean }>();
const { locale } = useI18n();
const initial = computed(() => Array.from(props.name)[0]?.toLocaleUpperCase(locale.value));
const timeLabel = computed(() => formatClock(props.time, locale.value));
</script>

<template>
  <article class="entry" :class="{ 'entry--right': right }" data-room-item>
    <span class="entry-avatar" aria-hidden="true">{{ initial }}</span>
    <div class="entry-body">
      <div class="entry-meta">
        <span class="entry-author">{{ name }}</span>
        <slot name="meta" />
        <time v-if="timeLabel">{{ timeLabel }}</time>
      </div>
      <div :class="{ bubble: right }"><slot /></div>
    </div>
  </article>
</template>

<style scoped>
.entry {
  display: flex;
  gap: var(--spacing-3);
  padding: var(--spacing-2) var(--spacing-3);
  min-width: 0;
}
.entry--right {
  flex-direction: row-reverse;
}
.entry-avatar {
  display: grid;
  flex: 0 0 2.5rem;
  block-size: 2.5rem;
  place-items: center;
  border: 1px solid var(--entry-color, var(--color-border-strong));
  border-radius: 50%;
  color: var(--entry-color, var(--color-text-muted));
}
.entry-body {
  min-width: 0;
}
.entry-meta {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.375rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  overflow-wrap: anywhere;
}
.entry-author {
  color: var(--entry-color, var(--color-text));
  font-weight: 600;
}
.entry--right .entry-meta {
  justify-content: flex-end;
}
.bubble {
  background: var(--color-surface-2);
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-md);
  padding: var(--spacing-2);
}
@media (max-width: 760px) {
  .entry-avatar {
    flex-basis: 2rem;
    block-size: 2rem;
  }
}
</style>
