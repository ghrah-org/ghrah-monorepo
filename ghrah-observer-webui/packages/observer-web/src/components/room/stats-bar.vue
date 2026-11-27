<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import {
  formatCacheRate,
  formatDuration,
  formatTokens,
  type turnStats,
} from "./turn-card-model.js";

const props = defineProps<{ stats: ReturnType<typeof turnStats>; turnIndex: number }>();
const { t } = useI18n();
const labels = computed(() => [
  t("room.card.turnIndex", { n: props.turnIndex }),
  t("room.card.steps", { n: props.stats.steps }),
  formatDuration(props.stats.durationMs),
  t("room.card.tokens", {
    in: formatTokens(props.stats.tokens.input),
    out: formatTokens(props.stats.tokens.output),
  }),
  t("room.card.cache", { rate: formatCacheRate(props.stats.cacheHitRate) }),
]);
</script>

<template>
  <footer class="tc-foot" :aria-label="labels.join(' · ')">
    <template v-for="(label, index) in labels" :key="index">
      <span v-if="index" class="tc-stat-sep" aria-hidden="true">·</span>
      <span class="tc-stat">{{ label }}</span>
    </template>
  </footer>
</template>

<style scoped>
.tc-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-variant-numeric: tabular-nums;
}
</style>
