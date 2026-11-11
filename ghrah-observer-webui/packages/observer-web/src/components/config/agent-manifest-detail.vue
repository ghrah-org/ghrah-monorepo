<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type { AgentManifestInfo } from "@ghrah/observer-core";
import { useI18n } from "vue-i18n";
import UiBadge from "@/components/ui/badge.vue";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();

defineProps<{
  manifest: AgentManifestInfo | null;
}>();

defineEmits<{
  spawn: [];
  edit: [];
  delete: [];
}>();

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}
</script>

<template>
  <p v-if="!manifest" class="manifest-detail-empty">
    {{ t("config.agent.selectManifest") }}
  </p>
  <article v-else class="manifest-detail">
    <header class="manifest-detail-heading">
      <h3>{{ manifest.title || manifest.name }}</h3>
      <UiBadge>{{ manifest.namespace }}</UiBadge>
    </header>

    <p v-if="manifest.description" class="manifest-detail-description">
      {{ manifest.description }}
    </p>

    <dl class="manifest-detail-facts">
      <div>
        <dt>{{ t("config.agent.name") }}</dt>
        <dd>{{ manifest.name }}</dd>
      </div>
      <div>
        <dt>{{ t("config.agent.config") }}</dt>
        <dd>{{ manifest.endpoint_name || "—" }}</dd>
      </div>
      <div>
        <dt>{{ t("config.agent.temperature") }}</dt>
        <dd>—</dd>
      </div>
      <div>
        <dt>{{ t("config.agent.maxIterations") }}</dt>
        <dd>{{ manifest.max_iterations }}</dd>
      </div>
    </dl>

    <div v-if="manifest.tags.length > 0" class="manifest-detail-tags">
      <UiBadge v-for="tag in manifest.tags" :key="tag">{{ tag }}</UiBadge>
    </div>

    <section v-if="manifest.system_prompt" class="manifest-detail-block">
      <h4>{{ t("config.agent.systemPrompt") }}</h4>
      <pre>{{ truncate(manifest.system_prompt, 500) }}</pre>
    </section>

    <section v-if="manifest.ability_refs.length > 0" class="manifest-detail-block">
      <h4>{{ t("config.agent.abilities", { count: manifest.ability_refs.length }) }}</h4>
      <ul>
        <li v-for="ref in manifest.ability_refs" :key="ref">{{ ref }}</li>
      </ul>
    </section>

    <div class="manifest-detail-actions">
      <UiButton variant="primary" @click="$emit('spawn')">
        {{ t("config.spawn.title") }}
      </UiButton>
      <UiButton variant="secondary" @click="$emit('edit')">
        {{ t("config.agent.editYaml") }}
      </UiButton>
      <UiButton variant="danger" @click="$emit('delete')">
        {{ t("config.agent.delete") }}
      </UiButton>
    </div>
  </article>
</template>

<style scoped>
.manifest-detail-empty,
.manifest-detail {
  margin: 0;
  min-width: 0;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.manifest-detail-empty {
  padding: 1.5rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  text-align: center;
}

.manifest-detail {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.manifest-detail-heading,
.manifest-detail-tags,
.manifest-detail-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.manifest-detail-heading {
  justify-content: space-between;
}

.manifest-detail-heading h3,
.manifest-detail-description,
.manifest-detail-block h4,
.manifest-detail-facts {
  margin: 0;
}

.manifest-detail-heading h3 {
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.manifest-detail-description {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.manifest-detail-facts {
  display: grid;
  gap: 0.75rem 1rem;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
}

.manifest-detail-facts div {
  display: grid;
  gap: 0.125rem;
  min-width: 0;
}

.manifest-detail-facts dt {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.manifest-detail-facts dd,
.manifest-detail-block li {
  margin: 0;
  font-family: ui-monospace, monospace;
  font-size: var(--font-ui-code);
}

.manifest-detail-block {
  display: grid;
  gap: 0.25rem;
}

.manifest-detail-block h4 {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.manifest-detail-block pre,
.manifest-detail-block ul {
  margin: 0;
}

.manifest-detail-block pre {
  max-height: 8rem;
  padding: 0.5rem;
  overflow: auto;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  font-size: var(--font-ui-code);
  white-space: pre-wrap;
}

.manifest-detail-block ul {
  padding: 0;
  list-style: none;
}

.manifest-detail-block li + li {
  margin-top: 0.125rem;
}

.manifest-detail-actions {
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border);
}
</style>
