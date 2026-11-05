<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
defineOptions({ inheritAttrs: false });
defineProps<{ active?: boolean; disabled?: boolean; ariaLabel?: string }>();
defineEmits<{ select: [] }>();
</script>

<template>
  <li class="sidebar-list-item">
    <button
      v-bind="$attrs"
      type="button"
      :class="['sidebar-row', { active }]"
      :disabled="disabled"
      :aria-label="ariaLabel"
      :aria-current="active ? 'page' : undefined"
      @click="$emit('select')"
    >
      <span v-if="$slots.leading" class="sidebar-row-leading" aria-hidden="true"
        ><slot name="leading"
      /></span>
      <span class="sidebar-row-body">
        <span class="sidebar-row-primary">
          <span class="sidebar-row-label"><slot /></span>
          <span v-if="$slots.trailing" class="sidebar-row-trailing"><slot name="trailing" /></span>
        </span>
        <span v-if="$slots.secondary" class="sidebar-row-secondary"><slot name="secondary" /></span>
      </span>
    </button>
    <span v-if="$slots.actions" class="sidebar-row-actions"><slot name="actions" /></span>
  </li>
</template>
