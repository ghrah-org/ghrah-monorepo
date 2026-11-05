<script setup lang="ts">
import type { AgentTarget } from "@ghrah/observer-core";
import { ProjectInfoPayloadSchema } from "@ghrah/protocol";
import { computed, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import { useObserver } from "@/composables/useObserver";
import BaseModal from "./ui/base-modal.vue";
const props = defineProps<{ agent: AgentTarget; expectedVersion: number }>();
const emit = defineEmits<{ close: []; deleted: [] }>();
const { t } = useI18n();
const { client, connection, removeProjectAgent } = useObserver();
const typedName = ref("");
const busy = ref(false);
const error = ref("");
const attempted = ref(false);
const needsVerification = ref(false);
let active = true;
const connected = computed(() => connection.state === "connected" && client.value !== null);
const canDelete = computed(
  () =>
    connected.value && !busy.value && !attempted.value && typedName.value === props.agent.agentName,
);
function close() {
  if (!busy.value) emit("close");
}
async function verify() {
  if (!connected.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await client.value!.getProject(props.agent.projectId);
    if (!active) return;
    if (!result.success) throw new Error(result.error ?? t("agentDelete.verifyFailed"));
    const { project } = z.object({ project: ProjectInfoPayloadSchema }).parse(result.data);
    if (project.project_id !== props.agent.projectId) throw new Error(t("common.invalidResponse"));
    if (project.agents.some((a) => a.agent_id === props.agent.agentId)) {
      needsVerification.value = false;
      throw new Error(t("agentDelete.stillExists"));
    }
    emit("deleted");
  } catch (cause) {
    if (active) error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    if (active) busy.value = false;
  }
}
async function remove() {
  if (!canDelete.value) return;
  busy.value = true;
  attempted.value = true;
  error.value = "";
  try {
    const result = await removeProjectAgent(props.agent, props.expectedVersion);
    if (!active) return;
    if (!result) {
      needsVerification.value = true;
      error.value = t("agentDelete.uncertain");
      return;
    }
    if (!result.success) {
      error.value = [result.error, result.error_detail, t("agentDelete.refreshHint")]
        .filter(Boolean)
        .join("\n");
      return;
    }
    needsVerification.value = true;
  } catch (cause) {
    if (active) {
      needsVerification.value = true;
      error.value = (cause as Error).message;
    }
  } finally {
    if (active) busy.value = false;
  }
  if (active && needsVerification.value) await verify();
}
onBeforeUnmount(() => {
  active = false;
});
</script>

<template>
  <BaseModal :title="t('agentDelete.title')" size="md" :show-close="!busy" @request-close="close">
    <div class="agent-delete">
      <p>{{ t("agentDelete.scope") }}</p>
      <dl>
        <dt>{{ t("agentEntity.name") }}</dt>
        <dd>{{ agent.agentName }}</dd>
        <dt>{{ t("agentDelete.identity") }}</dt>
        <dd>{{ agent.projectId }} / {{ agent.agentId }}</dd>
      </dl>
      <p>{{ t("agentDelete.retained") }}</p>
      <p v-if="!connected" role="alert">{{ t("agents.notConnected") }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="needsVerification" role="status">{{ t("agentDelete.verifyHint") }}</p>
      <label v-if="!attempted"
        >{{ t("agentDelete.typeName") }}<input v-model="typedName" :disabled="busy" data-autofocus
      /></label>
      <p v-if="busy" role="status">{{ t("common.loading") }}</p>
    </div>
    <template #footer>
      <button class="btn-secondary" :disabled="busy" @click="close">
        {{ t("common.cancel") }}
      </button>
      <button
        v-if="needsVerification"
        class="btn-primary"
        :disabled="busy || !connected"
        @click="verify"
      >
        {{ t("agentDelete.check") }}
      </button>
      <button v-else-if="!attempted" class="btn-danger" :disabled="!canDelete" @click="remove">
        {{ t("agentDelete.confirm") }}
      </button>
    </template>
  </BaseModal>
</template>
<style scoped>
.agent-delete {
  display: grid;
  gap: var(--spacing-3);
  font-size: var(--font-ui-body);
}
.agent-delete p,
.agent-delete dl,
.agent-delete dd {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.agent-delete dl,
.agent-delete label {
  display: grid;
  gap: var(--spacing-2);
}
.agent-delete input {
  font: inherit;
  padding: var(--spacing-2);
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  color: inherit;
  background: transparent;
  min-width: 0;
}
</style>
