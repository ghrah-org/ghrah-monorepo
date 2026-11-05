<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { ActionNode } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { abilityClass } from "./ability-class.js";
import type { LayoutNode } from "./layout-graph.js";
import { DEFAULT_NODE_WIDTH } from "./layout-graph.js";

const { locale, t } = useI18n();

const props = defineProps<{
  layoutNode: LayoutNode;
  selected: boolean;
}>();

const emit = defineEmits<{ select: [nodeId: string] }>();

const CARD_HEIGHT = 48;
const NODE_WIDTH = DEFAULT_NODE_WIDTH;

const node = computed<ActionNode>(() => props.layoutNode.node);
const cls = computed(() => abilityClass(node.value.ability_names ?? []));
const abilitySummary = computed(() => (node.value.ability_names ?? []).join(",") || "—");
const timeStr = computed(() => {
  const ts = node.value.timestamp;
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString(locale.value);
});
const metaLine = computed(() => `${timeStr.value} iter=${node.value.iteration ?? 0}`);
const topY = computed(() => props.layoutNode.y - CARD_HEIGHT / 2);

function onSelect(): void {
  const id = node.value.id;
  if (id) emit("select", id);
}
</script>

<template>
  <g
    class="ac-node"
    :class="[`ac-node--${cls}`, { selected: props.selected }]"
    :transform="`translate(${layoutNode.x}, ${topY})`"
    tabindex="0"
    role="button"
    :aria-label="`${metaLine} | ${abilitySummary}`"
    @click.stop="onSelect"
    @keydown.enter.prevent="onSelect"
    @keydown.space.prevent="onSelect"
  >
    <rect class="ac-node-box" :width="NODE_WIDTH" :height="CARD_HEIGHT" rx="6" />
    <circle class="ac-node-dot" cx="10" cy="24" r="3.5" />
    <rect
      class="ac-node-colorbar"
      :x="NODE_WIDTH - 6"
      y="6"
      width="3"
      :height="CARD_HEIGHT - 12"
      rx="1.5"
    />
    <text class="ac-node-meta" x="20" y="20">{{ metaLine }}</text>
    <text class="ac-node-abilities" x="20" y="36">{{ abilitySummary }}</text>
    <text
      v-if="layoutNode.isHead"
      class="ac-node-badge ac-node-badge--head"
      :x="NODE_WIDTH - 16"
      y="20"
    >
      {{ t("actionChain.headBadge") }}
    </text>
    <text
      v-if="layoutNode.isBranchPoint"
      class="ac-node-badge ac-node-badge--fork"
      :x="NODE_WIDTH - 16"
      y="36"
    >
      {{ t("actionChain.forkBadge") }}
    </text>
  </g>
</template>
