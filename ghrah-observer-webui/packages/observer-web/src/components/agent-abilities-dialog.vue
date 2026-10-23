<script setup lang="ts">
import { type AgentTarget, useConnectionStore, useProjectsStore } from "@ghrah/observer-core";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import { useObserver } from "@/composables/useObserver";

const props = defineProps<{ target: AgentTarget }>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const connection = useConnectionStore();
const projects = useProjectsStore();
const { getAgentInfo, registerAbility, error: observerError } = useObserver();
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
      <div class="base-modal-body agent-abilities-body">
        <p>{{ t("agentAbilities.runtimeHint") }}</p>
        <p v-if="error" role="alert">{{ error }}</p>
        <p v-if="success" role="status">{{ success }}</p>
        <button type="button" class="btn-secondary" :disabled="loading || saving" @click="refresh">
          {{ t("agentAbilities.refresh") }}
        </button>
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
  </Teleport>
</template>

<style scoped>
.agent-abilities-body {
  font-size: var(--font-ui-body);
}
.agent-abilities-list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-2);
  padding: 0;
  list-style: none;
}
.agent-abilities-list li {
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
}
form,
label {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}
form {
  margin-block-start: var(--spacing-4);
}
select,
textarea {
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
  min-width: 0;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
