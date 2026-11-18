<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
/**
 * 模板表单：字段尽量"选"而不是"填"。
 *
 * - 模型端点：从「模型与 MCP」页里已配置的 LLM 端点下拉选。
 * - 能力包：从已安装能力包下拉选，选中后按该包的成员勾选操作（或整包）。
 * - 其余字段给出示例与说明，避免只留空输入框。
 */
import type {
  AgentTemplateCreateRequest,
  AgentTemplateDetailView,
  AgentTemplateUpdateRequest,
} from "@ghrah/protocol";
import {
  AbilityPackageGroupsViewSchema,
  AbilityPackageVersionProfileViewSchema,
  AgentconfEndpointListViewSchema,
} from "@ghrah/protocol";
import { createAgentTemplatesApi, HttpClient, useConnectionStore } from "@ghrah/observer-core";
import { computed, onMounted, reactive, ref } from "vue";
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
  wholeContainer: boolean;
  /** 该包可申请的成员（来自能力包版本详情）。 */
  available: string[];
  /** 已勾选的成员。 */
  selected: string[];
  /** 该包可申请的技能键。 */
  availableSkills: string[];
  /** 已勾选的技能键。 */
  selectedSkills: string[];
}

interface PackageOption {
  fullName: string;
  version: string;
  /** 包来源：内置或安装包。落库时原样带过去，解析端据此定位。 */
  origin: "builtin" | "installed";
  label: string;
}

const mode = computed(() => (props.template === null ? "create" : "edit"));
const loading = ref(false);
const loadingOptions = ref(true);
const error = ref<string | null>(null);
const endpoints = ref<string[]>([]);
const packages = ref<PackageOption[]>([]);
/** 端点下拉的当前选择；`__custom__` 表示手填一个 agentconf 里的模型名。 */
const endpointChoice = ref(props.template?.model.endpoint_name ?? "");
const customEndpoint = ref(false);
const CUSTOM_ENDPOINT = "__custom__";

function onEndpointChoiceChange(value: string) {
  customEndpoint.value = value === CUSTOM_ENDPOINT;
  if (!customEndpoint.value) form.endpointName = value;
}

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
  rows: (props.template?.ability_request.packages ?? []).map((item) => ({
    package: item.package,
    wholeContainer: item.whole_container,
    available: item.operations.map((op) => op.key),
    selected: item.operations.map((op) => op.key),
    availableSkills: item.skills.map((skill) => skill.key),
    selectedSkills: item.skills.map((skill) => skill.key),
  })) as PackageRow[],
});

const abilityCache = new Map<string, { operations: string[]; skills: string[] }>();

function http() {
  return new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken });
}

function api() {
  return createAgentTemplatesApi(http());
}

async function loadOptions() {
  loadingOptions.value = true;
  const client = http();
  try {
    const endpointPage = AgentconfEndpointListViewSchema.parse(
      await client.get("/api/v1/agentconf/endpoints", { query: { model_type: "llm" } }),
    );
    endpoints.value = endpointPage.endpoints.map((item) => item.name);
    if (form.endpointName && !endpoints.value.includes(form.endpointName)) {
      endpoints.value = [form.endpointName, ...endpoints.value];
    }
    // 新建时自动带上 agentconf 里的模型，用户不必先想"端点"这件事。
    if (form.endpointName === "") {
      const preferred =
        endpoints.value.find((name) => name.toLowerCase().includes("default")) ??
        endpoints.value[0];
      if (preferred !== undefined) {
        endpointChoice.value = preferred;
        form.endpointName = preferred;
      }
    } else if (endpoints.value.includes(form.endpointName)) {
      endpointChoice.value = form.endpointName;
    } else {
      endpointChoice.value = CUSTOM_ENDPOINT;
      customEndpoint.value = true;
    }
  } catch {
    endpoints.value = form.endpointName ? [form.endpointName] : [];
    customEndpoint.value = form.endpointName !== "";
    endpointChoice.value = customEndpoint.value ? CUSTOM_ENDPOINT : "";
  }
  try {
    const groups = AbilityPackageGroupsViewSchema.parse(
      await client.get("/api/v1/ability-package-groups"),
    );
    packages.value = groups.groups.flatMap((group) => {
      const fullName = group.full_name ?? `${group.namespace ?? ""}.${group.name ?? ""}`;
      return group.versions
        .filter((version) => version.version !== null)
        .map((version) => ({
          fullName,
          version: version.version as string,
          origin: version.origin === "installed" ? ("installed" as const) : ("builtin" as const),
          label: `${fullName} v${version.version}${group.title ? ` · ${group.title}` : ""}`,
        }));
    });
  } catch {
    packages.value = [];
  } finally {
    loadingOptions.value = false;
  }
  for (const row of form.rows) await ensureMembers(row);
}

async function fetchMembers(
  fullName: string,
  version: string,
): Promise<{ operations: string[]; skills: string[] }> {
  const key = `${fullName}@${version}`;
  const cached = abilityCache.get(key);
  if (cached !== undefined) return cached;
  // 退化身份（只有 namespace，如 `research`）在服务端以 <namespace>/<namespace> 落盘。
  const parts = fullName.split(".");
  const namespace = parts[0];
  const name = parts[1] ?? parts[0];
  if (!namespace) return { operations: [], skills: [] };
  try {
    // 与「Ability 配置」页同一个版本详情接口：返回该版本可申请的操作键与技能键。
    const profile = AbilityPackageVersionProfileViewSchema.parse(
      await http().get(`/api/v1/ability-packages/${namespace}/${name}/versions/${version}`),
    );
    const members = {
      operations: profile.operations.map((item) => item.key),
      skills: profile.skills.map((item) => item.key),
    };
    abilityCache.set(key, members);
    return members;
  } catch {
    return { operations: [], skills: [] };
  }
}

async function ensureMembers(row: PackageRow) {
  if (row.package === "") return;
  const option = packages.value.find((item) => item.fullName === row.package);
  if (option === undefined) return;
  const members = await fetchMembers(option.fullName, option.version);
  row.available = members.operations;
  row.availableSkills = members.skills;
  if (row.wholeContainer) {
    // 整包 = 全选：保存时按具体成员键落库，所以这里直接把目录里的成员都选上。
    row.selected = [...members.operations];
    row.selectedSkills = [...members.skills];
  } else {
    row.selected = row.selected.filter((key) => members.operations.includes(key));
    row.selectedSkills = row.selectedSkills.filter((key) => members.skills.includes(key));
  }
}

function addPackage() {
  const first = packages.value[0];
  form.rows.push({
    package: "",
    wholeContainer: false,
    available: [],
    selected: [],
    availableSkills: [],
    selectedSkills: [],
  });
  if (first && form.rows[form.rows.length - 1]!.package === "") {
    form.rows[form.rows.length - 1]!.package = first.fullName;
    void ensureMembers(form.rows[form.rows.length - 1]!);
  }
}

async function onPackageChange(row: PackageRow) {
  row.selected = [];
  row.selectedSkills = [];
  row.available = [];
  row.availableSkills = [];
  await ensureMembers(row);
}

function removePackage(index: number) {
  form.rows.splice(index, 1);
}

function toggleOperation(row: PackageRow, key: string, checked: boolean) {
  if (checked) {
    if (!row.selected.includes(key)) row.selected.push(key);
  } else {
    row.selected = row.selected.filter((item) => item !== key);
  }
}

function toggleSkill(row: PackageRow, key: string, checked: boolean) {
  if (checked) {
    if (!row.selectedSkills.includes(key)) row.selectedSkills.push(key);
  } else {
    row.selectedSkills = row.selectedSkills.filter((item) => item !== key);
  }
}

const canSubmit = computed(() => {
  if (mode.value === "create" && (form.namespace.trim() === "" || form.name.trim() === "")) {
    return false;
  }
  if (form.endpointName.trim() === "") return false;
  return form.rows.every((row) => {
    if (row.package === "") return false;
    // 「整包」不需要本地目录：成员由 Core 按自己的能力来源展开，
    // 所以哪怕目录里暂时读不到操作也要允许保存。
    if (row.wholeContainer) return true;
    return row.selected.length > 0 || row.selectedSkills.length > 0;
  });
});

function splitTags(): string[] {
  return form.tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

/**
 * 「整包」以 `whole_container` 原样交给后端：成员由 Core 按自己的能力来源展开。
 *
 * 前端**不**展开成具体成员键：两侧目录可能不同步（实测 `ghrah.cluster` 在 Subject
 * 目录里有 `send`，Core 的包定义里没有），展开后的成员会在创建时解析失败。
 */
function originOf(fullName: string): "builtin" | "installed" {
  return packages.value.find((option) => option.fullName === fullName)?.origin ?? "builtin";
}

function versionOf(fullName: string): string | null {
  // 内核件随源码发布、没有版本层；能力件钉住目录里的当前版本。
  if (originOf(fullName) === "builtin") return null;
  return packages.value.find((option) => option.fullName === fullName)?.version ?? null;
}

function buildPackages(): NonNullable<AgentTemplateCreateRequest["ability_request"]>["packages"] {
  return form.rows
    .filter((row) => row.package !== "")
    .map((row) => ({
      package: row.package,
      origin: originOf(row.package),
      // 存储层不留整包：即便勾了整包也按成员落库（后端还会再展开一次兜底）。
      whole_container: false,
      version: versionOf(row.package),
      operations: (row.wholeContainer ? row.available : row.selected).map((key) => ({
        key,
        visible: true,
        available: true,
      })),
      skills: (row.wholeContainer ? row.availableSkills : row.selectedSkills).map((key) => ({
        key,
        visible: true,
        scripts: [],
      })),
    }));
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

function requestClose() {
  if (loading.value) return;
  emit("dirtyChange", false);
  emit("close");
}

onMounted(loadOptions);
</script>

<template>
  <BaseModal :title="t(`config.template.${mode}Title`)" size="lg" @request-close="requestClose">
    <form class="template-form" @submit.prevent="submit">
      <p v-if="error" class="template-form-alert" role="alert">{{ error }}</p>

      <p class="template-form-intro">{{ t("config.template.intro") }}</p>

      <fieldset class="template-form-group">
        <legend>{{ t("config.template.identity") }}</legend>
        <label class="template-form-field">
          <span>{{ t("config.template.namespace") }}</span>
          <UiTextField
            v-model="form.namespace"
            :placeholder="t('config.template.namespaceExample')"
            :disabled="mode === 'edit'"
            data-testid="template-namespace"
          />
          <small>{{ t("config.template.namespaceHint") }}</small>
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.name") }}</span>
          <UiTextField
            v-model="form.name"
            :placeholder="t('config.template.nameExample')"
            :disabled="mode === 'edit'"
            data-testid="template-name"
          />
          <small>{{ t("config.template.nameHint") }}</small>
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.displayTitle") }}</span>
          <UiTextField
            v-model="form.title"
            :placeholder="t('config.template.displayTitleExample')"
          />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.description") }}</span>
          <UiTextField
            v-model="form.description"
            :placeholder="t('config.template.descriptionExample')"
          />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.tags") }}</span>
          <UiTextField v-model="form.tags" :placeholder="t('config.template.tagsExample')" />
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
          <select
            v-model="endpointChoice"
            data-testid="template-endpoint"
            @change="onEndpointChoiceChange(endpointChoice)"
          >
            <option value="">{{ t("config.template.endpointPlaceholder") }}</option>
            <option v-for="name in endpoints" :key="name" :value="name">{{ name }}</option>
            <option :value="CUSTOM_ENDPOINT">{{ t("config.template.endpointCustom") }}</option>
          </select>
          <UiTextField
            v-if="customEndpoint"
            v-model="form.endpointName"
            :placeholder="t('config.template.endpointCustomExample')"
          />
          <small v-if="endpoints.length === 0" class="template-form-warn">
            {{ t("config.template.endpointEmpty") }}
          </small>
          <small v-else>{{ t("config.template.endpointHint") }}</small>
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.systemPrompt") }}</span>
          <UiTextField
            v-model="form.systemPrompt"
            multiline
            rows="13"
            :placeholder="t('config.template.systemPromptExample')"
          />
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.maxIterations") }}</span>
          <UiTextField v-model.number="form.maxIterations" type="number" />
          <small>{{ t("config.template.maxIterationsHint") }}</small>
        </label>
        <label class="template-form-field">
          <span>{{ t("config.template.communicationTimeout") }}</span>
          <UiTextField v-model.number="form.communicationTimeout" type="number" />
          <small>{{ t("config.template.communicationTimeoutHint") }}</small>
        </label>
      </fieldset>

      <fieldset class="template-form-group">
        <legend>{{ t("config.template.abilities") }}</legend>
        <p class="template-form-hint">{{ t("config.template.abilitiesHint") }}</p>
        <p v-if="loadingOptions" class="template-form-hint">
          {{ t("config.template.loadingOptions") }}
        </p>
        <p v-else-if="packages.length === 0" class="template-form-warn">
          {{ t("config.template.noPackagesAvailable") }}
        </p>

        <div v-for="(row, index) in form.rows" :key="index" class="template-form-package">
          <label class="template-form-field">
            <span>{{ t("config.template.package") }}</span>
            <select v-model="row.package" @change="onPackageChange(row)">
              <option value="">{{ t("config.template.choosePackage") }}</option>
              <option v-for="option in packages" :key="option.label" :value="option.fullName">
                {{ option.label }}
              </option>
            </select>
          </label>

          <label class="template-form-check">
            <input v-model="row.wholeContainer" type="checkbox" />
            {{ t("config.template.wholeContainer") }}
          </label>

          <div v-if="!row.wholeContainer" class="template-form-ops">
            <span class="template-form-ops-title">{{ t("config.template.operations") }}</span>
            <p v-if="row.available.length === 0" class="template-form-hint">
              {{ t("config.template.noOperations") }}
            </p>
            <label v-for="key in row.available" :key="key" class="template-form-check">
              <input
                type="checkbox"
                :checked="row.selected.includes(key)"
                @change="toggleOperation(row, key, ($event.target as HTMLInputElement).checked)"
              />
              {{ key }}
            </label>
          </div>

          <div v-if="!row.wholeContainer" class="template-form-ops">
            <span class="template-form-ops-title">{{ t("config.template.skills") }}</span>
            <p v-if="row.availableSkills.length === 0" class="template-form-hint">
              {{ t("config.template.noSkills") }}
            </p>
            <label v-for="key in row.availableSkills" :key="key" class="template-form-check">
              <input
                type="checkbox"
                :checked="row.selectedSkills.includes(key)"
                @change="toggleSkill(row, key, ($event.target as HTMLInputElement).checked)"
              />
              {{ key }}
            </label>
          </div>

          <UiButton type="button" variant="ghost" @click="removePackage(index)">
            {{ t("config.template.removePackage") }}
          </UiButton>
        </div>

        <UiButton
          type="button"
          variant="secondary"
          :disabled="packages.length === 0"
          @click="addPackage"
        >
          {{ t("config.template.addPackage") }}
        </UiButton>
      </fieldset>

      <footer class="template-form-actions">
        <p v-if="!canSubmit" class="template-form-hint template-form-need">
          {{ t("config.template.submitHint") }}
        </p>
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

.template-form-intro {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.template-form-group {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.625rem;
  margin: 0;
  padding: 0;
  border: 0;
}

.template-form-group legend {
  margin-bottom: 0.125rem;
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

.template-form-field small,
.template-form-hint {
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
  line-height: 1.4;
}

.template-form-field small.template-form-warn,
.template-form-warn {
  color: var(--color-warning, var(--color-danger));
}

.template-form-field select,
.template-form-field input,
.template-form-field textarea {
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}

.template-form-field textarea {
  min-height: 16rem;
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
}

.template-form-package {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.template-form-ops {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem 0.75rem;
}

.template-form-ops-title {
  flex-basis: 100%;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
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
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
}

.template-form-need {
  margin-right: auto;
}
</style>
