<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useHitlStore } from "@ghrah/observer-core";
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useObserver } from "@/composables/useObserver";
import HitlInbox from "./hitl-inbox.vue";
import UiButton from "@/components/ui/button.vue";

/**
 * 待审批摘要条：会话页底部只留一行（谁、什么工具、在等什么），
 * 完整队列（批量审批、拒绝理由）收进“详情”展开区。
 */
const { t } = useI18n();
const hitl = useHitlStore();
const { sendHitlResponse } = useObserver();

const expanded = ref(false);
const approving = ref(false);

const count = computed(() => hitl.pendingRequests.length);
const first = computed(() => hitl.pendingRequests[0] ?? null);

/** 一行摘要里最有用的一条参数：优先文件路径 / 命令，其次前两个键值。 */
const detail = computed(() => {
  const args = first.value?.toolArgs;
  if (!args) return "";
  for (const key of ["file_path", "command", "path"]) {
    const value = args[key];
    if (typeof value === "string" && value) return value;
  }
  const entries = Object.entries(args).slice(0, 2);
  return entries
    .map(([key, value]) => {
      const text =
        typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
      return `${key}=${text.length > 30 ? `${text.slice(0, 30)}…` : text}`;
    })
    .join(", ");
});

async function approveFirst() {
  const request = first.value;
  if (!request || approving.value) return;
  approving.value = true;
  try {
    await sendHitlResponse(request.promiseId, true);
  } finally {
    approving.value = false;
  }
}
</script>

<template>
  <footer v-if="count > 0" class="hitl-dock">
    <div class="hitl-bar">
      <span class="hitl-bar-icon" aria-hidden="true">⏸</span>
      <p class="hitl-bar-summary">
        <strong>{{ t("hitl.bar.pending", { count }) }}</strong>
        <span v-if="first" class="hitl-bar-detail">
          {{ first.abilityName }} · {{ first.agentName
          }}<template v-if="detail"> · {{ detail }}</template>
        </span>
      </p>
      <div class="hitl-bar-actions">
        <UiButton
          type="button"
          variant="secondary"
          :aria-expanded="expanded"
          aria-controls="hitl-bar-panel"
          @click="expanded = !expanded"
        >
          {{ t("hitl.bar.details") }}
        </UiButton>
        <UiButton type="button" variant="danger" @click="expanded = true">
          {{ t("hitl.bar.reject") }}
        </UiButton>
        <UiButton type="button" variant="primary" :disabled="approving" @click="approveFirst">
          {{ t("hitl.bar.approve") }}
        </UiButton>
      </div>
    </div>

    <div v-if="expanded" id="hitl-bar-panel" class="hitl-bar-panel">
      <HitlInbox />
    </div>
  </footer>
</template>

<style scoped>
.hitl-dock {
  display: flex;
  flex-direction: column;
  max-height: 18rem;
  overflow: hidden;
  border-top: 1px solid var(--ui-panel-border);
  background: var(--ui-panel);
}

.hitl-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 3rem;
  padding: 0 0.75rem;
}

.hitl-bar-icon {
  color: var(--ac-write);
  font-size: var(--font-ui-note);
}

.hitl-bar-summary {
  display: flex;
  align-items: baseline;
  gap: 0.375rem;
  min-width: 0;
  flex: 1;
  margin: 0;
  font-size: var(--font-ui-note);
}

.hitl-bar-summary strong {
  flex: 0 0 auto;
}

.hitl-bar-detail {
  overflow: hidden;
  color: var(--ui-panel-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hitl-bar-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 0.375rem;
}

.hitl-bar-panel {
  min-height: 0;
  overflow: hidden auto;
  border-top: 1px solid var(--ui-panel-border);
}
</style>
