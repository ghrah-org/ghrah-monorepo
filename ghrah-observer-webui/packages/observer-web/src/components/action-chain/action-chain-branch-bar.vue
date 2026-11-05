<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { BranchProjection } from "@ghrah/observer-core";
import { computed } from "vue";
import { useI18n } from "vue-i18n";

/**
 * Branch 选择栏（阶段 3 任务 3.3）：与 session-picker 同构。
 *
 * 查看选择含「全部 Branch」（__all__）；激活/归档/删除为显式动作。
 */
const props = defineProps<{
  branches: BranchProjection[];
  /** select 值：branchId | "__all__" | ""（未选择）。 */
  selectedValue: string;
  /** 运行中 Branch；其删除动作禁用。 */
  runtimeBranchId: string | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  view: [value: string];
  activate: [];
  archive: [];
  remove: [];
  create: [];
}>();

const { t } = useI18n();

/** 当前查看的单个 Branch id（"__all__"/未选择时无单对象动作）。 */
const viewedBranchId = computed(() =>
  props.selectedValue && props.selectedValue !== "__all__" ? props.selectedValue : null,
);
</script>

<template>
  <label class="text-xs text-gray-500 dark:text-gray-400">
    {{ t("actionChain.branch") }}
    <select
      class="chain-branch-select text-xs rounded-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
      :value="selectedValue"
      :disabled="disabled"
      @change="emit('view', ($event.target as HTMLSelectElement).value)"
    >
      <option value="" disabled>{{ t("actionChain.selectBranch") }}</option>
      <option value="__all__">{{ t("actionChain.allBranches") }}</option>
      <option
        v-for="option in branches"
        :key="option.target.branchId"
        :value="option.target.branchId"
      >
        {{ option.info.name || option.target.branchId
        }}<template v-if="option.target.branchId === runtimeBranchId">
          ● {{ t("actionChain.runningMark") }}</template
        >
      </option>
    </select>
  </label>
  <button
    v-if="viewedBranchId && viewedBranchId !== runtimeBranchId"
    type="button"
    class="chain-activate-branch text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('activate')"
  >
    {{ t("actionChain.activateBranch") }}
  </button>
  <button
    v-if="viewedBranchId"
    type="button"
    class="chain-archive-branch text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('archive')"
  >
    {{ t("actionChain.archiveBranch") }}
  </button>
  <button
    v-if="viewedBranchId"
    type="button"
    class="chain-delete-branch text-xs underline text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50 disabled:cursor-not-allowed"
    :disabled="disabled || viewedBranchId === runtimeBranchId"
    :title="viewedBranchId === runtimeBranchId ? t('actionChain.deleteRunningDisabled') : undefined"
    @click="emit('remove')"
  >
    {{ t("actionChain.deleteBranch") }}
  </button>
  <button
    type="button"
    class="chain-new-branch text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('create')"
  >
    {{ t("actionChain.newBranch") }}
  </button>
</template>
