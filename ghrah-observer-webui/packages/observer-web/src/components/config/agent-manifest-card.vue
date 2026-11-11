<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AgentManifestInfo } from "@ghrah/observer-core";
import { useI18n } from "vue-i18n";
import UiBadge from "@/components/ui/badge.vue";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();

defineProps<{
  manifest: AgentManifestInfo;
  selected: boolean;
}>();

defineEmits<{
  select: [];
  spawn: [];
  edit: [];
  delete: [];
}>();
</script>

<template>
  <article
    class="manifest-card"
    :class="{ 'manifest-card-selected': selected }"
    @click="$emit('select')"
  >
    <div class="manifest-card-heading">
      <span class="manifest-card-title">{{ manifest.title || manifest.name }}</span>
      <UiBadge>{{ manifest.namespace }}</UiBadge>
    </div>
    <p v-if="manifest.description" class="manifest-card-description">
      {{ manifest.description }}
    </p>
    <div v-if="manifest.ability_refs.length > 0" class="manifest-card-refs">
      <UiBadge v-for="ref in manifest.ability_refs.slice(0, 5)" :key="ref" variant="accent">
        {{ ref.split(".").pop() }}
      </UiBadge>
      <span v-if="manifest.ability_refs.length > 5" class="manifest-card-more">
        +{{ manifest.ability_refs.length - 5 }}
      </span>
    </div>
    <div class="manifest-card-actions">
      <UiButton size="xs" variant="primary" @click.stop="$emit('spawn')">
        {{ t("config.agent.spawn") }}
      </UiButton>
      <UiButton size="xs" variant="secondary" @click.stop="$emit('edit')">
        {{ t("config.agent.editYaml") }}
      </UiButton>
      <UiButton size="xs" variant="danger" @click.stop="$emit('delete')">
        {{ t("config.agent.delete") }}
      </UiButton>
    </div>
  </article>
</template>

<style scoped>
.manifest-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  cursor: pointer;
}

.manifest-card:hover {
  border-color: var(--color-border-strong);
}

.manifest-card-selected,
.manifest-card-selected:hover {
  border-color: var(--color-selected-line);
  background: var(--color-selected);
}

.manifest-card-heading,
.manifest-card-refs,
.manifest-card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}

.manifest-card-heading {
  justify-content: space-between;
}

.manifest-card-title {
  min-width: 0;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.manifest-card-description,
.manifest-card-more {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.manifest-card-description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
