<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ContextUsageDisplay } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = defineProps<{ usage: ContextUsageDisplay }>();
const { t } = useI18n();

const RADIUS = 5;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const dashOffset = computed(() => {
  if (props.usage.mode !== "ratio" || props.usage.percent == null) return CIRCUMFERENCE;
  return CIRCUMFERENCE * (1 - props.usage.percent / 100);
});

const title = computed(() => {
  const usage = props.usage;
  if (usage.mode === "ratio" && usage.percent != null) {
    const lines = [
      t("agentsOverview.usageTitle", {
        occupied: usage.occupiedTokens ?? 0,
        budget: usage.budgetTokens,
        percent: usage.percent,
      }),
    ];
    if (usage.realInputTokens != null || usage.realOutputTokens != null) {
      lines.push(
        t("agentsOverview.usageBreakdown", {
          input: usage.realInputTokens ?? 0,
          output: usage.realOutputTokens ?? 0,
          cacheRead: usage.realCacheReadTokens ?? 0,
        }),
      );
    }
    return lines.join("\n");
  }
  const lines = [
    t("agentsOverview.usageCumulative", {
      tokens: usage.cumulativeInputTokens,
    }),
  ];
  if (usage.realInputTokens != null || usage.realOutputTokens != null) {
    lines.push(
      t("agentsOverview.usageBreakdownLastCall", {
        input: usage.realInputTokens ?? 0,
        output: usage.realOutputTokens ?? 0,
        cacheRead: usage.realCacheReadTokens ?? 0,
      }),
    );
  }
  return lines.join("\n");
});
</script>

<template>
  <span :title="title" class="inline-flex items-center justify-center w-5 h-5 flex-shrink-0">
    <svg viewBox="0 0 14 14" class="w-4 h-4" aria-hidden="true">
      <circle
        cx="7"
        cy="7"
        :r="RADIUS"
        fill="none"
        stroke-width="2.5"
        class="stroke-gray-300 dark:stroke-gray-600"
      />
      <circle
        cx="7"
        cy="7"
        :r="RADIUS"
        fill="none"
        stroke-width="2.5"
        stroke-linecap="round"
        class="stroke-gray-700 dark:stroke-gray-200"
        :stroke-dasharray="CIRCUMFERENCE"
        :stroke-dashoffset="dashOffset"
        transform="rotate(-90 7 7)"
      />
    </svg>
  </span>
</template>
