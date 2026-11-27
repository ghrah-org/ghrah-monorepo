<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { attachmentUrl, isVideoBlock, type ChatBlock } from "@ghrah/observer-core";
import type { ContentBlock } from "@ghrah/protocol";
import { useI18n } from "vue-i18n";
import { useMarkdown } from "@/composables/useMarkdown";

const props = defineProps<{ projectId: string; content: string; blocks?: readonly ChatBlock[] }>();
const { t } = useI18n();
const { render: renderMarkdown } = useMarkdown();

function imgSrc(block: Extract<ContentBlock, { type: "image" }>): string {
  if (block.url) return block.url;
  if (block.base64) return `data:${block.mime_type ?? "image/png"};base64,${block.base64}`;
  return "";
}

function audioSrc(block: Extract<ContentBlock, { type: "audio" }>): string {
  return `data:${block.mime_type};base64,${block.data}`;
}

function fileHref(block: Extract<ContentBlock, { type: "file" }>): string {
  if (block.url) return block.url;
  if (block.base64)
    return `data:${block.mime_type ?? "application/octet-stream"};base64,${block.base64}`;
  return "";
}

function fileLabel(block: Extract<ContentBlock, { type: "file" }>): string {
  return block.filename ?? "file";
}

function isText(block: ChatBlock): block is Extract<ContentBlock, { type: "text" }> {
  return block.type === "text";
}

function videoPoster(block: Extract<ChatBlock, { type: "video" }>): string {
  const frame = block.frames?.[0];
  if (!frame?.base64) return "";
  return `data:${frame.mime_type ?? "image/jpeg"};base64,${frame.base64}`;
}

function videoSrc(block: Extract<ChatBlock, { type: "video" }>): string {
  if (block.attachment_id) return attachmentUrl(props.projectId, block.attachment_id);
  return block.url ?? "";
}
</script>

<template>
  <div class="room-entry-content">
    <!-- 富块渲染 -->
    <template v-if="blocks?.length">
      <div v-for="(block, j) in blocks" :key="j" :class="['entry-block', `block-${block.type}`]">
        <div v-if="isText(block)" class="markdown-body" v-html="renderMarkdown(block.text)" />
        <details v-else-if="block.type === 'reasoning'" class="block-reasoning">
          <summary class="text-xs italic">
            {{ t("chat.reasoning") }}{{ block.incomplete ? "…" : "" }}
          </summary>
          <pre class="text-xs whitespace-pre-wrap">{{ block.reasoning }}</pre>
        </details>
        <img
          v-else-if="block.type === 'image'"
          :src="imgSrc(block)"
          alt="image"
          class="max-w-full rounded"
        />
        <audio v-else-if="block.type === 'audio'" :src="audioSrc(block)" controls class="w-full" />
        <a
          v-else-if="block.type === 'file'"
          :href="fileHref(block)"
          :download="fileLabel(block)"
          class="text-xs underline"
        >
          📎 {{ fileLabel(block) }}
        </a>
        <template v-else-if="isVideoBlock(block)">
          <video
            v-if="videoSrc(block)"
            :src="videoSrc(block)"
            :poster="videoPoster(block) || undefined"
            controls
            preload="metadata"
            class="max-w-full rounded"
          />
          <div v-else class="block-video-fallback">
            <img
              v-if="videoPoster(block)"
              :src="videoPoster(block)"
              :alt="block.filename ?? t('chat.video')"
              class="max-w-full rounded"
            />
            <span class="block-video-error">
              {{ t("chat.videoUnavailable", { name: block.filename ?? t("chat.video") }) }}
            </span>
          </div>
        </template>
        <details v-else-if="block.type === 'tool_call'" class="block-tool">
          <summary class="text-xs">🔧 {{ block.name }}</summary>
          <pre class="text-xs whitespace-pre-wrap">{{ block.arguments }}</pre>
        </details>
        <details v-else-if="block.type === 'tool_result'" class="block-tool">
          <summary :class="{ 'block-error': !block.success }" class="text-xs">
            ↳ {{ block.name ?? block.tool_call_id }}{{ block.success ? "" : t("chat.failed") }}
          </summary>
          <pre class="text-xs whitespace-pre-wrap">{{ block.content }}</pre>
          <pre v-if="block.error" class="text-xs block-error whitespace-pre-wrap">{{
            block.error
          }}</pre>
        </details>
        <div v-else-if="block.type === 'error'" class="block-error">
          <span class="text-xs">{{ block.error_type }}: {{ block.message }}</span>
        </div>
      </div>
    </template>

    <!-- 无块：纯 content（与富块 text 同走 Markdown 渲染） -->
    <div v-else class="markdown-body" v-html="renderMarkdown(content)" />
  </div>
</template>

<style scoped>
.room-entry-content {
  overflow-wrap: anywhere;
}
.entry-block,
.block-tool,
.block-reasoning {
  margin-block: 0.125rem;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.block-error {
  color: var(--color-danger);
}
.markdown-body :deep(p) {
  margin: 0.125rem 0;
}
.markdown-body :deep(pre) {
  background: var(--color-surface-2);
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.875rem;
  overflow-x: auto;
}

/* 列表：UnoCSS preflight 会重置 list-style 与缩进，此处恢复 */
.markdown-body :deep(ul) {
  margin: 0.25rem 0;
  padding-left: 1.25rem;
  list-style-type: disc;
}
.markdown-body :deep(ol) {
  margin: 0.25rem 0;
  padding-left: 1.25rem;
  list-style-type: decimal;
}
.markdown-body :deep(li) {
  margin: 0.125rem 0;
}

/* 行内 code（pre 内的 code 由 pre 自身提供背景，去重） */
.markdown-body :deep(code) {
  background: var(--color-surface-2);
  border-radius: 0.25rem;
  padding: 0 0.25rem;
  font-size: 0.875rem;
}
.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.markdown-body :deep(a) {
  text-decoration: underline;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 600;
  margin: 0.25rem 0;
}
.markdown-body :deep(h1) {
  font-size: 1.125rem;
}
.markdown-body :deep(h2) {
  font-size: 1rem;
}
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-size: 0.9375rem;
}

.markdown-body :deep(blockquote) {
  margin: 0.25rem 0;
  padding-left: 0.5rem;
  border-left: 3px solid var(--color-border-strong);
}

.markdown-body :deep(table) {
  border-collapse: collapse;
  margin: 0.25rem 0;
  font-size: 0.875rem;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid var(--color-border-strong);
  padding: 0.25rem 0.5rem;
}
:global(:root.dark) .markdown-body :deep(th),
.block-video-fallback {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.block-video-error {
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}
</style>
