<script setup lang="ts">
import { type AgentTarget, useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "./ui/confirm-dialog.vue";
import { useObserver } from "@/composables/useObserver";

const props = defineProps<{ target: AgentTarget }>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const connection = useConnectionStore();
const projects = useProjectsStore();
const { getAgentInfo, registerAbility, unregisterAbility, error: observerError } = useObserver();
const abilities = ref<string[]>([]);
const availableTypes = ref<string[]>([]);
const definitions = ref<unknown[]>([]);
const stopped = ref(false);
const loaded = ref(false);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const success = ref("");
const abilityType = ref("");
const removeTarget = ref<string | null>(null);
const mutationUncertain = ref(false);
const paramsText = ref("{}");
const params = computed<Record<string, unknown> | null>(() => {
  try {
    const value: unknown = JSON.parse(paramsText.value);
    return value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
});
const canRegister = computed(
  () =>
    loaded.value &&
    !mutationUncertain.value &&
    !stopped.value &&
    !loading.value &&
    !saving.value &&
    connection.state === "connected" &&
    projects.projects.get(props.target.projectId)?.status === "active" &&
    availableTypes.value.includes(abilityType.value) &&
    params.value !== null,
);

async function refresh() {
  loading.value = true;
  loaded.value = false;
  error.value = "";
  try {
    const result = await getAgentInfo(props.target);
    if (!result || !result.success) {
      error.value = result?.error ?? observerError.value ?? t("agentAbilities.loadFailed");
      return;
    }
    const data = result.data as {
      abilities?: unknown;
      available_ability_types?: unknown;
      runtime_state?: string;
    } | null;
    if (!data || !Array.isArray(data.abilities)) {
      error.value = t("agentAbilities.loadFailed");
      return;
    }
    stopped.value = data.runtime_state === "stopped";
    definitions.value = stopped.value ? data.abilities : [];
    if (!stopped.value && !data.abilities.every((item) => typeof item === "string")) {
      error.value = t("agentAbilities.loadFailed");
      return;
    }
    abilities.value = stopped.value ? [] : (data.abilities as string[]);
    if (
      !stopped.value &&
      (!Array.isArray(data.available_ability_types) ||
        !data.available_ability_types.every((item) => typeof item === "string"))
    ) {
      error.value = t("agentAbilities.typesLoadFailed");
      return;
    }
    availableTypes.value = stopped.value ? [] : (data.available_ability_types as string[]);
    if (!availableTypes.value.includes(abilityType.value)) abilityType.value = "";
    loaded.value = true;
    mutationUncertain.value = false;
  } catch (cause) {
    error.value = String(cause);
  } finally {
    loading.value = false;
  }
}

async function register() {
  if (!canRegister.value || !params.value) return;
  saving.value = true;
  error.value = "";
  success.value = "";
  try {
    const result = await registerAbility(props.target, {
      ability_type: abilityType.value.trim(),
      params: params.value,
    });
    if (!result || !result.success) {
      error.value = result?.error ?? observerError.value ?? t("agentAbilities.registerFailed");
      return;
    }
    success.value = t("agentAbilities.registered", { ability: abilityType.value.trim() });
    await refresh();
  } catch (cause) {
    error.value = String(cause);
  } finally {
    saving.value = false;
  }
}
async function unregister() {
  const ability = removeTarget.value;
  removeTarget.value = null;
  if (
    !ability ||
    mutationUncertain.value ||
    !loaded.value ||
    stopped.value ||
    saving.value ||
    loading.value ||
    connection.state !== "connected"
  )
    return;
  saving.value = true;
  mutationUncertain.value = true;
  error.value = "";
  success.value = "";
  try {
    const result = await unregisterAbility(props.target, ability);
    if (!result?.success) {
      if (result) mutationUncertain.value = false;
      error.value = result?.error ?? observerError.value ?? t("commonConfig.unregisterFailed");
      return;
    }
    await refresh();
    if (!loaded.value || abilities.value.includes(ability)) {
      error.value = t("commonConfig.unregisterVerify");
      return;
    }
    success.value = t("commonConfig.unregistered", { ability });
  } catch (cause) {
    error.value = String(cause);
  } finally {
    saving.value = false;
  }
}
onMounted(refresh);
</script>

<template>
  <Teleport to="body">
    <BaseModal
      :title="t('agentAbilities.title', { agent: target.agentName })"
      @request-close="emit('close')"
    >
      <template #subtitle
        ><p>{{ target.agentId }}</p></template
      >
      <div class="agent-abilities-body">
        <div class="abilities-toolbar">
          <p class="abilities-hint">{{ t("agentAbilities.runtimeHint") }}</p>
          <button
            type="button"
            class="btn-secondary"
            :disabled="loading || saving"
            @click="refresh"
          >
            {{ t("agentAbilities.refresh") }}
          </button>
        </div>
        <p v-if="error" role="alert">{{ error }}</p>
        <p v-if="success" role="status">{{ success }}</p>
        <p v-if="loading">{{ t("common.loading") }}</p>
        <template v-else-if="loaded">
          <h3>{{ t(stopped ? "agentAbilities.definitions" : "agentAbilities.registeredList") }}</h3>
          <template v-if="stopped">
            <p>{{ t("agentAbilities.stoppedHint") }}</p>
            <pre>{{ JSON.stringify(definitions, null, 2) }}</pre>
          </template>
          <ul v-else-if="abilities.length" class="agent-abilities-list">
            <li v-for="ability in abilities" :key="ability">
              <code>{{ ability }}</code>
              <button
                class="btn ability-remove text-red-600 dark:text-red-400"
                :disabled="
                  saving ||
                  mutationUncertain ||
                  loading ||
                  connection.state !== 'connected' ||
                  projects.projects.get(target.projectId)?.status !== 'active'
                "
                @click="removeTarget = ability"
              >
                {{ t("commonConfig.unregister") }}
              </button>
            </li>
          </ul>
          <p v-else>{{ t("agentAbilities.empty") }}</p>
        </template>
        <form @submit.prevent="register">
          <h3>{{ t("agentAbilities.registerTitle") }}</h3>
          <label
            >{{ t("agentAbilities.type")
            }}<select v-model="abilityType" :disabled="saving || loading || !loaded || stopped">
              <option value="" disabled>{{ t("agentAbilities.chooseType") }}</option>
              <option v-for="type in availableTypes" :key="type" :value="type">{{ type }}</option>
            </select>
          </label>
          <p>{{ t("agentAbilities.typesHint") }}</p>
          <label
            >{{ t("agentAbilities.params")
            }}<textarea v-model="paramsText" :disabled="saving" rows="4" spellcheck="false" />
          </label>
          <p v-if="params === null" role="alert">{{ t("agentAbilities.invalidParams") }}</p>
          <p>{{ t("agentAbilities.scopeHint") }}</p>
          <p v-if="connection.state !== 'connected'">{{ t("agents.notConnected") }}</p>
          <button type="submit" class="btn-primary" :disabled="!canRegister">
            {{ t(saving ? "agentAbilities.registering" : "agentAbilities.register") }}
          </button>
        </form>
      </div>
    </BaseModal>
    <ConfirmDialog
      v-if="removeTarget"
      :title="t('commonConfig.unregister')"
      :message="t('commonConfig.unregisterHint', { ability: removeTarget })"
      :confirm-label="t('commonConfig.unregister')"
      danger
      @cancel="removeTarget = null"
      @confirm="unregister"
    />
  </Teleport>
</template>

<style scoped>
.agent-abilities-body {
  display: grid;
  gap: var(--spacing-4);
  font-size: var(--font-ui-body);
  min-width: 0;
}
p,
h3,
pre {
  margin: 0;
}
h3 {
  font-size: var(--font-ui-body);
  font-weight: 600;
}
.abilities-toolbar {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: var(--spacing-3);
}
.abilities-hint {
  flex: 1 1 60%;
  color: var(--ac-node-subtext);
  line-height: 1.6;
}
.abilities-toolbar button {
  flex-shrink: 0;
}
.agent-abilities-list {
  display: grid;
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
.agent-abilities-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--spacing-3);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-3) var(--spacing-4);
  background: var(--ac-node-bg);
}
.agent-abilities-list code {
  font-size: var(--font-ui-code);
  overflow-wrap: anywhere;
  min-width: 0;
}
.ability-remove {
  flex-shrink: 0;
  margin-inline-start: auto;
  background: transparent;
  border: 1px solid var(--ac-node-border);
  font-size: var(--font-ui-note);
  padding: var(--spacing-2) var(--spacing-3);
}
.ability-remove:hover:not(:disabled) {
  background: color-mix(in srgb, currentColor 8%, transparent);
  border-color: currentColor;
}
form,
label {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}
form {
  gap: var(--spacing-3);
  padding-block-start: var(--spacing-4);
  border-top: 1px solid var(--ac-node-border);
}
form p {
  color: var(--ac-node-subtext);
  line-height: 1.6;
}
form button {
  align-self: flex-start;
}
select,
textarea {
  color: inherit;
  background: transparent;
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
  min-width: 0;
  font: inherit;
}
textarea,
pre {
  font-size: var(--font-ui-code);
  line-height: 1.6;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
