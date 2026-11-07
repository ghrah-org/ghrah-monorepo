<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { Document, parseDocument } from "yaml";
import BaseModal from "@/components/ui/base-modal.vue";
import { useObserver } from "@/composables/useObserver";
import UiButton from "@/components/ui/button.vue";
import UiTextField from "@/components/ui/text-field.vue";

const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean] }>();

const { putAgent, validateManifest } = useObserver();
const { t } = useI18n();

const loading = ref(false);
const error = ref<string | null>(null);
const mode = ref<"form" | "yaml">("form");
const yamlDraft = ref("");
const status = ref("");
watch(yamlDraft, () => {
  error.value = null;
  status.value = "";
});

// ── Form state ──

interface AbilityEntry {
  mode: "type" | "ref";
  value: string;
}

const form = reactive({
  // metadata
  namespace: "",
  name: "",
  title: "",
  description: "",
  tags: "",
  // model
  endpoint_name: "",
  temperature: "" as string,
  max_tokens: "" as string,
  // prompt
  system_prompt: "You are a helpful assistant.",
  // abilities
  abilities: [{ mode: "type" as const, value: "end_task" }] as AbilityEntry[],
  // settings
  max_iterations: 10,
});

// ── Computed ──

const canSubmit = computed(() => {
  if (mode.value === "yaml") return yamlDraft.value.trim() !== "";
  return (
    form.namespace.trim() !== "" &&
    form.name.trim() !== "" &&
    form.endpoint_name.trim() !== "" &&
    form.system_prompt.trim() !== "" &&
    form.abilities.length > 0 &&
    form.abilities.every((a) => a.value.trim() !== "")
  );
});

watch(
  form,
  () => {
    error.value = null;
    status.value = "";
  },
  { deep: true },
);

function requestClose() {
  if (loading.value) return;
  finishClose();
}

function finishClose() {
  emit("dirtyChange", false);
  emit("close");
}

// ── Abilities ──

function addAbility() {
  form.abilities.push({ mode: "type", value: "" });
}

function removeAbility(index: number) {
  if (form.abilities.length > 1) {
    form.abilities.splice(index, 1);
  }
}

// ── YAML builder ──

function buildYaml(): string {
  const doc = new Document();
  doc.set("manifest", "agent");
  if (!doc.has("version")) doc.set("version", "2");
  for (const key of ["namespace", "name", "title", "description"] as const)
    doc.setIn(
      ["metadata", key],
      key === "namespace" || key === "name" ? form[key].trim() : form[key],
    );
  doc.setIn(
    ["metadata", "tags"],
    form.tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
  );
  doc.setIn(["model", "endpoint_name"], form.endpoint_name.trim());
  for (const key of ["temperature", "max_tokens"] as const) {
    if (form[key] === "") doc.deleteIn(["model", key]);
    else doc.setIn(["model", key], Number(form[key]));
  }
  doc.set("system_prompt", form.system_prompt);
  doc.set(
    "abilities",
    form.abilities.map((ability) => {
      const value = ability.value.trim();
      return {
        [ability.mode]: value,
      };
    }),
  );
  doc.set("max_iterations", form.max_iterations);
  return doc.toString();
}

async function validateContent(content: string) {
  const doc = parseDocument(content);
  if (doc.errors.length) throw new Error(doc.errors.map((e) => e.message).join("\n"));
  const data = doc.toJS();
  if (data?.manifest !== "agent") throw new Error(t("config.agent.agentRequired"));
  const result = await validateManifest(content, "agent");
  if (!result) throw new Error(t("config.manifest.notConnected"));
  if (!result.success) throw new Error(result.error || t("config.agent.validationFailed"));
  const validation = result.data as { valid: boolean; errors: string[] };
  if (!validation.valid)
    throw new Error(validation.errors.join("\n") || t("config.agent.validationFailed"));
  return data;
}

function switchMode(next: "form" | "yaml") {
  mode.value = next;
  error.value = null;
  status.value = "";
}

// ── Submit ──

async function handleSubmit(save = true) {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  status.value = "";
  try {
    const content = mode.value === "yaml" ? yamlDraft.value : buildYaml();
    const data = await validateContent(content);
    if (!save) {
      status.value = t("config.agent.valid");
      return;
    }
    const fullName = `${data.metadata.namespace}.${data.metadata.name}`;
    const result = await putAgent(fullName, content, false);
    if (!result) throw new Error(t("config.manifest.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.manifest.createFailed"));
    // putAgent 已用保存响应更新列表；避免刷新失败把已创建误报为创建失败。
    finishClose();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.manifest.createFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <BaseModal :title="t('config.manifest.dialogTitle')" size="lg" @request-close="requestClose">
    <template #subtitle>
      <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
        {{ t("config.manifest.subtitle") }}
      </p>
    </template>

    <!-- Scrollable form body -->
    <form class="manifest-create-form space-y-5" @submit.prevent="handleSubmit(true)">
      <div class="manifest-modes">
        <UiButton
          type="button"
          variant="secondary"
          :aria-pressed="mode === 'form'"
          :disabled="loading"
          @click="switchMode('form')"
        >
          {{ t("config.manifest.formMode") }}
        </UiButton>
        <UiButton
          type="button"
          variant="secondary"
          :aria-pressed="mode === 'yaml'"
          :disabled="loading"
          @click="switchMode('yaml')"
        >
          {{ t("config.manifest.yamlMode") }}
        </UiButton>
      </div>
      <div
        v-if="error"
        role="alert"
        class="text-red-600 dark:text-red-400 text-sm bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded"
      >
        {{ error }}
      </div>
      <p v-if="status" role="status">{{ status }}</p>
      <div v-if="mode === 'yaml'" class="manifest-yaml-panel">
        <label for="new-agent-yaml">{{ t("config.agent.yaml") }}</label>
        <p>{{ t("config.manifest.yamlHint") }}</p>
        <textarea
          id="new-agent-yaml"
          v-model="yamlDraft"
          rows="22"
          spellcheck="false"
          :disabled="loading"
        />
      </div>
      <fieldset v-else class="manifest-form-fields space-y-5" :disabled="loading">
        <!-- Metadata Section -->
        <fieldset>
          <legend class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
            {{ t("config.manifest.metadata") }}
          </legend>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                {{ t("config.manifest.namespace") }}
              </label>
              <UiTextField
                v-model="form.namespace"
                data-autofocus
                type="text"
                required
                pattern="^[a-z][a-z0-9_.]*[a-z0-9]$"
                class="w-full px-2 py-1.5 text-sm"
                placeholder="e.g. ghrah.examples"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                {{ t("config.manifest.name") }}
              </label>
              <UiTextField
                v-model="form.name"
                type="text"
                required
                pattern="^[a-z][a-z0-9_]*$"
                class="w-full px-2 py-1.5 text-sm"
                placeholder="e.g. designer"
              />
            </div>
          </div>
          <div class="mt-3">
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.titleField")
            }}</label>
            <UiTextField
              v-model="form.title"
              type="text"
              class="w-full px-2 py-1.5 text-sm"
              placeholder="e.g. Designer"
            />
          </div>
          <div class="mt-3">
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.description")
            }}</label>
            <UiTextField
              v-model="form.description"
              type="text"
              class="w-full px-2 py-1.5 text-sm"
              :placeholder="t('config.manifest.descriptionPlaceholder')"
            />
          </div>
          <div class="mt-3">
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.tags")
            }}</label>
            <UiTextField
              v-model="form.tags"
              type="text"
              class="w-full px-2 py-1.5 text-sm"
              :placeholder="t('config.manifest.tagsPlaceholder')"
            />
          </div>
        </fieldset>

        <!-- Model Section -->
        <fieldset>
          <legend class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
            {{ t("config.manifest.model") }}
          </legend>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                {{ t("config.manifest.endpointName") }}
              </label>
              <UiTextField
                v-model="form.endpoint_name"
                type="text"
                required
                class="w-full px-2 py-1.5 text-sm"
                placeholder="e.g. gpt-4o-mini"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
                t("config.manifest.maxIterations")
              }}</label>
              <UiTextField
                v-model.number="form.max_iterations"
                type="number"
                min="1"
                class="w-full px-2 py-1.5 text-sm"
              />
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3 mt-3">
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
                t("config.manifest.temperature")
              }}</label>
              <UiTextField
                v-model="form.temperature"
                type="number"
                step="0.1"
                min="0"
                max="2"
                class="w-full px-2 py-1.5 text-sm"
                :placeholder="t('config.manifest.temperaturePlaceholder')"
              />
            </div>
            <div>
              <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
                t("config.manifest.maxTokens")
              }}</label>
              <UiTextField
                v-model="form.max_tokens"
                type="number"
                min="1"
                class="w-full px-2 py-1.5 text-sm"
                :placeholder="t('config.manifest.maxTokensPlaceholder')"
              />
            </div>
          </div>
        </fieldset>

        <!-- System Prompt Section -->
        <fieldset>
          <legend class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
            {{ t("config.manifest.systemPrompt") }}
          </legend>
          <UiTextField
            v-model="form.system_prompt"
            multiline
            required
            rows="4"
            class="w-full px-2 py-1.5 text-sm font-mono"
            placeholder="You are a helpful assistant."
          />
        </fieldset>

        <!-- Abilities Section -->
        <fieldset>
          <legend class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
            {{ t("config.manifest.abilities") }}
          </legend>
          <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">
            {{ t("config.manifest.abilitiesHint") }}
          </p>
          <div class="space-y-2">
            <div
              v-for="(ability, index) in form.abilities"
              :key="index"
              class="flex items-center gap-2"
            >
              <select
                v-model="ability.mode"
                class="px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="type">{{ t("config.manifest.typeBuiltIn") }}</option>
                <option value="ref">{{ t("config.manifest.ref") }}</option>
              </select>
              <UiTextField
                v-model="ability.value"
                type="text"
                required
                class="flex-1 px-2 py-1.5 text-sm"
                :placeholder="
                  ability.mode === 'type'
                    ? 'e.g. end_task, conversation'
                    : 'e.g. ghrah.fs.read_file'
                "
              />
              <button
                type="button"
                :disabled="form.abilities.length <= 1"
                class="px-2 py-1 text-xs text-red-600 hover:text-red-800 dark:text-red-400 dark:hover:text-red-300 disabled:opacity-30 disabled:cursor-not-allowed"
                @click="removeAbility(index)"
              >
                ✕
              </button>
            </div>
          </div>
          <button
            type="button"
            class="mt-2 text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300"
            @click="addAbility()"
          >
            {{ t("config.manifest.addAbility") }}
          </button>
        </fieldset>

        <!-- Actions -->
      </fieldset>
      <div class="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
        <UiButton type="button" variant="secondary" :disabled="loading" @click="requestClose">
          {{ t("common.cancel") }}
        </UiButton>
        <UiButton
          type="button"
          variant="secondary"
          :disabled="loading || !canSubmit"
          @click="handleSubmit(false)"
        >
          {{ t("config.agent.validate") }}
        </UiButton>
        <UiButton type="submit" variant="primary" :disabled="loading || !canSubmit">
          {{ loading ? t("config.manifest.creating") : t("config.manifest.create") }}
        </UiButton>
      </div>
    </form>
  </BaseModal>
</template>

<style scoped>
.manifest-modes {
  display: flex;
  gap: var(--spacing-2);
}
.manifest-modes [aria-pressed="true"] {
  color: var(--ac-read);
  background: color-mix(in srgb, var(--ac-read) 12%, var(--ac-node-bg));
}
.manifest-form-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.manifest-yaml-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}
.manifest-yaml-panel p {
  color: var(--ac-node-subtext);
  font-size: var(--font-ui-note);
}
.manifest-yaml-panel textarea {
  width: 100%;
  box-sizing: border-box;
  padding: var(--spacing-3);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  color: var(--ac-node-text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--font-ui-code);
  resize: vertical;
}
.manifest-yaml-panel textarea:focus-visible {
  outline: 2px solid var(--ac-read);
}
[role="alert"] {
  white-space: pre-wrap;
}
</style>
