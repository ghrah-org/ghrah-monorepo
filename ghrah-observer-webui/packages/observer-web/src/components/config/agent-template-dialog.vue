<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import type {
  AgentTemplateCreateRequest,
  AgentTemplateDetailView,
  AgentTemplateUpdateRequest,
} from "@ghrah/protocol";
import { createAgentTemplatesApi, HttpClient, useConnectionStore } from "@ghrah/observer-core";
import { computed, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

const props = defineProps<{ template: AgentTemplateDetailView | null }>();
const emit = defineEmits<{
  close: [];
  saved: [template: AgentTemplateDetailView];
  dirtyChange: [dirty: boolean];
}>();

const { t } = useI18n();
const connection = useConnectionStore();

interface PackageRow {
  package: string;
  operations: string;
  wholeContainer: boolean;
}

const mode = computed(() => (props.template === null ? "create" : "edit"));
const loading = ref(false);
const error = ref<string | null>(null);

const form = reactive({
  namespace: props.template?.metadata.namespace ?? "",
  name: props.template?.metadata.name ?? "",
  title: props.template?.metadata.title ?? "",
  description: props.template?.metadata.description ?? "",
  tags: (props.template?.metadata.tags ?? []).join(", "),
  deprecated: props.template?.metadata.deprecated ?? false,
  endpointName: props.template?.model.endpoint_name ?? "",
  systemPrompt: props.template?.prompt.system_prompt ?? "",
  maxIterations: props.template?.context_policy.max_iterations ?? -1,
  communicationTimeout: props.template?.context_policy.communication_timeout ?? 300,
  packages: (props.template?.ability_request.packages ?? []).map((item) => ({
    package: item.package,
    operations: item.operations.map((op) => op.key).join(", "),
    wholeContainer: item.whole_container,
  })) as PackageRow[],
});

function api() {
  return createAgentTemplatesApi(
    new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken }),
  );
}

const canSubmit = computed(() => {
  if (mode.value === "create" && (form.namespace.trim() === "" || form.name.trim() === "")) {
    return false;
  }
  if (form.endpointName.trim() === "") return false;
  return form.packages.every((row) => row.package.trim() !== "");
});

function addPackage() {
  form.packages.push({ package: "", operations: "", wholeContainer: false });
}

function removePackage(index: number) {
  form.packages.splice(index, 1);
}

function buildPackages(): NonNullable<AgentTemplateCreateRequest["ability_request"]>["packages"] {
  return form.packages
    .filter((row) => row.package.trim() !== "")
    .map((row) => ({
      package: row.package.trim(),
      origin: "builtin" as const,
      whole_container: row.wholeContainer,
      operations: row.wholeContainer
        ? []
        : row.operations
            .split(",")
            .map((key) => key.trim())
            .filter(Boolean)
            .map((key) => ({ key, visible: true, available: true })),
      skills: [],
    }));
}

function splitTags(): string[] {
  return form.tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

async function submit() {
  if (loading.value || !canSubmit.value) return;
  loading.value = true;
  error.value = null;
  try {
    const detail =
      mode.value === "create"
        ? await api().create(buildCreatePayload())
        : await api().update(props.template!.template_id, buildUpdatePayload());
    emit("dirtyChange", false);
    emit("saved", detail);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.template.saveFailed");
  } finally {
    loading.value = false;
  }
}

function buildAbilityRequest(): AgentTemplateCreateRequest["ability_request"] {
  return {
    surface: { mode: "auto", static: [], max_static: null },
    packages: buildPackages(),
    mcp_refs: [],
  };
}

function buildCreatePayload(): AgentTemplateCreateRequest {
  return {
    metadata: {
      namespace: form.namespace.trim(),
      name: form.name.trim(),
      title: form.title,
      description: form.description,
      tags: splitTags(),
      icon: "",
      deprecated: form.deprecated,
    },
    model: { endpoint_name: form.endpointName.trim(), model_overrides: null },
    prompt: { system_prompt: form.systemPrompt },
    context_policy: {
      max_iterations: form.maxIterations,
      communication_timeout: form.communicationTimeout,
      window: null,
      overrides: null,
    },
    ability_request: buildAbilityRequest(),
    permission_request: null,
    path_request: [],
  };
}

function buildUpdatePayload(): AgentTemplateUpdateRequest {
  return {
    expected_revision: props.template!.revision,
    title: form.title,
    description: form.description,
    tags: splitTags(),
    icon: null,
    deprecated: form.deprecated,
    model: { endpoint_name: form.endpointName.trim(), model_overrides: null },
    prompt: { system_prompt: form.systemPrompt },
    context_policy: {
      max_iterations: form.maxIterations,
      communication_timeout: form.communicationTimeout,
      window: null,
      overrides: null,
    },
    ability_request: buildAbilityRequest(),
    permission_request: null,
    path_request: null,
  };
}

function requestClose() {
  if (loading.value) return;
  emit("dirtyChange", false);
  emit("close");
}
</script>

<template>
  <BaseModal :title="t(`config.template.${mode}Title`)" @request-close="requestClose">
    <form class="template-form" @submit.prevent="submit">
      <p v-if="error" class="template-form-alert" role="alert">{{ error }}</p>

      <fieldset class="template-form-group">
        <legend>{{ t("config.template.identity") }}</legend>
        <label class="template-form-field">
          <span>{{ t("config.template.namespace") }}</span>
          <UiTextField
            v-model="form.namespace"
            :disabled="mode === 'edit'"
            data-testid="template-namespace"
          />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.name") }}</span>
          <UiTextField
            v-model="form.name"
            :disabled="mode === 'edit'"
            data-testid="template-name"
          />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.displayTitle") }}</span>
          <UiTextField v-model="form.title" />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.description") }}</span>
          <UiTextField v-model="form.description" />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.tags") }}</span>
          <UiTextField v-model="form.tags" />
        </label>
        <label class="template-form-check">
          <input v-model="form.deprecated" type="checkbox" />
          {{ t("config.template.deprecated") }}
        </label>
      </fieldset>

      <fieldset class="template-form-group">
        <legend>{{ t("config.template.modelGroup") }}</legend>
        <label class="template-form-field">
          <span>{{ t("config.template.endpoint") }}</span>
          <UiTextField v-model="form.endpointName" data-testid="template-endpoint" />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.systemPrompt") }}</span>
          <UiTextField v-model="form.systemPrompt" multiline rows="5" />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.maxIterations") }}</span>
          <UiTextField v-model.number="form.maxIterations" type="number" />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.communicationTimeout") }}</span>
          <UiTextField v-model.number="form.communicationTimeout" type="number" />
        </label>
      </fieldset>

      <fieldset class="template-form-group">
        <legend>{{ t("config.template.abilities") }}</legend>
        <p class="template-form-hint">{{ t("config.template.abilitiesHint") }}</p>
        <div v-for="(row, index) in form.packages" :key="index" class="template-form-package">
          <label class="template-form-field">
            <span>{{ t("config.template.package") }}</span>
            <UiTextField v-model="row.package" />
          </label>
          <label class="template-form-field">
            <span>{{ t("config.template.operations") }}</span>
            <UiTextField v-model="row.operations" :disabled="row.wholeContainer" />
          </label>
          <label class="template-form-check">
            <input v-model="row.wholeContainer" type="checkbox" />
            {{ t("config.template.wholeContainer") }}
          </label>
          <UiButton type="button" variant="ghost" @click="removePackage(index)">
            {{ t("config.template.removePackage") }}
          </UiButton>
        </div>
        <UiButton type="button" variant="secondary" @click="addPackage">
          {{ t("config.template.addPackage") }}
        </UiButton>
      </fieldset>

      <footer class="template-form-actions">
        <UiButton type="button" variant="ghost" @click="requestClose">
          {{ t("common.cancel") }}
        </UiButton>
        <UiButton type="submit" variant="primary" :disabled="loading || !canSubmit">
          {{ loading ? t("common.saving") : t("common.save") }}
        </UiButton>
      </footer>
    </form>
  </BaseModal>
</template>

<style scoped>
.template-form {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1rem;
  overflow-wrap: anywhere;
}

.template-form-group {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  border: 0;
}

.template-form-group legend {
  margin-bottom: 0.25rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  font-weight: 600;
}

.template-form-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: var(--font-ui-note);
}

.template-form-field textarea,
.template-form-field input {
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}

.template-form-field textarea {
  resize: vertical;
}

.template-form-check {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: var(--font-ui-note);
}

.template-form-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.template-form-package {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.5rem;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.template-form-alert {
  margin: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.template-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
</style>
