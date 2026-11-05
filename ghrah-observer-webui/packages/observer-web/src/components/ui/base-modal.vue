<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";

const props = withDefaults(
  defineProps<{
    title: string;
    size?: "sm" | "md" | "lg";
    closeLabel?: string;
    closeOnBackdrop?: boolean;
    showClose?: boolean;
  }>(),
  { size: "md", closeLabel: undefined, closeOnBackdrop: true, showClose: true },
);
const emit = defineEmits<{ requestClose: [] }>();
const { t } = useI18n();
const panel = ref<HTMLElement | null>(null);
const titleId = `modal-title-${Math.random().toString(36).slice(2)}`;
let returnFocus: HTMLElement | null = null;

function focusables(): HTMLElement[] {
  if (!panel.value) return [];
  return Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hasAttribute("hidden"));
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    emit("requestClose");
    return;
  }
  if (event.key !== "Tab") return;
  const items = focusables();
  if (items.length === 0) {
    event.preventDefault();
    panel.value?.focus();
    return;
  }
  const first = items[0];
  const last = items.at(-1);
  if (
    (event.shiftKey &&
      (document.activeElement === first || document.activeElement === panel.value)) ||
    (!event.shiftKey && document.activeElement === last)
  ) {
    event.preventDefault();
    (event.shiftKey ? last : first)?.focus();
  }
}

function onBackdrop(event: PointerEvent) {
  if (props.closeOnBackdrop && event.target === event.currentTarget) emit("requestClose");
}

onMounted(async () => {
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  await nextTick();
  const preferred = panel.value?.querySelector<HTMLElement>("[data-autofocus]");
  (preferred ?? focusables()[0] ?? panel.value)?.focus();
});

onUnmounted(() => {
  const target = returnFocus;
  void nextTick(() => target?.focus());
});
</script>

<template>
  <div class="base-modal-backdrop" @pointerdown="onBackdrop" @keydown="onKeydown">
    <section
      ref="panel"
      :class="['base-modal', `base-modal-${size}`]"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      tabindex="-1"
    >
      <header class="base-modal-header">
        <div class="base-modal-heading">
          <h2 :id="titleId">{{ title }}</h2>
          <slot name="subtitle" />
        </div>
        <button
          v-if="showClose"
          type="button"
          class="base-modal-close"
          :aria-label="closeLabel ?? t('common.close')"
          @click="$emit('requestClose')"
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <div class="base-modal-body"><slot /></div>
      <footer v-if="$slots.footer" class="base-modal-footer"><slot name="footer" /></footer>
    </section>
  </div>
</template>
