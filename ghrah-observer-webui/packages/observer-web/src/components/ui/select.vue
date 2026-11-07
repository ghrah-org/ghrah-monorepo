<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, useAttrs } from "vue";

defineOptions({ name: "UiSelect", inheritAttrs: false });

defineProps<{
  modelValue?: string | number;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const attrs = useAttrs();
const selectAttrs = computed(() => {
  const { class: _class, ...rest } = attrs;
  return rest;
});

function onChange(event: Event) {
  emit("update:modelValue", (event.target as HTMLSelectElement).value);
}
</script>

<template>
  <span class="field-select-wrap" :class="attrs.class">
    <select v-bind="selectAttrs" class="field-select" :value="modelValue ?? ''" @change="onChange">
      <slot />
    </select>
    <svg class="field-chevron" viewBox="0 0 10 10" aria-hidden="true">
      <path
        d="M2 3.5 5 6.5 8 3.5"
        fill="none"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </span>
</template>
