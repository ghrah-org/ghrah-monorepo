<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { PendingAttachment } from "@/composables/useAttachments";

const props = defineProps<{
  attachment: PendingAttachment;
  /** 单个附件的失败说明（i18n 后的文本）；正常时为 null。 */
  error?: string | null;
}>();
const emit = defineEmits<{ remove: []; retry: [] }>();
const { t } = useI18n();

const sizeLabel = computed(() => {
  const bytes = props.attachment.size;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
});

const isVideo = computed(() => props.attachment.kind === "video");
const thumb = computed(() => {
  if (props.attachment.kind === "image") return props.attachment.dataUrl;
  if (props.attachment.kind === "video") return props.attachment.coverDataUrl;
  return "";
});
const extension = computed(() => {
  if (props.attachment.kind !== "file") return "";
  const match = /\.([A-Za-z0-9]+)$/.exec(props.attachment.name);
  return (match?.[1] ?? "file").toUpperCase();
});
const durationLabel = computed(() => {
  if (props.attachment.kind !== "video") return "";
  const duration = props.attachment.durationS;
  if (!(duration > 0)) return "";
  const totalSeconds = Math.round(duration);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
});
const videoStatus = computed(() =>
  props.attachment.kind === "video" ? props.attachment.status : "ready",
);
const videoProgress = computed(() =>
  props.attachment.kind === "video" ? Math.round(props.attachment.progress * 100) : 0,
);
const videoErrorKey = computed(() =>
  props.attachment.kind === "video" ? props.attachment.errorKey : undefined,
);
const videoError = computed(() => {
  if (videoErrorKey.value) return t(videoErrorKey.value);
  return props.error ?? null;
});
</script>

<template>
  <div :class="['attachment-chip', { 'attachment-chip-error': videoError }]">
    <img v-if="thumb" :src="thumb" :alt="attachment.name" class="attachment-thumb" />
    <div v-else class="attachment-thumb attachment-thumb-file" aria-hidden="true">
      {{ extension }}
    </div>
    <div class="attachment-meta">
      <span class="attachment-name" :title="attachment.name">{{ attachment.name }}</span>
      <span class="attachment-size">
        {{ sizeLabel }}
        <template v-if="durationLabel"> · {{ t("chat.video") }} {{ durationLabel }}</template>
      </span>
      <span v-if="isVideo && videoStatus === 'uploading'" class="attachment-status">
        {{ t("chat.attachment.uploadingPercent", { percent: videoProgress }) }}
      </span>
      <span v-else-if="videoError" class="attachment-error" role="alert">{{ videoError }}</span>
    </div>
    <button
      v-if="isVideo && videoStatus === 'error'"
      type="button"
      class="attachment-retry"
      :aria-label="t('chat.attachment.retry')"
      @click="emit('retry')"
    >
      {{ t("chat.attachment.retry") }}
    </button>
    <button
      type="button"
      class="attachment-remove"
      :aria-label="t('chat.attachment.remove')"
      @click="emit('remove')"
    >
      ×
    </button>
  </div>
</template>

<style scoped>
.attachment-chip {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.attachment-chip-error {
  border-color: var(--color-danger);
}

.attachment-thumb {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: var(--radius-md);
  object-fit: cover;
  flex: none;
}

.attachment-thumb-file {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.attachment-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.125rem;
}

.attachment-name {
  overflow: hidden;
  color: var(--color-text);
  font-size: var(--font-ui-note);
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 14rem;
}

.attachment-size,
.attachment-status {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.attachment-error {
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.attachment-retry {
  padding: 0 0.25rem;
  border: 0;
  background: none;
  color: var(--color-text);
  font-size: var(--font-ui-note);
  text-decoration: underline;
  cursor: pointer;
}

.attachment-remove {
  margin-inline-start: auto;
  padding: 0 0.25rem;
  border: 0;
  background: none;
  color: var(--color-text-muted);
  font-size: var(--font-ui-body);
  cursor: pointer;
}

.attachment-remove:hover {
  color: var(--color-danger);
}
</style>
