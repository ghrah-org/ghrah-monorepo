<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
/**
 * 实例设置 → Agent 模板。
 *
 * 卡片网格展示模板（默认每个模板只显示最新版本），点卡片看详情与版本，
 * 卡片上直接可以「创建 Agent / 编辑 / 归档」。
 */
import type {
  AgentTemplateDetailView,
  AgentTemplateRevisionSummaryView,
  AgentTemplateSummaryView,
} from "@ghrah/protocol";
import { createAgentTemplatesApi, HttpClient, useConnectionStore } from "@ghrah/observer-core";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import AgentTemplateCard from "@/components/config/agent-template-card.vue";
import AgentTemplateDialog from "@/components/config/agent-template-dialog.vue";
import CreateAgentFromTemplateDialog from "@/components/config/create-agent-from-template-dialog.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import UiButton from "@/components/ui/button.vue";

const { t } = useI18n();
const connection = useConnectionStore();
const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();

const loading = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const templates = ref<AgentTemplateSummaryView[]>([]);
const selected = ref<AgentTemplateDetailView | null>(null);
const versions = ref<AgentTemplateRevisionSummaryView[]>([]);
const includeArchived = ref(false);
const includeHistory = ref(false);
const search = ref("");
const dialogOpen = ref(false);
const dialogTarget = ref<AgentTemplateDetailView | null>(null);
/** 创建 Agent 只需要模板身份与版本，详情与摘要都能满足。 */
interface TemplateRef {
  template_id: string;
  revision: number;
  metadata: { namespace: string; name: string };
}
const spawnTarget = ref<TemplateRef | null>(null);
const archiveTarget = ref<{ template_id: string; revision: number } | null>(null);

function api() {
  return createAgentTemplatesApi(
    new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken }),
  );
}

const visible = computed(() => {
  const keyword = search.value.trim().toLowerCase();
  if (keyword === "") return templates.value;
  return templates.value.filter((row) => {
    const haystack =
      `${row.metadata.namespace}.${row.metadata.name} ${row.metadata.title} ${row.metadata.tags.join(" ")}`.toLowerCase();
    return haystack.includes(keyword);
  });
});

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
      const stillThere = list.templates.some(
        (row) => row.template_id === selected.value?.template_id,
      );
      if (!stillThere) {
        selected.value = null;
        versions.value = [];
      }
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
    selected.value = await api().get(templateId);
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

function openEdit(template: AgentTemplateSummaryView) {
  void (async () => {
    try {
      selected.value = await api().get(template.template_id);
      dialogTarget.value = selected.value;
      dialogOpen.value = true;
      emit("dirtyChange", true);
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : t("config.template.loadFailed");
    }
  })();
}

function closeDialog() {
  dialogOpen.value = false;
  dialogTarget.value = null;
  emit("dirtyChange", false);
}

async function onSaved() {
  closeDialog();
  await refresh();
  if (selected.value) await select(selected.value.template_id);
}

function openSpawn(template: TemplateRef) {
  spawnTarget.value = template;
}

function onSpawned(agentName: string, projectName: string) {
  spawnTarget.value = null;
  notice.value = t("config.template.spawned", { agent: agentName, project: projectName });
  void refresh();
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

function titleOf(row: { metadata: AgentTemplateSummaryView["metadata"] }): string {
  const title = row.metadata.title.trim();
  return title === "" ? `${row.metadata.namespace}.${row.metadata.name}` : title;
}

onMounted(refresh);
</script>

<template>
  <section class="templates">
    <header class="templates-head">
      <div class="templates-heading">
        <h2>{{ t("config.template.title") }}</h2>
        <p class="templates-hint">{{ t("config.template.hint") }}</p>
      </div>
      <div class="templates-actions">
        <UiButton variant="ghost" :disabled="loading" @click="refresh">
          {{ t("config.template.refresh") }}
        </UiButton>
        <UiButton variant="primary" @click="openCreate">
          {{ t("config.template.create") }}
        </UiButton>
      </div>
    </header>

    <div class="templates-toolbar">
      <input
        v-model="search"
        class="templates-search"
        type="search"
        :placeholder="t('config.template.search')"
        :aria-label="t('config.template.search')"
      />
      <label class="templates-check">
        <input v-model="includeArchived" type="checkbox" @change="refresh" />
        {{ t("config.template.showArchived") }}
      </label>
      <label class="templates-check">
        <input v-model="includeHistory" type="checkbox" @change="refresh" />
        {{ t("config.template.showHistory") }}
      </label>
    </div>

    <p v-if="error" class="templates-alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="templates-notice">{{ notice }}</p>

    <p v-if="loading" class="templates-state">{{ t("common.loading") }}</p>
    <p v-else-if="templates.length === 0" class="templates-state">
      {{ t("config.template.empty") }}
    </p>
    <p v-else-if="visible.length === 0" class="templates-state">
      {{ t("config.template.noMatch") }}
    </p>

    <ul v-else class="templates-grid">
      <li v-for="row in visible" :key="`${row.template_id}-${row.revision}`">
        <AgentTemplateCard
          :template="row"
          :selected="selected?.template_id === row.template_id"
          @select="select(row.template_id)"
          @spawn="openSpawn(row)"
          @edit="openEdit(row)"
          @archive="archiveTarget = { template_id: row.template_id, revision: row.revision }"
        />
      </li>
    </ul>

    <section v-if="selected" class="templates-detail">
      <header class="templates-detail-head">
        <div>
          <h3>{{ titleOf(selected) }}</h3>
          <p class="templates-detail-sub">
            {{ selected.metadata.namespace }}.{{ selected.metadata.name }} · v{{
              selected.revision
            }}
            <span v-if="selected.is_latest">· {{ t("config.template.latestTag") }}</span>
          </p>
        </div>
        <div class="templates-actions">
          <UiButton
            variant="primary"
            :disabled="Boolean(selected.archived_at)"
            @click="openSpawn(selected)"
          >
            {{ t("config.template.spawn") }}
          </UiButton>
          <UiButton
            variant="secondary"
            @click="
              dialogTarget = selected;
              dialogOpen = true;
            "
          >
            {{ t("config.template.edit") }}
          </UiButton>
        </div>
      </header>

      <div class="templates-facts">
        <div class="templates-fact">
          <span class="templates-fact-label">{{ t("config.template.factModel") }}</span>
          <span class="templates-fact-value">
            {{ selected.model.endpoint_name ?? t("config.template.endpointUnset") }}
          </span>
        </div>
        <div class="templates-fact">
          <span class="templates-fact-label">{{ t("config.template.factContext") }}</span>
          <span class="templates-fact-value">
            {{ t("config.template.factIterations") }}:
            {{ selected.context_policy.max_iterations }} · {{ t("config.template.factTimeout") }}:
            {{ selected.context_policy.communication_timeout }}s
          </span>
        </div>
        <div class="templates-fact">
          <span class="templates-fact-label">{{ t("config.template.references") }}</span>
          <span class="templates-fact-value">{{ selected.reference_count }}</span>
        </div>
      </div>

      <div class="templates-block">
        <h4>{{ t("config.template.factPackages") }}</h4>
        <p v-if="selected.ability_request.packages.length === 0" class="templates-muted">
          {{ t("config.template.noPackages") }}
        </p>
        <ul v-else class="templates-packages">
          <li v-for="item in selected.ability_request.packages" :key="item.package">
            <strong>{{ item.package }}</strong>
            <span v-if="item.whole_container"> · {{ t("config.template.wholeContainer") }}</span>
            <span v-else> · {{ item.operations.map((op) => op.key).join(", ") || "—" }} </span>
          </li>
        </ul>
      </div>

      <div class="templates-block">
        <h4>{{ t("config.template.factPrompt") }}</h4>
        <pre class="templates-prompt">{{ selected.prompt.system_prompt || "—" }}</pre>
      </div>

      <div class="templates-block">
        <h4>{{ t("config.template.versions") }}</h4>
        <ul class="templates-versions">
          <li v-for="item in versions" :key="item.revision">
            <span class="templates-version">v{{ item.revision }}</span>
            <span v-if="item.is_latest" class="templates-version-tag">
              {{ t("config.template.latestTag") }}
            </span>
            <span class="templates-muted">{{ item.created_at.slice(0, 10) }}</span>
            <span class="templates-muted">
              {{ t("config.template.references") }}: {{ item.reference_count }}
            </span>
          </li>
        </ul>
      </div>
    </section>

    <AgentTemplateDialog
      v-if="dialogOpen"
      :template="dialogTarget"
      @close="closeDialog"
      @saved="onSaved"
      @dirty-change="emit('dirtyChange', $event)"
    />

    <CreateAgentFromTemplateDialog
      v-if="spawnTarget"
      :template="spawnTarget"
      @close="spawnTarget = null"
      @created="onSpawned"
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
.templates {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1rem;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.templates-head,
.templates-detail-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem 1rem;
}

.templates-heading {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
}

.templates-head h2,
.templates-detail-head h3 {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.templates-hint,
.templates-detail-sub {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.templates-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.templates-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem 1rem;
}

.templates-search {
  flex: 1 1 14rem;
  max-width: 22rem;
  padding: 0.4375rem 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-size: var(--font-ui-note);
}

.templates-check {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.templates-alert,
.templates-notice,
.templates-state {
  margin: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.templates-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.templates-notice {
  border-color: var(--color-accent);
  background: color-mix(in srgb, var(--color-accent) 10%, var(--color-surface));
  color: var(--color-text);
}

.templates-state {
  background: var(--color-surface);
  color: var(--color-text-muted);
  text-align: center;
}

.templates-grid {
  display: grid;
  margin: 0;
  padding: 0;
  gap: 0.75rem;
  grid-template-columns: repeat(auto-fill, minmax(19rem, 1fr));
  list-style: none;
}

.templates-detail {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.templates-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1.5rem;
}

.templates-fact {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.templates-fact-label {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.templates-fact-value {
  font-size: var(--font-ui-note);
}

.templates-block {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.templates-block h4 {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.templates-muted {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.templates-packages,
.templates-versions {
  display: flex;
  margin: 0;
  padding: 0;
  flex-direction: column;
  gap: 0.25rem;
  font-size: var(--font-ui-note);
  list-style: none;
}

.templates-versions li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.templates-version {
  font-variant-numeric: tabular-nums;
}

.templates-version-tag {
  color: var(--color-accent);
}

.templates-prompt {
  margin: 0;
  padding: 0.625rem 0.75rem;
  max-height: 14rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  font-family: var(--font-mono, ui-monospace);
  font-size: var(--font-ui-note);
  line-height: 1.5;
  overflow: auto;
  white-space: pre-wrap;
}
</style>
