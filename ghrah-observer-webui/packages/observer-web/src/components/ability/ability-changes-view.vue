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
    <p v-if="changes.length === 0 && !loading">{{ t("assembly.changes.empty") }}</p>
    <ul v-else>
      <li v-for="change in changes" :key="change.seq">
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
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}
</style>
