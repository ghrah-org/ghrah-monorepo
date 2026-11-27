<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import { contentBlocksFromData, withTextContent } from "@ghrah/observer-core";
import type { TranscriptEntryView, TurnCardView, TurnDetailView } from "@ghrah/protocol";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import ActivityHeader from "./activity-header.vue";
import EntryContent from "./entry-content.vue";
import InsertNote from "./insert-note.vue";
import NodeRow from "./node-row.vue";
import WaitRow from "./wait-row.vue";
import type { WaitRequest } from "./wait-request.js";
import StatsBar from "./stats-bar.vue";
import UiButton from "@/components/ui/button.vue";
import { compactMetrics, isCompactNode } from "../action-chain/compact-node.js";
import {
  agentColorIndex,
  derivePhase,
  firstLine,
  injectedSummary,
  nodeSummary,
  triggerText,
  turnStats,
} from "./turn-card-model.js";

const props = defineProps<{
  roomId: string;
  projectId?: string;
  turn: TurnCardView;
  agentDisplayName?: string;
  opener?: TranscriptEntryView | null;
  replies?: readonly TranscriptEntryView[];
  waits?: readonly WaitRequest[];
  entries?: readonly TranscriptEntryView[];
  detail?: TurnDetailView;
  loading?: boolean;
  error?: string | null;
}>();
const emit = defineEmits<{ expand: [turn: TurnCardView] }>();
const { t, locale } = useI18n();

const detail = computed(() => {
  const view = props.detail;
  if (view?.turn.turn_id === props.turn.turn_id && view.turn.agent_name === props.turn.agent_name) {
    return view;
  }
  return undefined;
});
const nodes = computed(() => detail.value?.nodes ?? []);
const compactNode = computed(() => {
  const [first] = nodes.value;
  return nodes.value.length === 1 && first && isCompactNode(first) ? first : undefined;
});
const compact = computed(() => (compactNode.value ? compactMetrics(compactNode.value) : null));
const last = computed(() => {
  const metrics = compact.value;
  if (metrics && metrics.tokensBefore !== null && metrics.tokensAfter !== null) {
    return t("room.card.compactTokens", {
      before: metrics.tokensBefore,
      after: metrics.tokensAfter,
    });
  }
  return firstLine(props.replies?.at(-1)?.content ?? "") || nodeSummary(nodes.value.at(-1));
});

const agentName = computed(() => props.agentDisplayName?.trim() || props.turn.agent_name);
const initial = computed(() => Array.from(agentName.value)[0]?.toLocaleUpperCase(locale.value));
const agentColor = computed(
  () => `var(--agent-a${agentColorIndex(props.turn.agent_name)}, var(--color-text-muted))`,
);
const trigger = computed(() => triggerText(props.turn, props.opener ?? null, t));
const phase = computed(() => derivePhase(props.turn, nodes.value, Date.now()));
const stats = computed(() => turnStats(props.turn, nodes.value, Date.now()));
const inserted = computed(() => injectedSummary(props.entries ?? [], props.turn.turn_id));
const waits = computed(
  () =>
    props.waits?.filter(
      (request) =>
        request.turnId === props.turn.turn_id &&
        request.agentId === props.turn.agent_name &&
        request.status === "pending",
    ) ?? [],
);
const replies = computed(
  () =>
    props.replies?.map((entry) => ({
      entry,
      blocks: withTextContent(contentBlocksFromData(entry), entry.content),
    })) ?? [],
);
const timeline = computed(() => {
  const byStep = new Map<number, typeof inserted.value.inlineInserts>();
  for (const item of inserted.value.inlineInserts) {
    const atStep = byStep.get(item.step) ?? [];
    atStep.push(item);
    byStep.set(item.step, atStep);
  }
  let step = 0;
  const rows = nodes.value.map((node) => {
    if (isCompactNode(node) || node.metadata.node_kind === "incomplete")
      return { node, inserts: [] };
    const inserts = byStep.get(++step) ?? [];
    byStep.delete(step);
    return { node, inserts };
  });
  return { rows, remaining: [...byStep.values()].flat() };
});

const bodyId = computed(() => `tc-body-${props.turn.turn_id}`);
const storageKey = computed(() => `tc-expanded:${props.roomId}:${props.turn.turn_id}`);
const remembered = ref<boolean | null>(null);
const expanded = computed(() => remembered.value ?? props.turn.status === "waiting_hitl");

watch(
  storageKey,
  (key) => {
    remembered.value = null;
    try {
      const stored = globalThis.sessionStorage?.getItem(key);
      if (stored === "true" || stored === "false") remembered.value = stored === "true";
    } catch {
      // 浏览器限制存储时，仍允许在当前卡片上展开。
    }
  },
  { immediate: true },
);

watch(
  [storageKey, expanded],
  ([, open]) => {
    if (open && !detail.value && !props.loading) emit("expand", props.turn);
  },
  { immediate: true },
);

function toggle() {
  remembered.value = !expanded.value;
  try {
    globalThis.sessionStorage?.setItem(storageKey.value, String(remembered.value));
  } catch {
    // 当前卡片保留用户选择。
  }
}
</script>

<template>
  <article
    class="turn-card"
    data-room-item
    :class="{ 'turn-card--compact': compactNode }"
    :data-turn-id="turn.turn_id"
    :data-status="turn.status"
    :data-expanded="expanded"
    :style="{ '--agent-color': agentColor }"
  >
    <div class="tc-rail" aria-hidden="true">
      <span class="tc-avatar"
        ><span class="avatar-letter">{{ initial }}</span></span
      >
    </div>
    <div class="tc-main">
      <ActivityHeader
        :turn="turn"
        :phase="phase"
        :trigger="trigger"
        :agent-name="agentName"
        :compact="compact"
        :expanded="expanded"
        :body-id="bodyId"
        :duration-ms="stats.durationMs"
        @toggle="toggle"
      />
      <p v-if="inserted.injected || inserted.waiting" class="tc-injected">
        {{ t("room.card.injected", { injected: inserted.injected, waiting: inserted.waiting }) }}
      </p>
      <p v-if="!expanded && last" class="tc-last">{{ last }}</p>
      <section :id="bodyId" class="tc-body" :hidden="!expanded" :aria-hidden="!expanded">
        <template v-if="expanded">
          <div v-if="loading || (!detail && !error)" class="tc-skeleton" role="status">
            {{ t("room.card.loading") }}
          </div>
          <div v-else-if="error && !detail" class="tc-error">
            <p role="alert">{{ t("room.card.loadFailed") }}</p>
            <UiButton variant="secondary" size="xs" type="button" @click="emit('expand', turn)">
              {{ t("room.card.retry") }}
            </UiButton>
          </div>
          <div v-else-if="nodes.length === 0" class="tc-empty">{{ t("room.card.emptyNodes") }}</div>
          <div v-else class="tc-timeline">
            <template v-for="{ node, inserts } in timeline.rows" :key="node.id">
              <NodeRow :node="node" :project-id="projectId ?? ''" />
              <InsertNote
                v-for="{ entry, step } in inserts"
                :key="entry.entry_id"
                mode="inline"
                :entry="entry"
                :step="step"
                :project-id="projectId ?? ''"
              />
            </template>
          </div>
          <InsertNote
            v-for="{ entry, step } in timeline.remaining"
            :key="entry.entry_id"
            mode="inline"
            :entry="entry"
            :step="step"
            :project-id="projectId ?? ''"
          />
          <EntryContent
            v-for="{ entry, blocks } in replies"
            :key="entry.entry_id"
            class="tc-reply"
            :data-ability="entry.ability"
            :content="entry.content"
            :blocks="blocks"
            :project-id="projectId ?? ''"
          />
          <div v-if="inserted.waitingInserts.length" class="insert-waiting">
            {{ t("room.insert.waiting") }}
          </div>
          <div v-if="waits.length || $slots.waits" class="tc-wait-list">
            <WaitRow v-for="request in waits" :key="request.requestId" :request="request" />
            <slot name="waits" />
          </div>
          <p v-if="turn.recovery_branch_id" class="tc-recovery">
            {{ t("room.card.recovery") }} <code>{{ turn.recovery_branch_id }}</code>
          </p>
        </template>
      </section>
      <StatsBar
        v-if="expanded && turn.status !== 'open'"
        :stats="stats"
        :turn-index="turn.turn_index"
      />
    </div>
  </article>
</template>

<style scoped>
.turn-card {
  display: grid;
  grid-template-columns: 2.5rem minmax(0, 1fr);
  gap: var(--spacing-3);
  min-width: 0;
  padding: var(--spacing-2) var(--spacing-3);
  border-radius: var(--radius-lg);
}

.turn-card[data-expanded="true"] {
  background: var(--row-tint-expanded, transparent);
}

.turn-card:hover {
  background: var(--row-tint-hover, transparent);
}

.tc-avatar {
  display: grid;
  place-items: center;
  aspect-ratio: 1;
  border: 1px solid var(--agent-color);
  border-radius: 50%;
  color: var(--agent-color);
  font-weight: 600;
}

.tc-last,
.tc-injected,
.tc-foot {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.tc-time,
.tc-elapsed,
.tc-foot {
  font-variant-numeric: tabular-nums;
}

.tc-activity {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.tc-phase-dot {
  align-self: center;
  inline-size: 0.375rem;
  block-size: 0.375rem;
  border-radius: 50%;
  background: var(--color-text-subtle);
}

.tc-phase--done {
  background: var(--color-success);
}

.tc-phase--waiting_hitl,
.tc-phase--interrupted {
  background: var(--color-warn);
}

.tc-phase--failed,
.tc-phase--aborted,
.tc-phase--rolled_back {
  background: var(--color-danger);
}

.tc-toggle {
  display: grid;
  place-items: center;
  inline-size: 1.75rem;
  block-size: 1.75rem;
  margin-inline-start: auto;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
}

.tc-toggle:hover {
  background: var(--color-surface-2);
}

.tc-chevron {
  inline-size: 0.4375rem;
  block-size: 0.4375rem;
  border-inline-end: 1px solid currentColor;
  border-block-end: 1px solid currentColor;
  transform: rotate(45deg);
}

.tc-toggle[aria-expanded="true"] .tc-chevron {
  transform: rotate(225deg);
}

.tc-last,
.tc-injected {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tc-body:not([hidden]) {
  overflow-wrap: anywhere;
  padding-block: var(--spacing-2);
}

.tc-timeline {
  display: grid;
  gap: 0.375rem;
}

.tc-skeleton,
.tc-empty,
.tc-recovery,
.insert-note--inline,
.insert-waiting {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.tc-error {
  color: var(--color-danger);
}

.tc-reply {
  margin-block: var(--spacing-2);
}

.tc-recovery {
  padding-inline-start: var(--spacing-2);
  border-inline-start: 2px solid var(--color-selected-line);
}

@media (max-width: 760px) {
  .turn-card {
    grid-template-columns: 2rem minmax(0, 1fr);
    gap: var(--spacing-2);
  }
}
</style>
