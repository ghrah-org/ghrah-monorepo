<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, ref, useId, watch } from "vue";

const props = withDefaults(
  defineProps<{
    title: string;
    headingLevel?: 2 | 3 | 4;
    defaultOpen?: boolean;
    persistKey?: string;
    disabled?: boolean;
  }>(),
  { headingLevel: 3, defaultOpen: false, persistKey: undefined, disabled: false },
);

const emit = defineEmits<{ toggle: [open: boolean] }>();

const STORAGE_PREFIX = "ghrah-collapse:";

const contentId = `collapsible-content-${useId()}`;
const headingTag = computed(() => `h${props.headingLevel}` as "h2" | "h3" | "h4");

function storageKey(): string | null {
  return props.persistKey ? `${STORAGE_PREFIX}${props.persistKey}` : null;
}

// Storage may be missing (SSR) or throw (privacy mode, quota, sandboxed iframe).
// Either way the collapse state is cosmetic, so degrade silently.
function readStoredOpen(): boolean | null {
  const key = storageKey();
  if (!key || typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(key);
    if (stored === null) return null;
    return stored === "1";
  } catch {
    return null;
  }
}

function writeStoredOpen(open: boolean): void {
  const key = storageKey();
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, open ? "1" : "0");
  } catch {
    // Ignore: the section still works without persistence.
  }
}

const open = ref(readStoredOpen() ?? props.defaultOpen);

watch(
  () => props.persistKey,
  () => {
    open.value = readStoredOpen() ?? props.defaultOpen;
  },
);

function toggle(): void {
  if (props.disabled) return;
  open.value = !open.value;
  writeStoredOpen(open.value);
  emit("toggle", open.value);
}
</script>

<template>
  <section class="collapsible-section" :class="{ 'is-open': open }">
    <component :is="headingTag" class="collapsible-heading">
      <button
        type="button"
        class="collapsible-trigger"
        :aria-expanded="open"
        :aria-controls="contentId"
        :disabled="disabled"
        @click="toggle"
      >
        <span class="collapsible-chevron" aria-hidden="true">▸</span>
        <span class="collapsible-title">{{ title }}</span>
      </button>
      <span v-if="$slots.actions" class="collapsible-actions"><slot name="actions" /></span>
    </component>
    <div v-show="open" :id="contentId" class="collapsible-content">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.collapsible-section {
  display: flex;
  min-width: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--font-ui-body);
}

.collapsible-heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--spacing-2);
  margin: 0;
  padding: var(--spacing-2) var(--spacing-3);
  border-bottom: 1px solid transparent;
  font-size: var(--font-ui-body);
  font-weight: 600;
  line-height: var(--leading-tight);
}

.is-open .collapsible-heading {
  border-bottom-color: var(--color-border);
}

.collapsible-trigger {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  align-items: baseline;
  gap: var(--spacing-2);
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: start;
  cursor: pointer;
}

.collapsible-trigger:disabled {
  cursor: default;
  opacity: 0.6;
}

.collapsible-trigger:focus-visible {
  border-radius: var(--radius-md);
  outline: 2px solid currentcolor;
  outline-offset: 2px;
}

.collapsible-chevron {
  flex: none;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  transition: transform 120ms ease;
}

.is-open .collapsible-chevron {
  transform: rotate(90deg);
}

.collapsible-title {
  min-width: 0;
  overflow-wrap: anywhere;
}

.collapsible-actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--spacing-2);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 400;
}

.collapsible-content {
  min-width: 0;
  padding: var(--spacing-3);
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  line-height: var(--leading-body);
}

@media (prefers-reduced-motion: reduce) {
  .collapsible-chevron {
    transition: none;
  }
}
</style>
