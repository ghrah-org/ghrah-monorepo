<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
defineOptions({ name: "UiBadge" });

withDefaults(
  defineProps<{
    variant?: "neutral" | "accent";
  }>(),
  { variant: "neutral" },
);

const variantClass = {
  neutral: "ui-badge-neutral",
  accent: "ui-badge-accent",
} as const;
</script>

<template>
  <span :class="variantClass[variant]"><slot /></span>
</template>
