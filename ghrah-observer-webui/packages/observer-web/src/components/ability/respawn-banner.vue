<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { SurfaceStatus } from "./surface-status";

const props = defineProps<{
  status: SurfaceStatus | null;
  pendingAdded?: readonly string[];
  pendingRemoved?: readonly string[];
  conflict: boolean;
  revision: number | null;
}>();
const { t } = useI18n();

const addedText = computed(() => (props.pendingAdded ?? []).join(", "));
const removedText = computed(() => (props.pendingRemoved ?? []).join(", "));
</script>

<template>
  <div class="respawn-banner">
    <p v-if="status === 'unobserved'" class="respawn-note" role="status">
      {{ t("assembly.state.unobserved") }}
    </p>
    <p v-else-if="status === 'unmaterialized'" class="respawn-note" role="status">
      {{ t("assembly.state.runtimeUnobserved") }}
    </p>
    <div v-else-if="status === 'restart'" class="respawn-note" role="status">
      <p>{{ t("assembly.state.restartRequired") }}</p>
      <p v-if="addedText">{{ t("assembly.state.pendingAdded", { tools: addedText }) }}</p>
      <p v-if="removedText">{{ t("assembly.state.pendingRemoved", { tools: removedText }) }}</p>
    </div>
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
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
  background: var(--color-surface-2);
  color: var(--color-text-muted);
}

.respawn-note p {
  margin: 0;
}

.respawn-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}
</style>
