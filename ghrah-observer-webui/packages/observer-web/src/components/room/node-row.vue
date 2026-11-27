<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import type { ActionNode } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { chainToken, nodeClass } from "../action-chain/ability-class.js";
import { compactMetrics, compactWarnings } from "../action-chain/compact-node.js";
import EntryContent from "./entry-content.vue";
import { formatClock, formatTokens, nodeError, nodeSummary } from "./turn-card-model.js";

const props = defineProps<{ node: ActionNode; projectId: string }>();
const { t, locale } = useI18n();
const diagnostic = computed(() => props.node.metadata.node_kind === "incomplete");
const kind = computed(() => (diagnostic.value ? "diagnose" : nodeClass(props.node)));
const dotColor = computed(() =>
  kind.value === "diagnose" ? "var(--chain-error)" : chainToken(kind.value),
);
const error = computed(() => nodeError(props.node));
const status = computed(() => {
  const explicit = props.node.metadata.status;
  if (explicit === "running" || explicit === "completed" || explicit === "failed") return explicit;
  return error.value ||
    props.node.action_results.some((result) => result.action_result?.outcome === "failure")
    ? "failed"
    : undefined;
});
const meta = computed(() => {
  const time = props.node.timestamp ? formatClock(props.node.timestamp, locale.value) : "";
  const usage = props.node.metadata.token_usage;
  const total =
    usage && typeof usage === "object" && "total_tokens" in usage ? usage.total_tokens : null;
  const tokens =
    typeof total === "number" && Number.isFinite(total)
      ? t("room.node.tokens", { n: formatTokens(total) })
      : "";
  return [time, tokens, status.value ? t(`room.node.status.${status.value}`) : ""]
    .filter(Boolean)
    .join(" · ");
});
const metrics = computed(() => (kind.value === "compact" ? compactMetrics(props.node) : null));
const compactLabels = computed(() => {
  const value = metrics.value;
  if (!value) return [];
  const labels = [];
  if (value.tokensBefore !== null && value.tokensAfter !== null)
    labels.push(
      t("room.card.compactTokens", { before: value.tokensBefore, after: value.tokensAfter }),
    );
  if (value.summarizedRange)
    labels.push(t("room.node.range", { range: value.summarizedRange.join("…") }));
  if (value.keptRecentNodes !== null)
    labels.push(t("room.node.kept", { n: value.keptRecentNodes }));
  if (value.postCheck) labels.push(t("room.node.check", { status: value.postCheck }));
  return labels;
});
const warnings = computed(() => {
  if (!metrics.value) return [];
  const result = compactWarnings(props.node);
  return [
    result.degraded && t("actionChain.compactDegraded"),
    result.overBudget && t("actionChain.compactOverBudget"),
    result.truncatedSummaryInput && t("actionChain.compactTruncatedInput"),
  ].filter(Boolean);
});
const messages = computed(() =>
  metrics.value ? (props.node.messages_snapshot ?? []) : props.node.messages_delta,
);
const title = computed(() => (metrics.value ? "" : nodeSummary(props.node)));
const detailId = computed(() => `node-detail-${props.node.id}`);
</script>

<template>
  <div
    class="node"
    :class="[`node--${kind}`, status && `node--status-${status}`]"
    :data-status="status"
  >
    <span class="node-rail" aria-hidden="true"
      ><span class="node-dot" :style="{ background: dotColor }"
    /></span>
    <div class="node-main">
      <span class="node-verb">{{ t(`room.verb.${kind}`) }}</span>
      <span v-if="title" class="node-title">{{ title }}</span>
      <span v-if="meta" class="node-meta">{{ meta }}</span>
      <p v-if="compactLabels.length" class="node-meta">{{ compactLabels.join(" · ") }}</p>
      <p v-if="error" class="node-error">{{ error }}</p>
      <p v-for="(warning, index) in warnings" :key="index" class="node-error">{{ warning }}</p>
      <details v-if="messages.length || metrics">
        <summary :aria-controls="detailId">
          {{ metrics ? t("actionChain.compactSnapshotTitle") : t("actionChain.detailMessages") }}
        </summary>
        <div :id="detailId" class="node-detail">
          <p v-if="metrics">
            {{ t("actionChain.compactSnapshotCount", { count: messages.length }) }}
          </p>
          <p v-if="metrics && !messages.length">{{ t("room.node.emptySnapshot") }}</p>
          <div v-for="(message, index) in messages" :key="index">
            <p class="node-meta">
              {{ message.role }}<template v-if="message.source"> · {{ message.source }}</template>
            </p>
            <EntryContent :project-id="projectId" content="" :blocks="message.content_blocks" />
          </div>
        </div>
      </details>
    </div>
  </div>
</template>

<style scoped>
.node {
  display: grid;
  grid-template-columns: 0.75rem minmax(0, 1fr);
  gap: var(--spacing-2);
}
.node-rail {
  display: grid;
  justify-items: center;
  padding-block-start: 0.5rem;
}
.node-dot {
  inline-size: 0.375rem;
  block-size: 0.375rem;
  border-radius: 50%;
}
.node-main {
  min-inline-size: 0;
  font-size: var(--font-ui-note);
}
.node-verb,
.node-title {
  margin-inline-end: 0.375rem;
}
.node-verb {
  font-weight: 600;
}
.node-meta {
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
.node-error {
  color: var(--color-danger);
}
.node-main p {
  margin: 0;
}
.node-detail {
  margin-inline-start: var(--spacing-2);
}
</style>
