<script setup lang="ts">
import { extractManifestEntry, extractValidationResult } from "@ghrah/observer-core";
import { load as parseYaml } from "js-yaml";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

const props = withDefaults(defineProps<{ fullName?: string; initialMode?: "view" | "edit" }>(), {
  fullName: undefined,
  initialMode: "edit",
});
const emit = defineEmits<{ close: []; dirtyChange: [dirty: boolean]; saved: [fullName: string] }>();
const { t } = useI18n();
const {
  connection,
  getAbility,
  putAbility,
  validateManifest,
  error: observerError,
} = useObserver();
const template = `manifest: ability
version: "1"
metadata:
  namespace: custom
  name: my_ability
  title: ""
  description: ""
  tags: []
  permissions:
    require_hitl: false
    fs_read_only: false
    fs_write: false
    net_access: false
    shell_access: false
    allowed_paths: []
    denied_paths: []
    allowed_commands: []
    denied_commands: []
tool: null
implementation:
  type: builtin
  handler: ""
hooks:
  pre_execute: []
  post_execute: []
  on_error: []
`;
const target = ref(props.fullName ?? "");
const editing = ref(props.initialMode === "edit");
const original = ref<string | null>(props.fullName ? null : template);
const draft = ref(props.fullName ? "" : template);
const sourceHash = ref<string | null>(null);
const phase = ref<"loading" | "validating" | "saving" | "verifying" | null>(null);
const error = ref("");
const validation = ref<{ valid: boolean; errors: string[] } | null>(null);
const saved = ref(false);
const needsVerification = ref(false);
const conflict = ref(false);
const confirmDiscardOpen = ref(false);
const confirmReloadOpen = ref(false);
const sourceField = ref<HTMLTextAreaElement | null>(null);
let submitted: string | null = null;
let epoch = 0;
const connected = computed(() => connection.state === "connected");
const dirty = computed(() => original.value !== null && draft.value !== original.value);
const busy = computed(() => phase.value !== null);
const guarded = computed(() => dirty.value || needsVerification.value || busy.value);
const closeBlocked = computed(() => busy.value && phase.value !== "loading");
const canValidate = computed(
  () => connected.value && original.value !== null && !busy.value && !needsVerification.value,
);
const canSave = computed(
  () => canValidate.value && editing.value && dirty.value && !conflict.value,
);
watch(guarded, (value) => emit("dirtyChange", value));
watch(draft, () => {
  validation.value = null;
  saved.value = false;
  error.value = "";
});
function failure(fallback: string, message?: string | null) {
  return new Error(message ?? observerError.value ?? t(fallback));
}
async function readSource() {
  const result = await getAbility(target.value);
  if (!result?.success) throw failure("config.editor.loadFailed", result?.error);
  const entry = extractManifestEntry(result.data, { kind: "ability", fullName: target.value });
  if (!entry) throw new Error(t("common.invalidResponse"));
  return entry.detail;
}
async function load() {
  if (busy.value || !connected.value || !target.value) return;
  const current = ++epoch;
  phase.value = "loading";
  error.value = "";
  try {
    const detail = await readSource();
    if (current !== epoch) return;
    draft.value = detail.source;
    original.value = detail.source;
    sourceHash.value = detail.source_hash;
    conflict.value = false;
    needsVerification.value = false;
    await nextTick();
    sourceField.value?.focus();
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}
async function check(source: string, current: number) {
  const result = await validateManifest(source, "ability");
  if (current !== epoch) return false;
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
  try {
    await check(draft.value, current);
  } catch (cause) {
    if (current === epoch) error.value = (cause as Error).message;
  } finally {
    if (current === epoch) phase.value = null;
  }
}
function draftIdentity(source: string): string {
  const value = parseYaml(source) as { metadata?: { namespace?: unknown; name?: unknown } } | null;
  const metadata = value?.metadata;
  if (typeof metadata?.namespace !== "string" || typeof metadata?.name !== "string")
    throw new Error(t("common.invalidResponse"));
  return `${metadata.namespace}.${metadata.name}`;
}
async function verify(current = epoch) {
  if (!connected.value) return;
  phase.value = "verifying";
  error.value = "";
  try {
    const detail = await readSource();
    if (current !== epoch) return;
    needsVerification.value = false;
    if (detail.source !== submitted) {
      // Keep both the draft and its original baseline; a newer server file requires explicit reload.
      conflict.value = true;
      error.value = t("config.editor.differentSource");
      return;
    }
    draft.value = detail.source;
    original.value = detail.source;
    sourceHash.value = detail.source_hash;
    await nextTick();
    if (current !== epoch) return;
    saved.value = true;
    emit("saved", target.value);
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
  try {
    if (!(await check(source, current))) return;
    const fullName = draftIdentity(source);
    if (target.value && fullName !== target.value) throw new Error(t("config.editor.identityHint"));
    const overwrite = sourceHash.value !== null;
    phase.value = "saving";
    submitted = source;
    const result = await putAbility(fullName, source, overwrite, sourceHash.value ?? undefined);
    if (current !== epoch) return;
    if (!result) {
      target.value = fullName;
      needsVerification.value = true;
      error.value = t("config.editor.saveUncertain");
      return;
    }
    if (!result.success && result.error_detail?.includes("code=manifest_conflict")) {
      conflict.value = true;
      throw new Error(t("config.editor.conflict"));
    }
    if (!result.success) throw failure("config.editor.saveFailed", result.error);
    target.value = fullName;
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
function requestReload() {
  if (dirty.value) confirmReloadOpen.value = true;
  else void load();
}
function confirmReload() {
  confirmReloadOpen.value = false;
  void load();
}
function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!guarded.value) return;
  event.preventDefault();
  event.returnValue = "";
}
onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  if (props.fullName) void load();
});
onBeforeUnmount(() => {
  epoch++;
  window.removeEventListener("beforeunload", onBeforeUnload);
});
</script>

<template>
  <BaseModal
    :title="
      t(
        !target
          ? 'config.ability.createTitle'
          : editing
            ? 'config.ability.editTitle'
            : 'config.editor.viewTitle',
      )
    "
    size="lg"
    :show-close="!closeBlocked"
    @request-close="requestClose"
  >
    <template #subtitle
      ><p>{{ target }}</p></template
    >
    <div class="ability-editor">
      <p>{{ t("config.ability.legacyHint") }}</p>
      <p>{{ t("config.editor.fileHint") }}</p>
      <p v-if="sourceHash">{{ t("config.editor.identityHint") }}</p>
      <p v-if="!connected" role="alert">{{ t("config.manifest.notConnected") }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="phase" role="status">{{ t(`config.editor.${phase}`) }}</p>
      <template v-if="original !== null">
        <label for="ability-manifest-source">{{ t("config.editor.source") }}</label>
        <textarea
          id="ability-manifest-source"
          ref="sourceField"
          v-model="draft"
          data-autofocus
          :readonly="!editing || needsVerification"
          :disabled="busy"
          rows="20"
          spellcheck="false"
        />
        <p v-if="dirty">{{ t("config.editor.modified") }}</p>
        <p v-if="validation?.valid" role="status">{{ t("config.editor.valid") }}</p>
        <div v-if="validation && !validation.valid" role="alert">
          <p>{{ t("common.validationFailed") }}</p>
          <ul>
            <li v-for="(item, index) in validation.errors" :key="index">{{ item }}</li>
          </ul>
        </div>
        <p v-if="saved" role="status">{{ t("config.editor.saved") }}</p>
      </template>
    </div>
    <template #footer>
      <button type="button" class="btn-secondary" :disabled="closeBlocked" @click="requestClose">
        {{ t("common.close") }}
      </button>
      <button
        v-if="original === null || conflict"
        type="button"
        class="btn-secondary"
        :disabled="busy || !connected"
        @click="requestReload"
      >
        {{ t("config.editor.retryLoad") }}
      </button>
      <button
        v-if="needsVerification"
        type="button"
        class="btn-primary"
        :disabled="busy || !connected"
        @click="verify()"
      >
        {{ t("config.editor.verifySave") }}
      </button>
      <template v-else-if="original !== null">
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
        <button v-if="editing" type="button" class="btn-primary" :disabled="!canSave" @click="save">
          {{ t("config.editor.save") }}
        </button>
      </template>
    </template>
  </BaseModal>
  <ConfirmDialog
    v-if="confirmDiscardOpen || confirmReloadOpen"
    :title="t('config.unsavedTitle')"
    :message="t('config.unsavedConfirm')"
    :confirm-label="t('config.discardChanges')"
    danger
    @cancel="
      confirmDiscardOpen = false;
      confirmReloadOpen = false;
    "
    @confirm="confirmReloadOpen ? confirmReload() : finishClose()"
  />
</template>

<style scoped>
.ability-editor {
  display: grid;
  gap: var(--spacing-2);
  font-size: var(--font-ui-body);
}
.ability-editor p {
  margin: 0;
}
.ability-editor textarea {
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
</style>
