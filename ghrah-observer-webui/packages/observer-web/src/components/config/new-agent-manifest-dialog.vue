<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean] }>();

const { putAgent, listManifestAgents } = useObserver();
const { t } = useI18n();

const loading = ref(false);
const error = ref<string | null>(null);
const dirty = ref(false);
const confirmDiscardOpen = ref(false);

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
    if (dirty.value) return;
    dirty.value = true;
    emit("dirtyChange", true);
  },
  { deep: true },
);

function requestClose() {
  if (dirty.value) confirmDiscardOpen.value = true;
  else finishClose();
}

function finishClose() {
  dirty.value = false;
  emit("dirtyChange", false);
  emit("close");
}

function discardAndClose() {
  confirmDiscardOpen.value = false;
  finishClose();
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
  const ns = form.namespace.trim();
  const nm = form.name.trim();
  const lines: string[] = [];

  lines.push("manifest: agent");
  lines.push('version: "2"');

  // metadata
  lines.push("metadata:");
  lines.push(`  namespace: ${ns}`);
  lines.push(`  name: ${nm}`);
  lines.push(`  title: ${quote(form.title)}`);
  lines.push(`  description: ${quote(form.description)}`);
  const tagsList = form.tags
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t);
  lines.push(`  tags: [${tagsList.map((t) => quote(t)).join(", ")}]`);

  // model
  lines.push("model:");
  lines.push(`  endpoint_name: ${quote(form.endpoint_name.trim())}`);
  if (form.temperature !== "") {
    lines.push(`  temperature: ${form.temperature}`);
  }
  if (form.max_tokens !== "") {
    lines.push(`  max_tokens: ${form.max_tokens}`);
  }

  // system_prompt (multiline safe)
  lines.push(`system_prompt: ${quoteBlock(form.system_prompt)}`);

  // abilities
  lines.push("abilities:");
  for (const ab of form.abilities) {
    if (ab.mode === "type") {
      lines.push(`  - type: ${ab.value.trim()}`);
    } else {
      lines.push(`  - ref: ${ab.value.trim()}`);
    }
  }

  // settings
  lines.push(`max_iterations: ${form.max_iterations}`);

  return lines.join("\n") + "\n";
}

function quote(s: string): string {
  if (s === "" || /[:#{}[\],&*?|>!%@`]/.test(s) || s.includes("\n")) {
    return JSON.stringify(s);
  }
  return s;
}

function quoteBlock(s: string): string {
  if (!s.includes("\n")) {
    return quote(s);
  }
  // Use YAML literal block scalar for multiline
  return "|\n  " + s.split("\n").join("\n  ");
}

// ── Submit ──

async function handleSubmit() {
  const ns = form.namespace.trim();
  const nm = form.name.trim();
  if (!ns || !nm) return;
  loading.value = true;
  error.value = null;

  const fullName = `${ns}.${nm}`;
  const content = buildYaml();

  const result = await putAgent(fullName, content, false);
  loading.value = false;

  if (result && !result.success) {
    error.value = result.error ?? t("config.manifest.createFailed");
  } else if (result === null) {
    error.value = t("config.manifest.notConnected");
  } else {
    await listManifestAgents();
    window.postMessage?.({ type: "openFile", fullName, kind: "agent" });
    finishClose();
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
    <form class="manifest-create-form space-y-5" @submit.prevent="handleSubmit">
      <div
        v-if="error"
        class="text-red-600 dark:text-red-400 text-sm bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded"
      >
        {{ error }}
      </div>

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
            <input
              v-model="form.namespace"
              data-autofocus
              type="text"
              required
              pattern="^[a-z][a-z0-9_.]*[a-z0-9]$"
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="e.g. ghrah.examples"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              {{ t("config.manifest.name") }}
            </label>
            <input
              v-model="form.name"
              type="text"
              required
              pattern="^[a-z][a-z0-9_]*$"
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="e.g. designer"
            />
          </div>
        </div>
        <div class="mt-3">
          <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
            t("config.manifest.titleField")
          }}</label>
          <input
            v-model="form.title"
            type="text"
            class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="e.g. Designer"
          />
        </div>
        <div class="mt-3">
          <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
            t("config.manifest.description")
          }}</label>
          <input
            v-model="form.description"
            type="text"
            class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            :placeholder="t('config.manifest.descriptionPlaceholder')"
          />
        </div>
        <div class="mt-3">
          <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
            t("config.manifest.tags")
          }}</label>
          <input
            v-model="form.tags"
            type="text"
            class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
            <input
              v-model="form.endpoint_name"
              type="text"
              required
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="e.g. gpt-4o-mini"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.maxIterations")
            }}</label>
            <input
              v-model.number="form.max_iterations"
              type="number"
              min="1"
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3 mt-3">
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.temperature")
            }}</label>
            <input
              v-model="form.temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              :placeholder="t('config.manifest.temperaturePlaceholder')"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">{{
              t("config.manifest.maxTokens")
            }}</label>
            <input
              v-model="form.max_tokens"
              type="number"
              min="1"
              class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
        <textarea
          v-model="form.system_prompt"
          required
          rows="4"
          class="w-full px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono"
          placeholder="You are a helpful assistant."
        ></textarea>
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
            <input
              v-model="ability.value"
              type="text"
              required
              class="flex-1 px-2 py-1.5 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              :placeholder="
                ability.mode === 'type' ? 'e.g. end_task, conversation' : 'e.g. ghrah.fs.read_file'
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
      <div class="flex justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
        <button type="button" class="btn-secondary" :disabled="loading" @click="requestClose">
          {{ t("common.cancel") }}
        </button>
        <button type="submit" class="btn-primary" :disabled="loading || !canSubmit">
          {{ loading ? t("config.manifest.creating") : t("config.manifest.create") }}
        </button>
      </div>
    </form>
  </BaseModal>
  <ConfirmDialog
    v-if="confirmDiscardOpen"
    :title="t('config.unsavedTitle')"
    :message="t('config.unsavedConfirm')"
    :confirm-label="t('config.discardChanges')"
    danger
    @cancel="confirmDiscardOpen = false"
    @confirm="discardAndClose"
  />
</template>
