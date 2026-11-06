<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { parseDocument } from "yaml";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

const props = defineProps<{ fullName: string }>();
const emit = defineEmits<{ close: []; saved: []; dirtyChange: [dirty: boolean] }>();
const { getAgent, putAgent, validateManifest } = useObserver();
const { t } = useI18n();
const source = ref("");
const draft = ref("");
const loaded = ref(false);
const busy = ref(false);
const error = ref("");
const status = ref("");
const confirmDiscard = ref(false);
const dirty = computed(() => loaded.value && draft.value !== source.value);
watch(dirty, (value) => emit("dirtyChange", value));

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const result = await getAgent(props.fullName);
    if (!result) throw new Error(t("config.agent.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.agent.loadFailed"));
    source.value = (result.data as { source: string }).source;
    draft.value = source.value;
    loaded.value = true;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.agent.loadFailed");
  } finally {
    busy.value = false;
  }
}
function close() {
  emit("dirtyChange", false);
  emit("close");
}
function requestClose() {
  if (busy.value) return;
  if (dirty.value) confirmDiscard.value = true;
  else close();
}
async function submit(save: boolean) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    const document = parseDocument(draft.value);
    if (document.errors.length) throw new Error(document.errors.map((e) => e.message).join("\n"));
    const data = document.toJS();
    if (data?.manifest !== "agent") throw new Error(t("config.agent.agentRequired"));
    if (`${data.metadata?.namespace}.${data.metadata?.name}` !== props.fullName)
      throw new Error(t("config.agent.identityUnchanged"));
    const result = await validateManifest(draft.value, "agent");
    if (!result) throw new Error(t("config.agent.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.agent.validationFailed"));
    const validation = result.data as { valid: boolean; errors: string[] };
    if (!validation.valid)
      throw new Error(validation.errors.join("\n") || t("config.agent.validationFailed"));
    if (!save) {
      status.value = t("config.agent.valid");
      return;
    }
    const saved = await putAgent(props.fullName, draft.value, true);
    if (!saved) throw new Error(t("config.agent.notConnected"));
    if (!saved.success) throw new Error(saved.error || t("config.agent.saveFailed"));
    source.value = (saved.data as { source: string }).source;
    draft.value = source.value;
    status.value = t("config.agent.saved");
    emit("dirtyChange", false);
    emit("saved");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.agent.saveFailed");
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <BaseModal :title="t('config.agent.editYaml')" size="lg" @request-close="requestClose">
    <div class="agent-yaml-editor" :aria-busy="busy">
      <code>{{ fullName }}</code>
      <p class="agent-yaml-hint">{{ t("config.agent.editHint") }}</p>
      <label v-if="loaded" for="agent-manifest-yaml">{{ t("config.agent.yaml") }}</label>
      <textarea
        v-if="loaded"
        id="agent-manifest-yaml"
        v-model="draft"
        rows="22"
        spellcheck="false"
        :disabled="busy"
        @input="
          error = '';
          status = '';
        "
      />
      <p v-if="busy" role="status">{{ t("common.loading") }}</p>
      <p v-if="error" class="agent-yaml-message" role="alert">{{ error }}</p>
      <p v-if="status" role="status">{{ status }}</p>
      <button v-if="!loaded && !busy" class="btn-secondary" @click="load">
        {{ t("config.agent.retry") }}
      </button>
    </div>
    <template #footer>
      <button class="btn-secondary" :disabled="busy" @click="requestClose">
        {{ t("common.cancel") }}
      </button>
      <button class="btn-secondary" :disabled="busy || !loaded" @click="submit(false)">
        {{ t("config.agent.validate") }}
      </button>
      <button class="btn-primary" :disabled="busy || !loaded || !dirty" @click="submit(true)">
        {{ t("config.agent.save") }}
      </button>
    </template>
  </BaseModal>
  <ConfirmDialog
    v-if="confirmDiscard"
    :title="t('config.unsavedTitle')"
    :message="t('config.unsavedConfirm')"
    :confirm-label="t('config.discardChanges')"
    danger
    @cancel="confirmDiscard = false"
    @confirm="close"
  />
</template>

<style scoped>
.agent-yaml-editor {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}
.agent-yaml-hint {
  color: var(--ac-node-subtext);
  font-size: var(--font-ui-note);
}
.agent-yaml-editor textarea {
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
.agent-yaml-editor textarea:focus-visible {
  outline: 2px solid var(--ac-read);
}
.agent-yaml-message {
  white-space: pre-wrap;
}
</style>
