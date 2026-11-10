<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { ref, useAttrs } from "vue";

defineOptions({ name: "UiTextField", inheritAttrs: false });

const props = defineProps<{
  modelValue?: string | number;
  modelModifiers?: { number?: boolean };
  multiline?: boolean;
  variant?: "default" | "chat";
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string | number];
}>();

const attrs = useAttrs();
const root = ref<HTMLInputElement | HTMLTextAreaElement | null>(null);

function looseToNumber(value: string): string | number {
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) ? value : parsed;
}

function onInput(event: Event) {
  const value = (event.target as HTMLInputElement | HTMLTextAreaElement).value;
  const numeric = props.modelModifiers?.number === true || attrs.type === "number";
  emit("update:modelValue", numeric ? looseToNumber(value) : value);
}

defineExpose({
  get element() {
    return root.value;
  },
});
</script>

<template>
  <textarea
    v-if="multiline"
    ref="root"
    v-bind="$attrs"
    :class="variant === 'chat' ? 'field-multiline-chat' : 'field-multiline'"
    :value="modelValue ?? ''"
    @input="onInput"
  />
  <input
    v-else
    ref="root"
    v-bind="$attrs"
    class="field-input"
    :value="modelValue ?? ''"
    @input="onInput"
  />
</template>
