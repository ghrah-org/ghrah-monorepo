<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import ActionNodeCard from "./action-node.vue";
import type { LayoutGraph } from "./layout-graph.js";

const { t } = useI18n();

const props = defineProps<{
  graph: LayoutGraph;
  selectedNodeId: string | null;
}>();

const emit = defineEmits<{
  select: [nodeId: string | null];
  /** 视口滚动漫游（滚动恢复由面板挂接，组件自身不持状态）。 */
  scroll: [];
}>();

/** 独立滚动视口：唯一滚动容器（双向 overflow:auto，样式契约在 style.css）。 */
const viewport = ref<HTMLElement | null>(null);
defineExpose({ viewport });

/** 泳道分隔线 y 坐标（1..laneCount-1；laneHeight = height / laneCount）。 */
const laneGuideYs = computed<number[]>(() => {
  if (props.graph.height <= 0) return [];
  const laneCount = props.graph.nodes.reduce((max, n) => Math.max(max, n.laneIndex + 1), 0);
  if (laneCount <= 1) return [];
  const laneHeight = props.graph.height / laneCount;
  return Array.from({ length: laneCount - 1 }, (_, i) => Math.round((i + 1) * laneHeight));
});

function onBackground(): void {
  emit("select", null);
}
</script>

<template>
  <div class="ac-canvas">
    <div class="ac-legend">
      <span class="ac-legend-item"
        ><i class="ac-legend-swatch ac-legend-swatch--write" />{{
          t("actionChain.legendWrite")
        }}</span
      >
      <span class="ac-legend-item"
        ><i class="ac-legend-swatch ac-legend-swatch--read" />{{
          t("actionChain.legendRead")
        }}</span
      >
      <span class="ac-legend-item"
        ><i class="ac-legend-swatch ac-legend-swatch--converse" />{{
          t("actionChain.legendConverse")
        }}</span
      >
      <span class="ac-legend-item"
        ><i class="ac-legend-swatch ac-legend-swatch--unknown" />{{
          t("actionChain.legendUnknown")
        }}</span
      >
    </div>
    <div
      ref="viewport"
      class="ac-canvas-viewport"
      @click.self="onBackground"
      @scroll.passive="emit('scroll')"
    >
      <svg
        :width="graph.width"
        :height="graph.height"
        class="ac-canvas-svg"
        role="img"
        :aria-label="t('actionChain.canvasLabel')"
      >
        <!-- 泳道分隔虚线 -->
        <line
          v-for="(y, i) in laneGuideYs"
          :key="`lane-${i}`"
          class="ac-lane-guide"
          x1="0"
          :y1="y"
          :x2="graph.width"
          :y2="y"
        />
        <!-- 边 -->
        <path
          v-for="(edge, i) in graph.edges"
          :key="`edge-${i}`"
          class="ac-edge"
          :class="{ 'ac-edge--lane-change': edge.laneChange }"
          :d="edge.path"
        />
        <!-- 节点 -->
        <ActionNodeCard
          v-for="layoutNode in graph.nodes"
          :key="layoutNode.node.id ?? ''"
          :layout-node="layoutNode"
          :selected="selectedNodeId !== null && layoutNode.node.id === selectedNodeId"
          @select="emit('select', $event)"
        />
      </svg>
    </div>
  </div>
</template>
