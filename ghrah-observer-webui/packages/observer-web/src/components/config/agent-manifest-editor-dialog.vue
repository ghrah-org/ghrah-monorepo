<script setup lang="ts">
import { extractManifestEntry, extractValidationResult } from "@ghrah/observer-core";
import {
  type ManifestResolveAgentResponsePayload,
  ManifestResolveAgentResponsePayloadSchema,
} from "@ghrah/protocol";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";
import AgentManifestForm from "./agent-manifest-form.vue";
import AgentManifestPreview from "./agent-manifest-preview.vue";
import {
  applyAgentManifestForm,
  createEmptyAgentManifestForm,
  hookEvents,
  parseAgentManifestForm,
  type HookEvent,
} from "./agent-manifest-form";

const props = withDefaults(defineProps<{ fullName: string; initialMode?: "view" | "edit" }>(), {
  initialMode: "edit",
});
const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean] }>();
const { t } = useI18n();
const {
  connection,
  getAgent,
  putAgent,
  validateManifest,
  resolveAgent,
  error: observerError,
} = useObserver();
const editing = ref(props.initialMode === "edit");
const original = ref<string | null>(null);
const sourceHash = ref<string | null>(null);
const draft = ref("");
const phase = ref<"loading" | "validating" | "saving" | "verifying" | "resolving" | null>(null);
const error = ref("");
const validation = ref<{ valid: boolean; errors: string[] } | null>(null);
const saved = ref(false);
const needsVerification = ref(false);
const confirmDiscardOpen = ref(false);
const sourceField = ref<HTMLTextAreaElement | null>(null);
const submitted = ref<string | null>(null);
const preview = ref<ManifestResolveAgentResponsePayload | null>(null);
const previewPanel = ref<HTMLElement | null>(null);
const sourceMode = ref<"yaml" | "structured">("yaml");
const structuredError = ref("");
const structured = reactive(createEmptyAgentManifestForm());
let epoch = 0;

const connected = computed(() => connection.state === "connected");
const dirty = computed(() => original.value !== null && draft.value !== original.value);
const busy = computed(() => phase.value !== null);
const closeBlocked = computed(() => busy.value && phase.value !== "loading");
const canValidate = computed(
  () => connected.value && original.value !== null && !busy.value && !needsVerification.value,
);
const canSave = computed(() => canValidate.value && editing.value && dirty.value);

watch(dirty, (value) => emit("dirtyChange", value));
watch(draft, () => {
  validation.value = null;
  saved.value = false;
  error.value = "";
  preview.value = null;
  structuredError.value = "";
});

function failure(fallback: string, message?: string | null): Error {
  return new Error(message ?? observerError.value ?? t(fallback));
}

async function readSource(): Promise<{ source: string; sourceHash: string }> {
  const result = await getAgent(props.fullName);
  if (!result?.success) throw failure("config.editor.loadFailed", result?.error);
  const entry = extractManifestEntry(result.data, { kind: "agent", fullName: props.fullName });
  if (!entry) throw new Error(t("common.invalidResponse"));
  return { source: entry.detail.source, sourceHash: entry.detail.source_hash };
}

function enterStructuredMode() {
  if (sourceMode.value === "structured" || busy.value || needsVerification.value) return;
  try {
    replaceStructuredForm(parseAgentManifestForm(draft.value));
    sourceMode.value = "structured";
  } catch (cause) {
    structuredError.value = (cause as Error).message;
  }
}

function enterYamlMode() {
  if (sourceMode.value !== "structured" || busy.value || needsVerification.value) return;
  try {
    draft.value = applyAgentManifestForm(draft.value, structured);
    sourceMode.value = "yaml";
    void nextTick(() => sourceField.value?.focus());
  } catch (cause) {
    structuredError.value = (cause as Error).message;
  }
}

function replaceStructuredForm(next: ReturnType<typeof parseAgentManifestForm>) {
  Object.assign(structured, next, {
    window: next.window,
    persistence: next.persistence,
    hooks: next.hooks,
  });
  structured.abilities.splice(0, structured.abilities.length, ...next.abilities);
  for (const event of hookEvents) {
    structured.hooks[event].splice(0, structured.hooks[event].length, ...next.hooks[event]);
  }
}

function addAbility() {
  structured.abilities.push({
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
  if (structured.abilities.length > 1) structured.abilities.splice(index, 1);
}

function addHook(event: HookEvent) {
  structured.hooks[event].push({
    type: "builtin",
    handler: "",
    source: "",
    params: "{}",
  });
}

function removeHook(event: HookEvent, index: number) {
  structured.hooks[event].splice(index, 1);
}

async function load() {
  if (busy.value || !connected.value) return;
  const current = ++epoch;
  phase.value = "loading";
  error.value = "";
  try {
    const loaded = await readSource();
    if (current !== epoch) return;
    draft.value = loaded.source;
    original.value = loaded.source;
    sourceMode.value = "yaml";
    sourceHash.value = loaded.sourceHash;
    saved.value = false;
    phase.value = null;
    await nextTick();
    if (current === epoch) sourceField.value?.focus();
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}

async function check(source: string, current: number): Promise<boolean> {
  const result = await validateManifest(source, "agent");
  if (current !== epoch || source !== draft.value) return false;
  if (!result?.success) throw failure("common.validationFailed", result?.error);
  const parsed = extractValidationResult(result.data);
  if (!parsed) throw new Error(t("common.invalidResponse"));
  validation.value = parsed;
  return parsed.valid;
}

async function validate() {
  if (!canValidate.value) return;
  const current = epoch;
  phase.value = "validating";
  error.value = "";
  saved.value = false;
  try {
    await check(draft.value, current);
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}

async function previewDraft() {
  if (!canValidate.value) return;
  const current = epoch;
  const source = draft.value;
  phase.value = "resolving";
  preview.value = null;
  error.value = "";
  try {
    const result = await resolveAgent(props.fullName, null, source);
    if (current !== epoch || source !== draft.value) return;
    if (!result?.success) throw failure("config.preview.failed", result?.error);
    const parsed = ManifestResolveAgentResponsePayloadSchema.safeParse(result.data);
    if (
      !parsed.success ||
      parsed.data.agent_full_name !== props.fullName ||
      parsed.data.input_source !== "draft"
    ) {
      throw new Error(t("common.invalidResponse"));
    }
    preview.value = parsed.data;
    await nextTick();
    if (current === epoch) previewPanel.value?.focus();
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}

async function verify(current = epoch) {
  phase.value = "verifying";
  error.value = "";
  try {
    const loaded = await readSource();
    if (current !== epoch) return;
    if (loaded.source !== submitted.value) {
      original.value = loaded.source;
      sourceHash.value = loaded.sourceHash;
      needsVerification.value = false;
      validation.value = null;
      error.value = t("config.editor.differentSource");
      return;
    }
    draft.value = loaded.source;
    original.value = loaded.source;
    sourceHash.value = loaded.sourceHash;
    needsVerification.value = false;
    await nextTick();
    if (current === epoch) saved.value = true;
  } catch {
    if (current === epoch) error.value = t("config.editor.verifyFailed");
  } finally {
    if (current === epoch) phase.value = null;
  }
}

async function save() {
  if (!canSave.value) return;
  const current = epoch;
  const source = draft.value;
  phase.value = "validating";
  error.value = "";
  saved.value = false;
  try {
    if (!(await check(source, current))) return;
    phase.value = "saving";
    submitted.value = source;
    const result = await putAgent(props.fullName, source, true, sourceHash.value ?? undefined);
    if (current !== epoch) return;
    if (!result) {
      needsVerification.value = true;
      error.value = t("config.editor.saveUncertain");
      return;
    }
    if (!result.success && result.error_detail?.includes("code=manifest_conflict")) {
      throw new Error(t("config.editor.conflict"));
    }
    if (!result.success) throw failure("config.editor.saveFailed", result.error);
    needsVerification.value = true;
    await verify(current);
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}

function requestClose() {
  if (closeBlocked.value) return;
  if (dirty.value || needsVerification.value) confirmDiscardOpen.value = true;
  else finishClose();
}

function finishClose() {
  emit("dirtyChange", false);
  emit("close");
}

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value && !needsVerification.value) return;
  event.preventDefault();
  event.returnValue = "";
}

onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  void load();
});
onBeforeUnmount(() => {
  epoch++;
  window.removeEventListener("beforeunload", onBeforeUnload);
});
</script>

<template>
  <BaseModal
    :title="t(editing ? 'config.editor.editTitle' : 'config.editor.viewTitle')"
    size="lg"
    :show-close="!closeBlocked"
    @request-close="requestClose"
  >
    <template #subtitle
      ><p>{{ fullName }}</p></template
    >
    <div class="manifest-editor">
      <p>{{ t("config.editor.fileHint") }}</p>
      <p>{{ t("config.editor.identityHint") }}</p>
      <p v-if="structuredError" role="alert">{{ structuredError }}</p>
      <p v-if="!connected" role="alert">{{ t("config.manifest.notConnected") }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="phase" role="status">{{ t(`config.editor.${phase}`) }}</p>
      <template v-if="original !== null">
        <div class="source-tabs" role="tablist" :aria-label="t('config.editor.sourceMode')">
          <button
            type="button"
            role="tab"
            :aria-selected="sourceMode === 'yaml'"
            :disabled="busy || needsVerification"
            @click="enterYamlMode"
          >
            {{ t("config.editor.yamlMode") }}
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="sourceMode === 'structured'"
            :disabled="busy || needsVerification"
            @click="enterStructuredMode"
          >
            {{ t("config.editor.structuredMode") }}
          </button>
        </div>
        <template v-if="sourceMode === 'yaml'">
          <label for="agent-manifest-source">{{ t("config.editor.source") }}</label>
          <textarea
            id="agent-manifest-source"
            ref="sourceField"
            v-model="draft"
            data-autofocus
            :readonly="!editing || needsVerification"
            :disabled="busy"
            spellcheck="false"
            rows="20"
            :aria-describedby="
              validation && !validation.valid ? 'manifest-validation-errors' : undefined
            "
          />
        </template>
        <template v-else>
          <p role="status">{{ t("config.editor.structuredHint") }}</p>
          <AgentManifestForm
            :form="structured"
            :lock-identity="true"
            :disabled="!editing || busy || needsVerification"
            @add-ability="addAbility"
            @remove-ability="removeAbility"
            @add-hook="addHook"
            @remove-hook="removeHook"
          />
        </template>
        <p v-if="dirty">{{ t("config.editor.modified") }}</p>
        <p v-if="validation?.valid" role="status">{{ t("config.editor.valid") }}</p>
        <div v-if="validation && !validation.valid" id="manifest-validation-errors" role="alert">
          <p>{{ t("common.validationFailed") }}</p>
          <ul>
            <li v-for="(item, index) in validation.errors" :key="index">{{ item }}</li>
          </ul>
        </div>
        <p v-if="saved" role="status">{{ t("config.editor.saved") }}</p>
        <section
          v-if="preview"
          ref="previewPanel"
          tabindex="-1"
          role="region"
          :aria-label="t('config.preview.title')"
        >
          <AgentManifestPreview :preview="preview" />
        </section>
      </template>
    </div>
    <template #footer>
      <button type="button" class="btn-secondary" :disabled="closeBlocked" @click="requestClose">
        {{ t("common.close") }}
      </button>
      <button
        v-if="original === null"
        type="button"
        class="btn-secondary"
        :disabled="busy || !connected"
        @click="load"
      >
        {{ t("config.editor.retryLoad") }}
      </button>
      <button
        v-else-if="needsVerification"
        type="button"
        class="btn-primary"
        :disabled="busy || !connected"
        @click="verify()"
      >
        {{ t("config.editor.verifySave") }}
      </button>
      <template v-else>
        <button
          v-if="!editing"
          type="button"
          class="btn-secondary"
          :disabled="busy"
          @click="editing = true"
        >
          {{ t("config.agent.editYaml") }}
        </button>
        <button type="button" class="btn-secondary" :disabled="!canValidate" @click="validate">
          {{ t("config.editor.validate") }}
        </button>
        <button type="button" class="btn-secondary" :disabled="!canValidate" @click="previewDraft">
          {{ t("config.preview.title") }}
        </button>
        <button v-if="editing" type="button" class="btn-primary" :disabled="!canSave" @click="save">
          {{ t("config.editor.save") }}
        </button>
      </template>
    </template>
  </BaseModal>
  <ConfirmDialog
    v-if="confirmDiscardOpen"
    :title="t('config.unsavedTitle')"
    :message="t('config.unsavedConfirm')"
    :confirm-label="t('config.discardChanges')"
    danger
    @cancel="confirmDiscardOpen = false"
    @confirm="finishClose"
  />
</template>

<style scoped>
.manifest-editor {
  display: grid;
  gap: var(--spacing-2);
  font-size: var(--font-ui-body);
}
.manifest-editor p {
  margin: 0;
}
.manifest-editor textarea {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  resize: vertical;
  padding: var(--spacing-3);
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  font-family: ui-monospace, monospace;
  font-size: var(--font-ui-code);
}
.source-tabs {
  display: flex;
  gap: var(--spacing-1);
}
.source-tabs button {
  padding: var(--spacing-1) var(--spacing-2);
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  font-size: var(--font-ui-body);
}
.source-tabs button[aria-selected="true"] {
  font-weight: 600;
}
</style>
