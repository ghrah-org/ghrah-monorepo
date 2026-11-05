<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { SessionProjection } from "@ghrah/observer-core";
import { useI18n } from "vue-i18n";

/**
 * Session 选择器（阶段 3 任务 3.3）：查看选择 + 显式管理动作。
 *
 * 普通点击仅查看（D1）；激活/归档/删除均显式确认并 emit 命令事件，
 * 由面板统一走 useObserver + failureOf 回执。
 */
defineProps<{
  sessions: SessionProjection[];
  /** 当前查看的 Session（viewed，回退 runtime）。 */
  viewedSessionId: string | null;
  /** 运行中 Session；其删除动作禁用。 */
  runtimeSessionId: string | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  view: [sessionId: string];
  activate: [];
  archive: [];
  remove: [];
  create: [];
}>();

const { t } = useI18n();
</script>

<template>
  <label class="text-xs text-gray-500 dark:text-gray-400">
    {{ t("actionChain.session") }}
    <select
      class="chain-session-select text-xs rounded-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200"
      :value="viewedSessionId ?? ''"
      :disabled="disabled"
      @change="emit('view', ($event.target as HTMLSelectElement).value)"
    >
      <option value="" disabled>{{ t("actionChain.selectSession") }}</option>
      <option
        v-for="option in sessions"
        :key="option.target.sessionId"
        :value="option.target.sessionId"
      >
        {{ option.info.name || option.target.sessionId
        }}<template v-if="option.target.sessionId === runtimeSessionId">
          ● {{ t("actionChain.runningMark") }}</template
        >
      </option>
    </select>
  </label>
  <button
    v-if="viewedSessionId && viewedSessionId !== runtimeSessionId"
    type="button"
    class="chain-activate-session text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('activate')"
  >
    {{ t("actionChain.activateSession") }}
  </button>
  <button
    v-if="viewedSessionId"
    type="button"
    class="chain-archive-session text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('archive')"
  >
    {{ t("actionChain.archiveSession") }}
  </button>
  <button
    v-if="viewedSessionId"
    type="button"
    class="chain-delete-session text-xs underline text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50 disabled:cursor-not-allowed"
    :disabled="disabled || viewedSessionId === runtimeSessionId"
    :title="
      viewedSessionId === runtimeSessionId ? t('actionChain.deleteRunningDisabled') : undefined
    "
    @click="emit('remove')"
  >
    {{ t("actionChain.deleteSession") }}
  </button>
  <button
    type="button"
    class="chain-new-session text-xs underline text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
    :disabled="disabled"
    @click="emit('create')"
  >
    {{ t("actionChain.newSession") }}
  </button>
</template>
