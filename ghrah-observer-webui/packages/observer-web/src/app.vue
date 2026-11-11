<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useObserver } from "@/composables/useObserver";
import Dashboard from "@/pages/dashboard.vue";
import InstanceSettings from "@/pages/instance-settings.vue";

type SettingsSection =
  "general" | "agents" | "abilities" | "plugins" | "tasks" | "archived" | "connections";
const { error, autoConnect, disconnect } = useObserver();
const settingsOpen = ref(false);
const settingsSection = ref<SettingsSection>("general");
const visibleError = ref<string | null>(null);
let errorTimer: ReturnType<typeof setTimeout> | undefined;
let settingsOpener: HTMLElement | null = null;

watch(
  error,
  (nextError) => {
    if (errorTimer) clearTimeout(errorTimer);
    visibleError.value = nextError;
    if (nextError) {
      errorTimer = setTimeout(() => {
        visibleError.value = null;
        errorTimer = undefined;
      }, 8000);
    }
  },
  { immediate: true },
);

onMounted(async () => {
  await autoConnect();
});

onUnmounted(() => {
  if (errorTimer) clearTimeout(errorTimer);
  disconnect();
});

function openSettings(section: SettingsSection = "general") {
  settingsOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  settingsSection.value = section;
  settingsOpen.value = true;
}

async function closeSettings() {
  settingsOpen.value = false;
  await nextTick();
  settingsOpener?.focus();
  settingsOpener = null;
}

function dismissError() {
  if (errorTimer) clearTimeout(errorTimer);
  errorTimer = undefined;
  visibleError.value = null;
}
</script>

<template>
  <div class="app-frame h-screen text-gray-900 dark:text-gray-100">
    <Dashboard
      class="app-workspace"
      :aria-hidden="settingsOpen ? 'true' : undefined"
      :inert="settingsOpen"
      @open-settings="openSettings"
    />
    <div v-if="visibleError" class="app-error-toast" role="alert">
      <span class="app-error-message">{{ visibleError }}</span>
      <button
        type="button"
        class="app-error-dismiss"
        :aria-label="$t('common.close')"
        @click="dismissError"
      >
        <span aria-hidden="true">×</span>
      </button>
    </div>
    <InstanceSettings
      v-if="settingsOpen"
      :initial-section="settingsSection"
      @close="closeSettings"
    />
  </div>
</template>
