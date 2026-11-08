<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed } from "vue";
import UiIconButton from "./icon-button.vue";

const props = defineProps<{
  side: "left" | "right";
  collapsed: boolean;
  controls: string;
  label: string;
}>();
defineEmits<{ toggle: [] }>();

const glyph = computed(() => {
  if (props.side === "left") return props.collapsed ? "»" : "«";
  return props.collapsed ? "«" : "»";
});
</script>

<template>
  <UiIconButton
    variant="ghost"
    size="sm"
    :label="label"
    :aria-expanded="!collapsed"
    :aria-controls="controls"
    :title="label"
    @click="$emit('toggle')"
  >
    <span aria-hidden="true">{{ glyph }}</span>
  </UiIconButton>
</template>
