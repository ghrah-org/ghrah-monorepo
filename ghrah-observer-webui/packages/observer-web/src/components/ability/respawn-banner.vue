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
    <p v-if="requiresRespawn" class="respawn-note" role="status">{{ t("assembly.respawn") }}</p>
    <p v-if="conflict" class="respawn-alert" role="alert">
      {{ t("assembly.conflict", { revision: revision ?? 0 }) }}
    </p>
  </div>
</template>

<style scoped>
.respawn-banner {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-note);
}

.respawn-note,
.respawn-alert {
  margin: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  line-height: 1.5;
}

.respawn-note {
  background: var(--color-surface-2);
  color: var(--color-text-muted);
}

.respawn-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}
</style>
