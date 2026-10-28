<script setup lang="ts">
import type { AgentManifestInfo } from "@ghrah/observer-core";
import { useI18n } from "vue-i18n";

const { t } = useI18n();

defineProps<{
  manifest: AgentManifestInfo | null;
}>();

defineEmits<{
  spawn: [];
  edit: [];
  delete: [];
}>();

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}
</script>

<template>
  <div v-if="!manifest" class="text-gray-400 dark:text-gray-600 text-sm italic py-8 text-center">
    {{ t("config.agent.selectManifest") }}
  </div>
  <div
    v-else
    class="border border-gray-200 dark:border-gray-700 rounded p-4 bg-white dark:bg-gray-900 space-y-3"
  >
    <div class="flex items-center justify-between">
      <h3 class="font-semibold">{{ manifest.title || manifest.name }}</h3>
      <span class="badge bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 text-xs">{{
        manifest.namespace
      }}</span>
    </div>

    <div v-if="manifest.description" class="text-sm text-gray-600 dark:text-gray-400">
      {{ manifest.description }}
    </div>

    <div class="grid grid-cols-2 gap-2 text-sm">
      <div>
        <span class="text-gray-500 dark:text-gray-400">{{ t("config.agent.name") }}</span>
        <span class="ml-1 font-mono">{{ manifest.name }}</span>
      </div>
      <div>
        <span class="text-gray-500 dark:text-gray-400">{{ t("config.agent.config") }}</span>
        <span class="ml-1 font-mono">{{ manifest.endpoint_name || "—" }}</span>
      </div>
      <div>
        <span class="text-gray-500 dark:text-gray-400">{{ t("config.agent.temperature") }}</span>
        <span class="ml-1">—</span>
      </div>
      <div>
        <span class="text-gray-500 dark:text-gray-400">{{ t("config.agent.maxIterations") }}</span>
        <span class="ml-1">{{ manifest.max_iterations }}</span>
      </div>
    </div>

    <div v-if="manifest.tags.length > 0" class="flex flex-wrap gap-1">
      <span
        v-for="tag in manifest.tags"
        :key="tag"
        class="badge bg-gray-100 dark:bg-gray-700 text-xs"
        >{{ tag }}</span
      >
    </div>

    <div v-if="manifest.system_prompt" class="space-y-1">
      <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">
        {{ t("config.agent.systemPrompt") }}
      </div>
      <pre
        class="text-xs bg-gray-50 dark:bg-gray-800 p-2 rounded overflow-x-auto whitespace-pre-wrap max-h-32 overflow-y-auto"
        >{{ truncate(manifest.system_prompt, 500) }}</pre>
    </div>

    <div v-if="manifest.ability_refs.length > 0" class="space-y-1">
      <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">
        {{ t("config.agent.abilities", { count: manifest.ability_refs.length }) }}
      </div>
      <ul class="text-xs space-y-0.5">
        <li
          v-for="ref in manifest.ability_refs"
          :key="ref"
          class="font-mono text-gray-700 dark:text-gray-300"
        >
          {{ ref }}
        </li>
      </ul>
    </div>

    <div class="flex gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
      <button class="btn-primary text-xs" @click="$emit('spawn')">
        {{ t("config.spawn.title") }}
      </button>
      <button class="btn-secondary text-xs" @click="$emit('edit')">
        {{ t("config.agent.editYaml") }}
      </button>
      <button class="btn-danger text-xs" @click="$emit('delete')">
        {{ t("config.agent.delete") }}
      </button>
    </div>
  </div>
</template>
