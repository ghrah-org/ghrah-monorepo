<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->
<script setup lang="ts">
import type { TurnCardView } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { compactMetrics } from "../action-chain/compact-node.js";
import { formatDuration, formatClock, phaseText, type Phase } from "./turn-card-model.js";

const props = defineProps<{
  turn: TurnCardView;
  phase: Phase;
  trigger: string;
  agentName: string;
  expanded: boolean;
  bodyId: string;
  durationMs: number | null;
  compact: ReturnType<typeof compactMetrics> | null;
}>();
const emit = defineEmits<{ toggle: [] }>();
const { t, locale } = useI18n();
const openedAt = computed(() => formatClock(props.turn.opened_at, locale.value));
const phaseLabel = computed(() => phaseText(props.phase, t));
</script>

<template>
  <p v-if="trigger" class="tc-trigger">{{ trigger }}</p>
  <header class="tc-head">
    <span class="tc-ident">
      <span class="tc-name">{{ agentName }}</span>
      <span v-if="compact" class="tc-badge--compact">
        {{
          compact.triggerSource
            ? t("actionChain.compactBadge", { source: compact.triggerSource })
            : t("room.verb.compact")
        }}
      </span>
    </span>
    <time v-if="openedAt" class="tc-time" :datetime="turn.opened_at">{{ openedAt }}</time>
    <span v-if="phaseLabel" class="tc-activity">
      <span class="tc-phase-dot" :class="`tc-phase--${phase.kind}`" aria-hidden="true" />
      <span class="tc-phase-text">{{ phaseLabel }}</span>
      <span v-if="turn.closed_at && durationMs !== null" class="tc-elapsed">
        {{ formatDuration(durationMs) }}
      </span>
    </span>
    <button
      class="tc-toggle"
      type="button"
      :aria-label="t(expanded ? 'room.card.collapse' : 'room.card.expand')"
      :aria-expanded="expanded"
      :aria-controls="bodyId"
      @click="emit('toggle')"
    >
      <span class="tc-chevron" aria-hidden="true" />
    </button>
    <span class="sr-only tc-live" aria-live="polite" />
  </header>
</template>

<style scoped>
.tc-head,
.tc-ident,
.tc-activity {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0.375rem;
  min-width: 0;
}

.tc-head {
  align-items: center;
}

.tc-name {
  color: var(--agent-color);
  font-weight: 600;
}

.tc-name,
.tc-activity {
  overflow-wrap: anywhere;
}

.tc-time,
.tc-elapsed,
.tc-badge--compact,
.tc-trigger,
.tc-activity {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}
.tc-time,
.tc-elapsed {
  font-variant-numeric: tabular-nums;
}
.tc-phase-dot {
  inline-size: 0.375rem;
  block-size: 0.375rem;
  border-radius: 50%;
  align-self: center;
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
.tc-trigger {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 760px) {
  .tc-time {
    display: none;
  }
}
</style>
