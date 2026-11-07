<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { HitlRequest } from "@ghrah/observer-core";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();

const props = defineProps<{ request: HitlRequest; selectable?: boolean; selected?: boolean }>();

const emit = defineEmits<{ toggleSelect: [promiseId: string] }>();

const { sendHitlResponse } = useObserver();

const showRejectForm = ref(false);
const rejectReason = ref("");
const loading = ref(false);

const toolArgsSummary = computed(() => {
  const args = props.request.toolArgs;
  if (!args) return "";
  if (args.file_path) return String(args.file_path);
  if (args.command) return String(args.command);
  if (args.path) return String(args.path);
  const entries = Object.entries(args).slice(0, 2);
  return entries
    .map(([k, v]) => {
      const val = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
      return `${k}=${val.length > 30 ? val.slice(0, 30) + "\u2026" : val}`;
    })
    .join(", ");
});

const contextDesc = computed(() => {
  const ctx = props.request.context;
  if (!ctx) return "";
  return String(ctx.description ?? "");
});

const approvalReason = computed(() => props.request.reason || "");

async function handleApprove() {
  loading.value = true;
  try {
    await sendHitlResponse(props.request.promiseId, true);
  } finally {
    loading.value = false;
  }
}

async function handleRejectSubmit() {
  loading.value = true;
  try {
    await sendHitlResponse(props.request.promiseId, false, rejectReason.value || undefined);
    showRejectForm.value = false;
    rejectReason.value = "";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <li
    class="flex flex-col gap-1 p-2 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
  >
    <div class="flex items-center gap-2">
      <input
        v-if="selectable"
        type="checkbox"
        class="accent-blue-600"
        :checked="selected"
        :aria-label="t('hitl.selectForBatch')"
        @change="emit('toggleSelect', request.promiseId)"
      />
      <span class="text-amber-500 font-bold text-xs">⏳</span>
      <span class="font-medium text-sm">{{ request.abilityName }}</span>
      <span class="text-gray-400 dark:text-gray-500 text-xs font-mono truncate">{{
        toolArgsSummary
      }}</span>
      <span class="ml-auto text-xs text-gray-500 dark:text-gray-400">{{ request.agentName }}</span>
    </div>

    <div v-if="approvalReason" class="text-xs text-amber-600 dark:text-amber-400 pl-5">
      {{ t("hitl.approvalReason") }}: {{ approvalReason }}
    </div>

    <div v-if="contextDesc" class="text-xs text-gray-500 dark:text-gray-400 italic pl-5">
      {{ contextDesc }}
    </div>

    <div v-if="showRejectForm" class="flex flex-col gap-1 pl-5">
      <textarea
        v-model="rejectReason"
        rows="2"
        class="w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded text-xs dark:text-gray-100 dark:placeholder-gray-500 bg-white dark:bg-gray-800 focus:outline-none focus:ring-1 focus:ring-red-500 resize-none"
        :placeholder="t('hitl.reasonPlaceholder')"
      />
      <div class="flex gap-2">
        <UiButton variant="danger" :disabled="loading" @click="handleRejectSubmit">
          {{ t("hitl.confirmReject") }}
        </UiButton>
        <UiButton variant="secondary" @click="showRejectForm = false">
          {{ t("common.cancel") }}
        </UiButton>
      </div>
    </div>

    <div v-else class="flex gap-2 pl-5">
      <UiButton variant="primary" :disabled="loading" @click="handleApprove">
        {{ t("hitl.approve") }}
      </UiButton>
      <UiButton variant="danger" :disabled="loading" @click="showRejectForm = true">
        {{ t("hitl.reject") }}
      </UiButton>
    </div>
  </li>
</template>
