<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { parseDocument } from "yaml";
import { useObserver } from "@/composables/useObserver";

const emit = defineEmits<{ cancel: []; created: [fullName: string] }>();
const { t } = useI18n();
const { validateManifest, putAbility } = useObserver();
const draft = ref(`manifest: ability
version: '1'
metadata:
  namespace: my_project
  name: new_ability
  title: ''
  description: ''
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
implementation:
  type: builtin
  handler: ''
`);
const busy = ref(false);
const error = ref("");
const status = ref("");

async function submit(save: boolean) {
  busy.value = true;
  error.value = "";
  status.value = "";
  try {
    const document = parseDocument(draft.value);
    if (document.errors.length) throw new Error(document.errors.map((e) => e.message).join("\n"));
    const data = document.toJS();
    if (data?.manifest !== "ability") throw new Error(t("config.ability.abilityRequired"));
    const result = await validateManifest(draft.value, "ability");
    if (!result) throw new Error(t("config.ability.notConnected"));
    if (!result.success) throw new Error(result.error || t("config.ability.validationFailed"));
    const validation = result.data as { valid: boolean; errors: string[] };
    if (!validation.valid)
      throw new Error(validation.errors.join("\n") || t("config.ability.validationFailed"));
    if (data.implementation.type === "builtin" && !data.implementation.handler?.trim())
      throw new Error(t("config.ability.handlerRequired"));
    if (!save) {
      status.value = t("config.ability.valid");
      return;
    }
    const fullName = `${data.metadata.namespace}.${data.metadata.name}`;
    const saved = await putAbility(fullName, draft.value, false);
    if (!saved) throw new Error(t("config.ability.notConnected"));
    if (!saved.success) throw new Error(saved.error || t("config.ability.saveFailed"));
    emit("created", fullName);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t("config.ability.saveFailed");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="new-ability" :aria-busy="busy" :aria-label="t('config.ability.create')">
    <h3 class="font-semibold">{{ t("config.ability.create") }}</h3>
    <p class="new-ability-hint">{{ t("config.ability.createHint") }}</p>
    <label for="new-ability-yaml">{{ t("config.ability.yaml") }}</label>
    <textarea
      id="new-ability-yaml"
      v-model="draft"
      rows="20"
      spellcheck="false"
      :disabled="busy"
      @input="
        error = '';
        status = '';
      "
    />
    <p v-if="error" class="new-ability-message" role="alert">{{ error }}</p>
    <p v-if="status" role="status">{{ status }}</p>
    <p v-if="busy" role="status">{{ t("common.loading") }}</p>
    <div class="new-ability-actions">
      <button class="btn-secondary" :disabled="busy" @click="submit(false)">
        {{ t("config.ability.validate") }}
      </button>
      <button class="btn-primary" :disabled="busy" @click="submit(true)">
        {{ t("config.ability.create") }}
      </button>
      <button class="btn-secondary" :disabled="busy" @click="emit('cancel')">
        {{ t("config.ability.cancel") }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.new-ability {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  background: var(--ac-node-bg);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
}
.new-ability-hint {
  color: var(--ac-node-subtext);
  font-size: var(--font-ui-note);
}
.new-ability textarea {
  width: 100%;
  box-sizing: border-box;
  padding: var(--spacing-3);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  background: var(--ac-node-bg);
  color: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: var(--font-ui-note);
  resize: vertical;
}
.new-ability textarea:focus-visible {
  outline: 2px solid var(--ac-read);
}
.new-ability-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-2);
}
.new-ability-message {
  white-space: pre-wrap;
}
</style>
