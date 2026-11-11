<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AbilityChangeView } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

defineProps<{ changes: AbilityChangeView[]; hasMore: boolean; loading: boolean }>();
const emit = defineEmits<{ more: [] }>();
const { t } = useI18n();
</script>

<template>
  <div class="ability-changes-view">
    <p v-if="changes.length === 0 && !loading" class="ability-state">
      {{ t("assembly.changes.empty") }}
    </p>
    <ul v-else class="ability-cards">
      <li v-for="change in changes" :key="change.seq" class="ability-card">
        <p>{{ t("assembly.changes.seq", { seq: change.seq }) }}</p>
        <p>{{ t("assembly.changes.reason", { reason: change.reason }) }}</p>
        <p>{{ t("assembly.changes.revision", { revision: change.revision }) }}</p>
        <p>{{ t("assembly.changes.source", { source: change.source }) }}</p>
      </li>
    </ul>
    <UiButton
      v-if="hasMore"
      type="button"
      variant="secondary"
      :disabled="loading"
      @click="emit('more')"
    >
      {{ t("assembly.changes.more") }}
    </UiButton>
  </div>
</template>

<style scoped>
.ability-changes-view {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: var(--font-ui-note);
}

.ability-cards {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ability-card {
  min-width: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.ability-card p,
.ability-state {
  margin: 0;
  color: var(--color-text-muted);
  line-height: 1.5;
}

.ability-state {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: center;
}
</style>
