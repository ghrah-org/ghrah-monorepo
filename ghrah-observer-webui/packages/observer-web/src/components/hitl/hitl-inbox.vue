<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useHitlStore } from "@ghrah/observer-core";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import HitlRequestItem from "./hitl-request-item.vue";

const { t } = useI18n();

const hitl = useHitlStore();
const { sendHitlResponse } = useObserver();

const batchRunning = ref(false);

const batchable = computed(() => hitl.pendingRequests.length > 1);
const selectedIds = computed(() => [...hitl.selectedIds]);
const allSelected = computed(
  () => batchable.value && selectedIds.value.length === hitl.pendingRequests.length,
);

async function handleBatchApprove() {
  if (selectedIds.value.length === 0 || batchRunning.value) return;
  batchRunning.value = true;
  try {
    // 逐条并行发送（协议为单 promise 的 hitl_response）；成功项经
    // sendHitlResponse 内部回执判断移除，失败项保留待重试
    await Promise.allSettled(
      selectedIds.value.map((promiseId) => sendHitlResponse(promiseId, true)),
    );
    hitl.clearSelection();
  } finally {
    batchRunning.value = false;
  }
}
</script>

<template>
  <div class="p-3">
    <h3
      class="text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400 mb-2"
    >
      {{ t("hitl.inbox") }}
      <span
        v-if="hitl.pendingRequests.length > 0"
        class="bg-yellow-200 dark:bg-yellow-800 text-yellow-900 dark:text-yellow-100 px-1.5 py-0.5 rounded-full text-xs ml-2"
      >
        {{ hitl.pendingRequests.length }}
      </span>
    </h3>

    <div
      v-if="hitl.pendingRequests.length === 0"
      class="text-gray-400 dark:text-gray-600 text-sm italic"
    >
      {{ t("hitl.empty") }}
    </div>

    <template v-else>
      <div v-if="batchable" class="flex items-center gap-2 mb-2">
        <button
          class="btn-secondary"
          @click="allSelected ? hitl.clearSelection() : hitl.selectAll()"
        >
          {{ allSelected ? t("hitl.deselectAll") : t("hitl.selectAll") }}
        </button>
        <button
          class="btn-primary"
          :disabled="selectedIds.length === 0 || batchRunning"
          @click="handleBatchApprove"
        >
          {{ t("hitl.batchApprove") }}{{ selectedIds.length > 0 ? ` (${selectedIds.length})` : "" }}
        </button>
      </div>

      <ul class="space-y-2">
        <HitlRequestItem
          v-for="req in hitl.pendingRequests"
          :key="req.promiseId"
          :request="req"
          :selectable="batchable"
          :selected="hitl.selectedIds.has(req.promiseId)"
          @toggle-select="hitl.toggleSelected"
        />
      </ul>
    </template>
  </div>
</template>
