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
  <div
    :class="[
      'p-3 rounded border cursor-pointer transition-colors',
      selected
        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900 dark:border-blue-400'
        : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600',
    ]"
    @click="$emit('select')"
  >
    <div class="flex items-center justify-between mb-1">
      <span class="font-medium text-sm truncate">{{ manifest.title || manifest.name }}</span>
      <UiBadge>{{ manifest.namespace }}</UiBadge>
    </div>
    <p v-if="manifest.description" class="text-xs text-gray-500 dark:text-gray-400 truncate mb-2">
      {{ manifest.description }}
    </p>
    <div v-if="manifest.ability_refs.length > 0" class="flex flex-wrap gap-1">
      <UiBadge v-for="ref in manifest.ability_refs.slice(0, 5)" :key="ref" variant="accent">
        {{ ref.split(".").pop() }}
      </UiBadge>
      <span v-if="manifest.ability_refs.length > 5" class="text-xs text-gray-400">
        +{{ manifest.ability_refs.length - 5 }}
      </span>
    </div>
    <div class="flex gap-1 mt-2">
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
  </div>
</template>
