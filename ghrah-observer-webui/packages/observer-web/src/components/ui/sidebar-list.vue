<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
defineProps<{ label?: string }>();
</script>

<template>
  <ul class="sidebar-list" :aria-label="label">
    <slot />
  </ul>
</template>
