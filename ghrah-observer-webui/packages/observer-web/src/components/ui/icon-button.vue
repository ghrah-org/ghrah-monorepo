<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { ref } from "vue";

defineOptions({ name: "UiIconButton", inheritAttrs: false });

withDefaults(
  defineProps<{
    label: string;
    variant?: "ghost" | "solid";
    size?: "xs" | "sm" | "md";
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
  }>(),
  { variant: "ghost", size: "md", type: "button" },
);

const variantClass = {
  ghost: "icon-button-ghost",
  solid: "icon-button-solid",
} as const;

const sizeClass = {
  xs: "icon-button-xs",
  sm: "icon-button-sm",
  md: "icon-button-md",
} as const;

const root = ref<HTMLButtonElement | null>(null);

defineExpose({
  focus() {
    root.value?.focus();
  },
});
</script>

<template>
  <button
    ref="root"
    v-bind="$attrs"
    class="ui-icon-button"
    :class="[variantClass[variant], sizeClass[size]]"
    :type="type"
    :disabled="disabled"
    :aria-label="label"
  >
    <slot />
  </button>
</template>
