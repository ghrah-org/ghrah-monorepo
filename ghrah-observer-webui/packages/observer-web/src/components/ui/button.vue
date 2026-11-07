<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "UiButton", inheritAttrs: false });

withDefaults(
  defineProps<{
    variant: "primary" | "secondary" | "danger" | "ghost";
    size?: "md" | "sm" | "xs";
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
  }>(),
  { size: "md" },
);

const variantClass = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  danger: "btn-danger",
  ghost: "btn-ghost",
} as const;

const sizeClass = {
  md: "btn-md",
  sm: "btn-sm",
  xs: "btn-xs",
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
    :class="[`ui-button-${variant}`, variantClass[variant], sizeClass[size]]"
    :type="type"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>
