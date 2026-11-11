<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

/**
 * 会话列的宽度手柄。
 *
 * 只负责交互，不持有状态：拖拽过程发 preview（父级只改内联变量），
 * 松手或键盘调整才发 commit（父级写响应式状态与存储）。
 */
const props = defineProps<{
  /** 已提交的列宽（rem）。 */
  width: number;
  min: number;
  max: number;
  step: number;
}>();

const emit = defineEmits<{
  preview: [width: number];
  commit: [width: number];
  reset: [];
}>();

const { t } = useI18n();

// 抓手只在悬停、拖拽或键盘聚焦时渲染；命中区本身常驻（否则无从悬停）。
const hovering = ref(false);
const dragging = ref(false);
const focused = ref(false);
const gripVisible = computed(() => hovering.value || dragging.value || focused.value);

let startX = 0;
let startWidth = 0;
let pending = 0;

function clamp(value: number): number {
  return Math.min(props.max, Math.max(props.min, value));
}

/** 1px 折合多少 rem：以根字号为准，字号缩放后拖拽手感保持一致。 */
function pxToRem(): number {
  const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(root) && root > 0 ? 1 / root : 1 / 16;
}

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) return;
  dragging.value = true;
  startX = event.clientX;
  startWidth = props.width;
  pending = props.width;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  // 拖拽时不要选中文本，也不要触发浏览器的原生拖拽。
  event.preventDefault();
}

function onPointerMove(event: PointerEvent) {
  if (!dragging.value) return;
  pending = clamp(startWidth + (event.clientX - startX) * pxToRem());
  emit("preview", pending);
}

function onPointerUp(event: PointerEvent) {
  if (!dragging.value) return;
  dragging.value = false;
  const target = event.currentTarget as HTMLElement;
  if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
  emit("commit", pending);
}

function onPointerCancel(event: PointerEvent) {
  if (!dragging.value) return;
  dragging.value = false;
  const target = event.currentTarget as HTMLElement;
  if (target.hasPointerCapture(event.pointerId)) target.releasePointerCapture(event.pointerId);
  // 取消时回落到已提交值，避免停在半途的预览宽度上。
  emit("preview", props.width);
}

function onKeydown(event: KeyboardEvent) {
  const moves: Record<string, number> = {
    ArrowLeft: -props.step,
    ArrowRight: props.step,
  };
  if (event.key in moves) {
    event.preventDefault();
    emit("commit", clamp(props.width + moves[event.key]));
    return;
  }
  if (event.key === "Home") {
    event.preventDefault();
    emit("commit", props.min);
    return;
  }
  if (event.key === "End") {
    event.preventDefault();
    emit("commit", props.max);
  }
}
</script>

<template>
  <div
    class="conversation-resizer"
    :class="{ 'is-active': gripVisible }"
    role="separator"
    aria-orientation="vertical"
    :aria-label="t('chat.resize.handle')"
    :aria-valuenow="Math.round(width)"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :title="t('chat.resize.hint')"
    tabindex="0"
    @pointerenter="hovering = true"
    @pointerleave="hovering = false"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerCancel"
    @keydown="onKeydown"
    @focus="focused = true"
    @blur="focused = false"
    @dblclick="emit('reset')"
  >
    <span v-if="gripVisible" class="conversation-grip" />
  </div>
</template>

<style scoped>
/*
 * 命中区紧贴会话列右缘、落在列外的留白里，因此不会压住列的滚动条。
 * 列宽本身已被父级收成 min(变量, 100% - 3rem)，这里同步用同一个 min()，
 * 于是无论拖到多大、窗口多窄，手柄都稳稳停在列的右侧 0–0.75rem 处。
 */
.conversation-resizer {
  position: absolute;
  inset-block: 0;
  right: calc((100% - min(var(--width-conversation), 100% - 3rem)) / 2);
  display: flex;
  width: 0.75rem;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  touch-action: none;
  user-select: none;
  z-index: 4;
}

.conversation-grip {
  width: 0.1875rem;
  height: 2.5rem;
  border-radius: 999px;
  background: var(--color-border-strong);
  transition:
    background-color 0.15s ease,
    height 0.15s ease;
}

.conversation-resizer:hover .conversation-grip,
.conversation-resizer.is-active .conversation-grip,
.conversation-resizer:focus-visible .conversation-grip {
  height: 4rem;
  background: var(--color-text-subtle);
}

.conversation-resizer:focus-visible {
  outline: 0.125rem solid var(--color-selected-line);
  outline-offset: 0.125rem;
  border-radius: 0.25rem;
}
</style>
