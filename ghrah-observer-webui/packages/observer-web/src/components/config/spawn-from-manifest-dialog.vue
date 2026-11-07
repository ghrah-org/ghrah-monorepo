<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AgentManifestInfo } from "@ghrah/observer-core";
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

const props = defineProps<{
  manifest: AgentManifestInfo;
}>();

const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean] }>();

const { spawnAgent, error } = useObserver();
const { t } = useI18n();

const runtimeName = ref(props.manifest.name);
const loading = ref(false);
const errorMsg = ref<string | null>(null);
const dirty = ref(false);
const confirmDiscardOpen = ref(false);

watch(runtimeName, (value) => {
  const nextDirty = value !== props.manifest.name;
  if (dirty.value === nextDirty) return;
  dirty.value = nextDirty;
  emit("dirtyChange", nextDirty);
});

function requestClose() {
  if (dirty.value) confirmDiscardOpen.value = true;
  else finishClose();
}

function finishClose() {
  dirty.value = false;
  emit("dirtyChange", false);
  emit("close");
}

function discardAndClose() {
  confirmDiscardOpen.value = false;
  finishClose();
}

async function handleSubmit() {
  if (!runtimeName.value.trim()) return;
  loading.value = true;
  errorMsg.value = null;

  const result = await spawnAgent(
    { name: runtimeName.value.trim(), description: "", system_prompt: "", max_iterations: 10 },
    props.manifest.full_name,
  );
  loading.value = false;

  if (result && !result.success) {
    errorMsg.value = result.error ?? t("config.spawn.failed");
  } else if (result) {
    finishClose();
  } else {
    errorMsg.value = error.value ?? t("config.spawn.notConnected");
  }
}
</script>

<template>
  <BaseModal :title="t('config.spawn.title')" size="sm" @request-close="requestClose">
    <template #subtitle>
      <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
        {{ t("config.spawn.fromManifest") }} <span class="font-mono">{{ manifest.full_name }}</span>
      </p>
    </template>

    <form class="space-y-4" @submit.prevent="handleSubmit">
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{{
          t("config.spawn.runtimeName")
        }}</label>
        <UiTextField
          v-model="runtimeName"
          data-autofocus
          type="text"
          required
          class="w-full px-3 py-2 text-sm"
          :placeholder="t('config.spawn.runtimeNamePlaceholder')"
        />
      </div>

      <div v-if="errorMsg" class="text-red-600 dark:text-red-400 text-sm">{{ errorMsg }}</div>

      <div class="flex justify-end gap-2">
        <UiButton type="button" variant="secondary" :disabled="loading" @click="requestClose">
          {{ t("common.cancel") }}
        </UiButton>
        <UiButton type="submit" variant="primary" :disabled="loading || !runtimeName.trim()">
          {{ loading ? t("config.spawn.spawning") : t("config.spawn.spawn") }}
        </UiButton>
      </div>
    </form>
  </BaseModal>
  <ConfirmDialog
    v-if="confirmDiscardOpen"
    :title="t('config.unsavedTitle')"
    :message="t('config.unsavedConfirm')"
    :confirm-label="t('config.discardChanges')"
    danger
    @cancel="confirmDiscardOpen = false"
    @confirm="discardAndClose"
  />
</template>
