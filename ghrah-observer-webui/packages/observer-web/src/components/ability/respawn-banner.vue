<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useI18n } from "vue-i18n";

defineProps<{ requiresRespawn: boolean; conflict: boolean; revision: number | null }>();
const { t } = useI18n();
</script>

<template>
  <div class="respawn-banner">
    <p v-if="requiresRespawn" role="status">{{ t("assembly.respawn") }}</p>
    <p v-if="conflict" role="alert">{{ t("assembly.conflict", { revision: revision ?? 0 }) }}</p>
  </div>
</template>

<style scoped>
.respawn-banner {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}
</style>
