<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "UiButton", inheritAttrs: false });

defineProps<{
  variant: "primary" | "secondary" | "danger";
  type?: "button" | "submit" | "reset";
  disabled?: boolean;
}>();

const shortcut = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  danger: "btn-danger",
} as const;

const root = ref<HTMLButtonElement | null>(null);

defineExpose({
  focus() {
    root.value?.focus();
  },
  get disabled() {
    return root.value?.disabled ?? false;
  },
});
</script>

<template>
  <button
    ref="root"
    v-bind="$attrs"
    class="ui-button"
    :class="[`ui-button-${variant}`, shortcut[variant]]"
    :type="type"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>
