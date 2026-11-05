<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useMarkdown } from "@/composables/useMarkdown";
import type { PayloadBlock } from "./render-payload.js";

const { t } = useI18n();
const { render: renderMarkdown } = useMarkdown();

defineProps<{
  block: PayloadBlock;
}>();

/** collapsed-json 的 <details> 折叠输出。 */
function jsonSource(block: Extract<PayloadBlock, { kind: "collapsed-json" }>): string {
  return typeof block.data === "string" ? block.data : JSON.stringify(block.data, null, 2);
}

/** 截断提示的已显示字符数（按 kind 取对应内容长度）。 */
function truncationLength(block: PayloadBlock): number {
  if (!("truncated" in block) || !block.truncated) return 0;
  switch (block.kind) {
    case "markdown":
    case "quote":
      return block.source.length;
    case "file-content":
      return block.content.length;
    case "table":
      return block.rows.length;
    case "terminal":
      return block.output.length;
    case "collapsed-json":
      return typeof block.data === "string" ? block.data.length : 0;
    default:
      return 0;
  }
}
</script>

<template>
  <div class="ac-block" :class="`ac-block--${block.kind}`">
    <!-- 参数/通用 kv 表 -->
    <template v-if="block.kind === 'kv-table'">
      <div v-if="block.title" class="ac-block-title">{{ block.title }}</div>
      <table v-if="block.rows.length > 0" class="ac-kv-table">
        <tbody>
          <tr v-for="(row, i) in block.rows" :key="i">
            <th class="ac-kv-key">{{ row.key }}</th>
            <td class="ac-kv-value" :class="{ 'ac-kv-value--mono': row.structured }">
              <span v-if="row.structured" class="ac-kv-json">{{ row.value }}</span>
              <template v-else>{{ row.value }}</template>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 目录列表 -->
    <table v-else-if="block.kind === 'table'" class="ac-table">
      <thead>
        <tr>
          <th v-for="col in block.columns" :key="col" class="ac-table-col" scope="col">
            {{ col }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in block.rows" :key="i">
          <td v-for="(cell, j) in row" :key="j" class="ac-table-cell">{{ cell }}</td>
        </tr>
      </tbody>
    </table>

    <!-- 终端输出 -->
    <div
      v-else-if="block.kind === 'terminal'"
      class="ac-terminal"
      :class="{ 'ac-terminal--fail': !block.success }"
    >
      <div v-if="block.command" class="ac-terminal-command">{{ block.command }}</div>
      <pre class="ac-terminal-output">{{ block.output }}</pre>
    </div>

    <!-- 文件内容 -->
    <div v-else-if="block.kind === 'file-content'" class="ac-file-content">
      <div v-if="block.filename" class="ac-file-name">{{ block.filename }}</div>
      <pre class="ac-file-body">{{ block.content }}</pre>
    </div>

    <!-- markdown（useMarkdown：html:false + 链接协议白名单，无新渲染路径） -->
    <div
      v-else-if="block.kind === 'markdown'"
      class="markdown-body ac-markdown"
      v-html="renderMarkdown(block.source)"
    />

    <!-- reasoning 引用样式 -->
    <blockquote v-else-if="block.kind === 'quote'" class="ac-quote">
      {{ block.source }}
    </blockquote>

    <!-- 媒体预览占位（image/audio/file） -->
    <div v-else-if="block.kind === 'media-preview'" class="ac-media">
      <img
        v-if="block.url && block.mime.startsWith('image/')"
        class="ac-media-image"
        :src="block.url"
        :alt="block.filename ?? block.mime"
      />
      <div v-else class="ac-media-placeholder">
        <span class="ac-media-badge">{{ block.mime }}</span>
        <span v-if="block.filename" class="ac-media-name">{{ block.filename }}</span>
        <span class="ac-media-hint">{{ t("actionChain.detailMediaPreview") }}</span>
      </div>
    </div>

    <!-- 错误卡片 -->
    <div v-else-if="block.kind === 'error-card'" class="ac-error-card" role="alert">
      <div class="ac-error-head">
        <span class="ac-error-type">{{ block.errorType }}</span>
        <span class="ac-error-message">{{ block.message }}</span>
      </div>
      <pre v-if="block.details != null" class="ac-error-details">{{
        JSON.stringify(block.details, null, 2)
      }}</pre>
    </div>

    <!-- 未知结构 JSON 折叠 -->
    <details v-else-if="block.kind === 'collapsed-json'" class="ac-json">
      <summary class="ac-json-summary">
        <span class="ac-json-label">{{ t("actionChain.detailUnknownData") }}</span>
      </summary>
      <pre class="ac-json-body">{{ jsonSource(block) }}</pre>
    </details>

    <!-- 截断提示（各内容型 block 附注） -->
    <div v-if="'truncated' in block && block.truncated" class="ac-truncated">
      {{ t("actionChain.detailTruncated", { length: truncationLength(block) }) }}
    </div>
  </div>
</template>

<style scoped>
/* markdown v-html 子树（非编译期模板内容，需 :deep 穿透） */
.ac-markdown :deep(p) {
  margin: 0 0 0.25rem;
}

.ac-markdown :deep(pre) {
  margin: 0.25rem 0;
  padding: 0.375rem 0.5rem;
  border-radius: 0.25rem;
  background: var(--ac-lane-guide);
  overflow-x: auto;
}

.ac-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--font-ui-code);
}

.ac-markdown :deep(a) {
  color: var(--ac-read);
}
</style>
