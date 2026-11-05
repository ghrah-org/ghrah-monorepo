<script setup lang="ts">
import { extractValidationResult } from "@ghrah/observer-core";
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import AgentManifestForm from "./agent-manifest-form.vue";
import {
  createEmptyAgentManifestForm,
  dumpAgentManifestForm,
  type HookEvent,
} from "./agent-manifest-form";

const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean] }>();

const { putAgent, listManifestAgents, validateManifest, error: observerError } = useObserver();
const { t } = useI18n();

const loading = ref(false);
const error = ref<string | null>(null);
const dirty = ref(false);
const confirmDiscardOpen = ref(false);

const form = reactive(createEmptyAgentManifestForm());

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
  form.abilities.push({
    mode: "type",
    value: "",
    permissions: {
      enabled: false,
      require_hitl: false,
      fs_read_only: false,
      fs_write: false,
      net_access: false,
      shell_access: false,
      allowed_paths: "",
      denied_paths: "",
      allowed_commands: "",
      denied_commands: "",
    },
  });
}

function removeAbility(index: number) {
  if (form.abilities.length > 1) form.abilities.splice(index, 1);
}

function addHook(event: HookEvent) {
  form.hooks[event].push({
    type: "builtin",
    handler: "",
    source: "",
    params: "{}",
  });
}

function removeHook(event: HookEvent, index: number) {
  form.hooks[event].splice(index, 1);
}

function buildYaml(): string {
  return dumpAgentManifestForm(form);
}

// ── Submit ──

async function handleSubmit() {
  const ns = form.namespace.trim();
  const nm = form.name.trim();
  if (!ns || !nm) return;
  loading.value = true;
  error.value = null;

  const fullName = `${ns}.${nm}`;
  let content: string;
  try {
    content = buildYaml();
  } catch (cause) {
    error.value = (cause as Error).message;
    loading.value = false;
    return;
  }

  const checked = await validateManifest(content, "agent");
  const validation = checked?.success ? extractValidationResult(checked.data) : null;
  if (!validation?.valid) {
    error.value =
      validation?.errors.join("\n") ||
      checked?.error ||
      observerError.value ||
      t("common.validationFailed");
    loading.value = false;
    return;
  }
  const result = await putAgent(fullName, content, false);
  loading.value = false;

  if (result && !result.success) {
    error.value = result.error ?? t("config.manifest.createFailed");
  } else if (result === null) {
    error.value = t("config.manifest.notConnected");
  } else {
    await listManifestAgents();
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
        role="alert"
        class="text-red-600 dark:text-red-400 text-sm bg-red-50 dark:bg-red-900/20 px-3 py-2 rounded whitespace-pre-wrap"
      >
        {{ error }}
      </div>

      <AgentManifestForm
        :form="form"
        :disabled="loading"
        @add-ability="addAbility"
        @remove-ability="removeAbility"
        @add-hook="addHook"
        @remove-hook="removeHook"
      />

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
