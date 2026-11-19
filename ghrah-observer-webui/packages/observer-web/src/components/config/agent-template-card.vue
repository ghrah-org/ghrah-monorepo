<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
/** 模板卡片：一眼看清"是什么模板、第几版、被几个 Agent 用"，操作直接可点。 */
import type { AgentTemplateSummaryView } from "@ghrah/protocol";
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

const props = defineProps<{
  template: AgentTemplateSummaryView;
  selected: boolean;
}>();
const emit = defineEmits<{
  select: [];
  spawn: [];
  edit: [];
  archive: [];
}>();

const { t } = useI18n();

const title = computed(() => {
  const value = props.template.metadata.title.trim();
  return value === ""
    ? `${props.template.metadata.namespace}.${props.template.metadata.name}`
    : value;
});

const identity = computed(
  () => `${props.template.metadata.namespace}.${props.template.metadata.name}`,
);

const tags = computed(() => props.template.metadata.tags.filter(Boolean));

const archived = computed(() => props.template.archived_at !== null);
</script>

<template>
  <article
    class="template-card"
    :class="{ 'is-selected': selected, 'is-archived': archived }"
    tabindex="0"
    role="button"
    :aria-pressed="selected"
    @click="emit('select')"
    @keydown.enter.prevent="emit('select')"
    @keydown.space.prevent="emit('select')"
  >
    <header class="template-card-head">
      <div class="template-card-titles">
        <h3>{{ title }}</h3>
        <p v-if="title !== identity" class="template-card-identity">{{ identity }}</p>
      </div>
      <span class="template-card-version">v{{ template.revision }}</span>
    </header>

    <div class="template-card-badges">
      <span v-if="template.is_latest" class="template-card-badge is-accent">
        {{ t("config.template.latestTag") }}
      </span>
      <span v-if="template.metadata.deprecated" class="template-card-badge">
        {{ t("config.template.deprecatedTag") }}
      </span>
      <span v-if="archived" class="template-card-badge">
        {{ t("config.template.archivedTag") }}
      </span>
      <span v-for="tag in tags" :key="tag" class="template-card-badge">{{ tag }}</span>
    </div>

    <p class="template-card-meta">
      <span>{{ t("config.template.references") }}: {{ template.reference_count }}</span>
      <span>{{ t("config.template.updatedAt") }}: {{ template.updated_at.slice(0, 10) }}</span>
    </p>

    <footer class="template-card-actions" @click.stop>
      <UiButton variant="primary" size="sm" :disabled="archived" @click="emit('spawn')">
        {{ t("config.template.spawn") }}
      </UiButton>
      <UiButton variant="secondary" size="sm" :disabled="archived" @click="emit('edit')">
        {{ t("config.template.edit") }}
      </UiButton>
      <UiButton variant="ghost" size="sm" :disabled="archived" @click="emit('archive')">
        {{ t("config.template.archive") }}
      </UiButton>
    </footer>
  </article>
</template>

<style scoped>
.template-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.625rem;
  padding: 0.875rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
  transition:
    border-color 120ms ease,
    background 120ms ease;
}

.template-card:hover,
.template-card:focus-visible {
  border-color: var(--color-accent);
  outline: none;
}

.template-card.is-selected {
  border-color: var(--color-accent);
  box-shadow: inset 0 0 0 1px var(--color-accent);
}

.template-card.is-archived {
  opacity: 0.72;
}

.template-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.template-card-titles {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.125rem;
}

.template-card-titles h3 {
  margin: 0;
  overflow: hidden;
  font-size: var(--font-ui-body);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-card-identity {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  overflow-wrap: anywhere;
}

.template-card-version {
  flex: none;
  padding: 0.0625rem 0.375rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-variant-numeric: tabular-nums;
}

.template-card-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  min-height: 1.25rem;
}

.template-card-badge {
  padding: 0 0.375rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.template-card-badge.is-accent {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.template-card-meta {
  display: flex;
  flex-wrap: wrap;
  margin: 0;
  gap: 0.25rem 0.875rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.template-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  padding-top: 0.125rem;
}
</style>
