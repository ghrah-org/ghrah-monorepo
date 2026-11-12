<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { ImageAttachment } from "@/composables/useAttachments";

const props = defineProps<{
  attachment: ImageAttachment;
  /** 单个附件的失败说明（i18n 后的文本）；正常时为 null。 */
  error?: string | null;
}>();
defineEmits<{ remove: [] }>();
const { t } = useI18n();

const sizeLabel = computed(() => {
  const bytes = props.attachment.size;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
});
</script>

<template>
  <div :class="['attachment-chip', { 'attachment-chip-error': error }]">
    <img :src="attachment.dataUrl" :alt="attachment.name" class="attachment-thumb" />
    <div class="attachment-meta">
      <span class="attachment-name" :title="attachment.name">{{ attachment.name }}</span>
      <span class="attachment-size">{{ sizeLabel }}</span>
      <span v-if="error" class="attachment-error" role="alert">{{ error }}</span>
    </div>
    <button
      type="button"
      class="attachment-remove"
      :aria-label="t('chat.attachment.remove')"
      @click="$emit('remove')"
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

.attachment-size {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.attachment-error {
  color: var(--color-danger);
  font-size: var(--font-ui-note);
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
