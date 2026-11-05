<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ActionNode } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { abilityClass } from "./ability-class.js";
import ActionChainPayloadBlock from "./action-chain-payload-block.vue";
import type { PayloadBlock } from "./render-payload.js";
import { renderActionResult, renderContentBlock } from "./render-payload.js";

const { locale, t } = useI18n();

/**
 * 节点详情区（阶段 3 任务 3.2）：摘要头 + 增量消息区 + 结果区。
 *
 * 纯展示组件：无本地状态、不发命令；渲染描述全部来自 render-payload 纯函数层。
 */
const props = defineProps<{
  node: ActionNode | null;
}>();

const cls = computed(() => abilityClass(props.node?.ability_names ?? []));
const abilitySummary = computed(() => (props.node?.ability_names ?? []).join(", ") || "—");
const timeStr = computed(() => {
  const ts = props.node?.timestamp;
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString(locale.value);
});
const metaLine = computed(() => `${timeStr.value} iter=${props.node?.iteration ?? 0}`);

/** outcome 聚合：action_results 任一 failure 即 failure；否则取首个非空 outcome。 */
const outcome = computed(() => {
  const results = props.node?.action_results ?? [];
  let first: string | null = null;
  for (const item of results) {
    const o = item.action_result?.outcome;
    if (!o) continue;
    if (o === "failure") return "failure";
    if (first == null) first = o;
  }
  return first;
});

const outcomeClass = computed(() => `ac-detail-outcome--${outcome.value ?? "none"}`);

/** 增量消息区：messages_delta 逐 block 经 renderContentBlock 分派，null 跳过（J4 抑制）。 */
const messageBlocks = computed<{ role: string; block: PayloadBlock }[]>(() => {
  const node = props.node;
  if (!node) return [];
  const entries: { role: string; block: PayloadBlock }[] = [];
  for (const message of node.messages_delta ?? []) {
    for (const raw of message.content_blocks ?? []) {
      const block = renderContentBlock(raw, { abilityNames: node.ability_names ?? [] });
      if (block != null) entries.push({ role: message.role, block });
    }
  }
  return entries;
});

/** 结果区：action_results 逐条经 renderActionResult。 */
const resultBlocks = computed(() =>
  (props.node?.action_results ?? []).map((item) => renderActionResult(item)),
);
</script>

<template>
  <section v-if="node" class="ac-detail" :aria-label="t('actionChain.detailTitle')">
    <!-- 摘要头：能力色条 + timestamp/iter + outcome 聚合 -->
    <header class="ac-detail-header" :class="`ac-detail-header--${cls}`">
      <span class="ac-detail-colorbar" />
      <span class="ac-detail-meta">{{ metaLine }}</span>
      <span v-if="abilitySummary !== '—'" class="ac-detail-abilities">{{ abilitySummary }}</span>
      <span v-if="outcome" class="ac-detail-outcome" :class="outcomeClass">
        {{ t(`actionChain.detailOutcome.${outcome}`) }}
      </span>
    </header>

    <!-- 增量消息区 -->
    <div class="ac-detail-section">
      <h4 class="ac-detail-section-title">{{ t("actionChain.detailMessages") }}</h4>
      <p v-if="messageBlocks.length === 0" class="ac-detail-empty-line">
        {{ t("actionChain.detailNoMessages") }}
      </p>
      <div v-for="(entry, i) in messageBlocks" :key="`msg-${i}`" class="ac-detail-message">
        <span class="ac-detail-role">{{ entry.role }}</span>
        <ActionChainPayloadBlock :block="entry.block" />
      </div>
    </div>

    <!-- 结果区 -->
    <div class="ac-detail-section">
      <h4 class="ac-detail-section-title">{{ t("actionChain.detailResults") }}</h4>
      <p v-if="resultBlocks.length === 0" class="ac-detail-empty-line">
        {{ t("actionChain.detailNoResults") }}
      </p>
      <ActionChainPayloadBlock
        v-for="(block, i) in resultBlocks"
        :key="`res-${i}`"
        :block="block"
      />
    </div>
  </section>

  <!-- 未选中节点占位 -->
  <div v-else class="ac-detail ac-detail--empty">
    <p class="ac-detail-empty-line">{{ t("actionChain.detailEmpty") }}</p>
  </div>
</template>
