<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { HttpError, useAbilityPackagesStore } from "@ghrah/observer-core";
import type { AbilityPackageImportView } from "@ghrah/protocol";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{ open: boolean; maxBytes: number }>();

const emit = defineEmits<{
  close: [];
  installed: [view: AbilityPackageImportView];
}>();

const packages = useAbilityPackagesStore();
const { t } = useI18n();

const ACCEPT = ".zip";
const ALLOWED = new Set(["zip"]);

const input = ref<HTMLInputElement | null>(null);
const picked = ref<File | null>(null);
const error = ref<string | null>(null);
const progress = ref<number | null>(null);
const busy = ref(false);
const dragging = ref(false);

const limitLabel = computed(() => formatBytes(props.maxBytes));

function formatBytes(bytes: number): string {
  if (bytes >= 1 << 30) return `${Math.round(bytes / (1 << 30))} GB`;
  if (bytes >= 1 << 20) return `${Math.round(bytes / (1 << 20))} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function suffixOf(name: string): string {
  const at = name.lastIndexOf(".");
  return at < 0 ? "" : name.slice(at + 1).toLowerCase();
}

function reset(): void {
  picked.value = null;
  error.value = null;
  progress.value = null;
  busy.value = false;
  dragging.value = false;
  if (input.value) input.value.value = "";
}

watch(
  () => props.open,
  (open) => {
    if (open) reset();
  },
  { immediate: true },
);

function select(file: File | null): void {
  error.value = null;
  progress.value = null;
  if (!file) {
    picked.value = null;
    return;
  }
  if (!ALLOWED.has(suffixOf(file.name))) {
    picked.value = null;
    error.value = t("assembly.catalog.import.badType");
    return;
  }
  if (file.size > props.maxBytes) {
    picked.value = null;
    error.value = t("assembly.catalog.import.tooLarge", { size: limitLabel.value });
    return;
  }
  picked.value = file;
}

function onPick(event: Event): void {
  const target = event.target as HTMLInputElement | null;
  select(target?.files?.[0] ?? null);
}

function onDrop(event: DragEvent): void {
  dragging.value = false;
  if (busy.value) return;
  select(event.dataTransfer?.files?.[0] ?? null);
}

function messageFor(reason: unknown): string {
  const code = reason instanceof HttpError ? reason.code : "";
  if (code === "too_large")
    return t("assembly.catalog.import.tooLargeServer", { size: limitLabel.value });
  if (code === "unsupported_archive") return t("assembly.catalog.import.badType");
  if (code === "invalid_package") return t("assembly.catalog.import.invalidPackage");
  if (code === "conflict") return t("assembly.catalog.import.conflict");
  if (code === "capability_unavailable") return t("assembly.catalog.installUnavailable");
  return t("assembly.catalog.import.failed");
}

async function submit(): Promise<void> {
  const chosen = picked.value;
  if (!chosen || busy.value) return;
  busy.value = true;
  error.value = null;
  progress.value = 0;
  try {
    const form = new FormData();
    form.append("file", chosen, chosen.name);
    const view = await packages.install(form, {
      onProgress: (ratio) => {
        progress.value = Math.round(ratio * 100);
      },
    });
    emit("installed", view);
  } catch (reason) {
    error.value = messageFor(reason);
  } finally {
    busy.value = false;
    progress.value = null;
  }
}

function close(): void {
  if (busy.value) return;
  emit("close");
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.stopPropagation();
    close();
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener("keydown", onKeydown, true);
    else window.removeEventListener("keydown", onKeydown, true);
  },
  { immediate: true },
);

onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown, true));
</script>

<template>
  <div v-if="open" class="import-scrim" @click.self="close">
    <div
      class="import-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ability-import-title"
    >
      <header class="import-head">
        <h3 id="ability-import-title">{{ t("assembly.catalog.import.title") }}</h3>
        <p class="import-hint">{{ t("assembly.catalog.import.hint", { size: limitLabel }) }}</p>
      </header>

      <label
        class="import-drop"
        :data-dragging="dragging"
        :data-busy="busy"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input
          ref="input"
          class="import-file"
          type="file"
          :accept="ACCEPT"
          :disabled="busy"
          @change="onPick"
        />
        <span v-if="!picked" class="import-drop-title">
          {{ t("assembly.catalog.import.drop") }}
        </span>
        <span v-else class="import-drop-title">{{ picked.name }}</span>
        <span class="import-drop-sub">
          {{ picked ? formatBytes(picked.size) : t("assembly.catalog.import.choose") }}
        </span>
      </label>

      <p v-if="error" class="import-error" role="alert">{{ error }}</p>
      <p v-else-if="busy" class="import-progress">
        <span v-if="progress !== null && progress < 100">
          {{ t("assembly.catalog.import.uploading", { percent: progress }) }}
        </span>
        <span v-else>{{ t("assembly.catalog.import.installing") }}</span>
      </p>

      <footer class="import-actions">
        <UiButton variant="secondary" size="sm" :disabled="busy" @click="close">
          {{ t("assembly.catalog.import.cancel") }}
        </UiButton>
        <UiButton variant="primary" size="sm" :disabled="!picked || busy" @click="submit">
          {{ t("assembly.catalog.import.submit") }}
        </UiButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.import-scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  padding: 1.5rem;
  place-items: center;
  background: color-mix(in srgb, var(--color-bg) 78%, transparent);
}

.import-dialog {
  display: flex;
  width: min(30rem, 100%);
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem 1.125rem;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}

.import-head h3 {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
}

.import-hint {
  margin: 0.25rem 0 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
}

.import-drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.25rem;
  padding: 1.25rem 1rem;
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  cursor: pointer;
  text-align: center;
}

.import-drop[data-dragging="true"] {
  border-color: var(--color-selected-line);
  background: color-mix(in srgb, var(--color-selected-line) 12%, var(--color-surface));
}

.import-drop[data-busy="true"] {
  cursor: progress;
}

.import-file {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
}

.import-drop-title {
  max-width: 100%;
  overflow: hidden;
  color: var(--color-text);
  font-size: 0.8125rem;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.import-drop-sub {
  color: var(--color-text-subtle);
  font-size: 0.6875rem;
}

.import-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.75rem;
}

.import-progress {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}

.import-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
