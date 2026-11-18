<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
/**
 * 实例设置 → Agent 模板：模板库的唯一管理面。
 *
 * 模板属于实例（Subject 的 `agent_templates` 表），不走项目。列表默认只返回每个
 * 模板的最新版本；勾选「显示历史版本」才列出全部版本行。
 */
import type {
  AgentTemplateDetailView,
  AgentTemplateRevisionSummaryView,
  AgentTemplateSummaryView,
} from "@ghrah/protocol";
import { createAgentTemplatesApi, HttpClient, useConnectionStore } from "@ghrah/observer-core";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import AgentTemplateDialog from "@/components/config/agent-template-dialog.vue";
import UiButton from "@/components/ui/button.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";

const { t } = useI18n();
const connection = useConnectionStore();
const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();

const loading = ref(false);
const error = ref<string | null>(null);
const templates = ref<AgentTemplateSummaryView[]>([]);
const selected = ref<AgentTemplateDetailView | null>(null);
const versions = ref<AgentTemplateRevisionSummaryView[]>([]);
const includeArchived = ref(false);
const includeHistory = ref(false);
const dialogOpen = ref(false);
const dialogTarget = ref<AgentTemplateDetailView | null>(null);
const archiveTarget = ref<{ template_id: string; revision: number } | null>(null);

function api() {
  return createAgentTemplatesApi(
    new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken }),
  );
}

const hasTemplates = computed(() => templates.value.length > 0);

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    const list = await api().list({
      includeArchived: includeArchived.value,
      includeHistory: includeHistory.value,
    });
    templates.value = list.templates;
    if (selected.value) {
      await select(selected.value.template_id);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.loadFailed");
  } finally {
    loading.value = false;
  }
}

async function select(templateId: string) {
  if (selected.value?.template_id === templateId) {
    selected.value = null;
    versions.value = [];
    return;
  }
  try {
    const detail = await api().get(templateId);
    selected.value = detail;
    const list = await api().versions(templateId);
    versions.value = list.revisions;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.loadFailed");
  }
}

function openCreate() {
  dialogTarget.value = null;
  dialogOpen.value = true;
  emit("dirtyChange", true);
}

function openEdit() {
  if (!selected.value) return;
  dialogTarget.value = selected.value;
  dialogOpen.value = true;
  emit("dirtyChange", true);
}

function closeDialog() {
  dialogOpen.value = false;
  dialogTarget.value = null;
  emit("dirtyChange", false);
}

async function onSaved() {
  closeDialog();
  await refresh();
}

async function confirmArchive() {
  const target = archiveTarget.value;
  archiveTarget.value = null;
  if (!target) return;
  loading.value = true;
  error.value = null;
  try {
    await api().archive(target.template_id, target.revision);
    if (selected.value?.template_id === target.template_id) {
      selected.value = null;
      versions.value = [];
    }
    await refresh();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.archiveFailed");
  } finally {
    loading.value = false;
  }
}

function label(row: { metadata: AgentTemplateSummaryView["metadata"] }): string {
  const title = row.metadata.title.trim();
  return title === ""
    ? `${row.metadata.namespace}.${row.metadata.name}`
    : `${title} · ${row.metadata.namespace}.${row.metadata.name}`;
}

onMounted(refresh);
</script>

<template>
  <section class="template-page">
    <header class="template-page-header">
      <div class="template-page-heading">
        <h2>{{ t("config.template.title") }}</h2>
        <p class="template-page-hint">{{ t("config.template.hint") }}</p>
      </div>
      <div class="template-page-actions">
        <UiButton variant="ghost" :disabled="loading" @click="refresh">
          {{ t("config.template.refresh") }}
        </UiButton>
        <UiButton variant="primary" @click="openCreate">
          {{ t("config.template.create") }}
        </UiButton>
      </div>
    </header>

    <div class="template-page-filters">
      <label class="template-page-check">
        <input v-model="includeArchived" type="checkbox" @change="refresh" />
        {{ t("config.template.showArchived") }}
      </label>
      <label class="template-page-check">
        <input v-model="includeHistory" type="checkbox" @change="refresh" />
        {{ t("config.template.showHistory") }}
      </label>
    </div>

    <p v-if="error" class="template-page-alert" role="alert">{{ error }}</p>

    <p v-if="loading" class="template-page-state">{{ t("common.loading") }}</p>
    <p v-else-if="!hasTemplates" class="template-page-state">
      {{ t("config.template.empty") }}
    </p>

    <table v-else class="template-page-table">
      <thead>
        <tr>
          <th scope="col">{{ t("config.template.columnTemplate") }}</th>
          <th scope="col">{{ t("config.template.columnRevision") }}</th>
          <th scope="col">{{ t("config.template.columnReferences") }}</th>
          <th scope="col">{{ t("config.template.columnActions") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in templates"
          :key="`${row.template_id}-${row.revision}`"
          :class="{ 'is-selected': selected?.template_id === row.template_id }"
        >
          <td>
            <button type="button" class="template-page-link" @click="select(row.template_id)">
              {{ label(row) }}
            </button>
            <span v-if="row.metadata.deprecated" class="template-page-tag">
              {{ t("config.template.deprecatedTag") }}
            </span>
            <span v-if="row.archived_at" class="template-page-tag">
              {{ t("config.template.archivedTag") }}
            </span>
          </td>
          <td>
            v{{ row.revision }}
            <span v-if="row.is_latest" class="template-page-tag is-latest">
              {{ t("config.template.latestTag") }}
            </span>
          </td>
          <td>{{ row.reference_count }}</td>
          <td class="template-page-cell-actions">
            <UiButton
              variant="ghost"
              :disabled="Boolean(row.archived_at)"
              @click="archiveTarget = { template_id: row.template_id, revision: row.revision }"
            >
              {{ t("config.template.archive") }}
            </UiButton>
          </td>
        </tr>
      </tbody>
    </table>

    <section v-if="selected" class="template-page-detail">
      <header class="template-page-detail-header">
        <h3>{{ label(selected) }}</h3>
        <div class="template-page-actions">
          <UiButton variant="secondary" @click="openEdit">
            {{ t("config.template.edit") }}
          </UiButton>
          <UiButton
            variant="danger"
            :disabled="Boolean(selected.archived_at)"
            @click="archiveTarget = selected"
          >
            {{ t("config.template.archive") }}
          </UiButton>
        </div>
      </header>

      <dl class="template-page-facts">
        <dt>{{ t("config.template.factModel") }}</dt>
        <dd>{{ selected.model.endpoint_name ?? t("config.template.endpointUnset") }}</dd>
        <dt>{{ t("config.template.factPrompt") }}</dt>
        <dd class="template-page-pre">{{ selected.prompt.system_prompt }}</dd>
        <dt>{{ t("config.template.factContext") }}</dt>
        <dd>
          {{ t("config.template.factIterations") }}: {{ selected.context_policy.max_iterations }} ·
          {{ t("config.template.factTimeout") }}:
          {{ selected.context_policy.communication_timeout }}
        </dd>
        <dt>{{ t("config.template.factPackages") }}</dt>
        <dd>
          <span v-if="selected.ability_request.packages.length === 0">
            {{ t("config.template.noPackages") }}
          </span>
          <ul v-else class="template-page-packages">
            <li v-for="item in selected.ability_request.packages" :key="item.package">
              {{ item.package }}
              <span v-if="item.whole_container">({{ t("config.template.wholeContainer") }})</span>
              <span v-else> · {{ item.operations.map((op) => op.key).join(", ") || "—" }} </span>
            </li>
          </ul>
        </dd>
      </dl>

      <h4 class="template-page-versions-title">{{ t("config.template.versions") }}</h4>
      <ul class="template-page-versions">
        <li v-for="item in versions" :key="item.revision">
          v{{ item.revision }}
          <span v-if="item.is_latest" class="template-page-tag is-latest">
            {{ t("config.template.latestTag") }}
          </span>
          · {{ item.created_at }} · {{ t("config.template.references") }}:
          {{ item.reference_count }}
        </li>
      </ul>
    </section>

    <AgentTemplateDialog
      v-if="dialogOpen"
      :template="dialogTarget"
      @close="closeDialog"
      @saved="onSaved"
      @dirty-change="emit('dirtyChange', $event)"
    />

    <ConfirmDialog
      v-if="archiveTarget"
      :title="t('config.template.archiveTitle')"
      :message="t('config.template.archiveConfirm')"
      :confirm-label="t('config.template.archive')"
      danger
      @cancel="archiveTarget = null"
      @confirm="confirmArchive"
    />
  </section>
</template>

<style scoped>
.template-page {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1rem;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.template-page-header,
.template-page-detail-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem 1rem;
}

.template-page-heading {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
}

.template-page-header h2,
.template-page-detail-header h3 {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.template-page-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.template-page-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.template-page-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}

.template-page-check {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: var(--font-ui-note);
}

.template-page-alert,
.template-page-state {
  margin: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.template-page-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.template-page-state {
  background: var(--color-surface);
  color: var(--color-text-muted);
  text-align: center;
}

.template-page-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-ui-note);
}

.template-page-table th,
.template-page-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  vertical-align: middle;
}

.template-page-table th {
  color: var(--color-text-muted);
  font-weight: 600;
}

.template-page-table tr.is-selected {
  background: color-mix(in srgb, var(--color-accent) 10%, transparent);
}

.template-page-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.template-page-link:hover {
  color: var(--color-accent);
}

.template-page-tag {
  margin-left: 0.5rem;
  padding: 0 0.375rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.template-page-tag.is-latest {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.template-page-cell-actions {
  white-space: nowrap;
}

.template-page-detail {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.template-page-facts {
  display: grid;
  margin: 0;
  gap: 0.375rem 0.75rem;
  grid-template-columns: max-content 1fr;
  font-size: var(--font-ui-note);
}

.template-page-facts dt {
  color: var(--color-text-muted);
}

.template-page-facts dd {
  margin: 0;
}

.template-page-pre {
  white-space: pre-wrap;
}

.template-page-packages,
.template-page-versions {
  display: flex;
  margin: 0;
  padding: 0;
  flex-direction: column;
  gap: 0.25rem;
  list-style: none;
}

.template-page-versions-title {
  margin: 0;
  font-size: var(--font-ui-note);
  font-weight: 600;
}
</style>
