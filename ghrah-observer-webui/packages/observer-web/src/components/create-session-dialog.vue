<script setup lang="ts">
import type { AgentTarget } from "@ghrah/observer-core";
import { computed, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import { useObserver } from "@/composables/useObserver";
import BaseModal from "./ui/base-modal.vue";
import ConfirmDialog from "./ui/confirm-dialog.vue";

const props = defineProps<{ agent: AgentTarget; route: "entity" | "chain" }>();
const emit = defineEmits<{ close: []; created: [sessionId: string] }>();
const { t } = useI18n();
const { client, connection, createSession, listSessions } = useObserver();
const name = ref("");
const custom = ref(false);
const prompt = ref("");
const busy = ref(false);
const error = ref("");
const createdId = ref<string | null>(null);
const uncertain = ref(false);
const verifiedPrompt = ref<string | null>(null);
const confirmed = ref(false);
const discard = ref(false);
let alive = true;
let submittedPrompt: string | undefined;
const connected = computed(() => connection.state === "connected" && client.value !== null);
const dirty = computed(() => !!name.value || custom.value || !!prompt.value);
const sessionsSchema = z.object({
  sessions: z.array(z.object({ session_id: z.string(), system_prompt: z.string() })),
});

async function verify() {
  if (!createdId.value || busy.value || !connected.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await listSessions(props.agent);
    if (!alive) return;
    if (!result?.success) throw new Error(result?.error ?? t("sessionCreate.verifyFailed"));
    const session = sessionsSchema
      .parse(result.data)
      .sessions.find((s) => s.session_id === createdId.value);
    if (!session) throw new Error(t("sessionCreate.verifyFailed"));
    if (submittedPrompt !== undefined && session.system_prompt !== submittedPrompt)
      throw new Error(t("sessionCreate.promptMismatch"));
    verifiedPrompt.value = session.system_prompt;
    confirmed.value = true;
    emit("created", createdId.value);
  } catch (cause) {
    if (alive) error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    if (alive) busy.value = false;
  }
}
async function submit() {
  if (busy.value || !connected.value || createdId.value || uncertain.value) return;
  busy.value = true;
  error.value = "";
  submittedPrompt = custom.value ? prompt.value : undefined;
  try {
    let data: unknown;
    if (props.route === "entity") {
      data = await client
        .value!.agent(props.agent.projectId, props.agent.agentId)
        .context.createSession(name.value.trim() || undefined, submittedPrompt);
      data = z.object({ session: z.object({ session_id: z.string() }) }).parse(data).session;
    } else {
      const result = await createSession(props.agent, {
        name: name.value.trim() || undefined,
        systemPrompt: submittedPrompt,
      });
      if (!result) {
        uncertain.value = true;
        throw new Error(t("sessionCreate.uncertain"));
      }
      if (!result.success) throw new Error(result.error ?? t("agentEntity.failed"));
      data = result.data;
    }
    const id = z.object({ session_id: z.string().min(1) }).parse(data).session_id;
    if (!alive) return;
    createdId.value = id;
  } catch (cause) {
    if (alive) {
      // Entity transport errors and malformed receipts cannot establish whether creation occurred.
      if (props.route === "entity" || cause instanceof z.ZodError) uncertain.value = true;
      error.value = cause instanceof Error ? cause.message : String(cause);
    }
  } finally {
    if (alive) busy.value = false;
  }
  if (alive && createdId.value) await verify();
}
function close() {
  if (busy.value) return;
  if (!confirmed.value && (dirty.value || createdId.value || uncertain.value)) discard.value = true;
  else emit("close");
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (!confirmed.value && (dirty.value || busy.value || createdId.value || uncertain.value)) {
    event.preventDefault();
    event.returnValue = "";
  }
}
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => {
  alive = false;
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<template>
  <BaseModal
    :title="t('agentEntity.createSession')"
    size="lg"
    :show-close="!busy"
    @request-close="close"
  >
    <div class="session-create">
      <p>{{ agent.agentName }}</p>
      <p>{{ t("sessionCreate.scope") }}</p>
      <p v-if="!connected" role="alert">{{ t("agents.notConnected") }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="uncertain" role="alert">{{ t("sessionCreate.uncertain") }}</p>
      <template v-if="!confirmed">
        <label
          >{{ t("agentEntity.sessionName")
          }}<input
            v-model="name"
            :disabled="busy"
            :readonly="!!createdId || uncertain"
            data-autofocus
        /></label>
        <label class="session-create-check"
          ><input v-model="custom" type="checkbox" :disabled="busy || !!createdId || uncertain" />{{
            t("sessionCreate.custom")
          }}</label
        >
        <p>{{ t("sessionCreate.defaultHint") }}</p>
        <label v-if="custom"
          >{{ t("agentEntity.prompt")
          }}<textarea
            v-model="prompt"
            :disabled="busy"
            :readonly="!!createdId || uncertain"
            rows="10"
            spellcheck="false"
          />
        </label>
        <p v-if="custom">{{ t("sessionCreate.emptyHint") }}</p>
      </template>
      <p v-if="createdId">{{ t("sessionCreate.identity", { id: createdId }) }}</p>
      <p v-if="confirmed" role="status">{{ t("sessionCreate.verified") }}</p>
      <label v-if="confirmed"
        >{{ t("sessionCreate.savedPrompt") }}<textarea :value="verifiedPrompt" readonly rows="10" />
      </label>
      <p v-if="confirmed && verifiedPrompt === ''">{{ t("sessionCreate.emptySaved") }}</p>
      <p v-if="busy" role="status">{{ t("common.loading") }}</p>
    </div>
    <template #footer>
      <button class="btn-secondary" :disabled="busy" @click="close">{{ t("common.close") }}</button>
      <button
        v-if="createdId && !confirmed"
        class="btn-primary"
        :disabled="busy || !connected"
        @click="verify"
      >
        {{ t("sessionCreate.verify") }}
      </button>
      <button
        v-else-if="!confirmed && !uncertain"
        class="btn-primary"
        :disabled="busy || !connected"
        @click="submit"
      >
        {{ t("agentEntity.createSession") }}
      </button>
    </template>
  </BaseModal>
  <ConfirmDialog
    v-if="discard"
    :title="t('config.unsavedTitle')"
    :message="t(createdId || uncertain ? 'sessionCreate.closeUnverified' : 'config.unsavedConfirm')"
    :confirm-label="t('common.close')"
    @cancel="discard = false"
    @confirm="emit('close')"
  />
</template>

<style scoped>
.session-create,
.session-create label {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
  font-size: var(--font-ui-body);
  min-width: 0;
}
.session-create p {
  margin: 0;
  overflow-wrap: anywhere;
}
.session-create .session-create-check {
  flex-direction: row;
  align-items: center;
}
.session-create textarea,
.session-create input {
  color: inherit;
  background: transparent;
  font: inherit;
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
  min-width: 0;
}
.session-create textarea {
  font-family: ui-monospace, monospace;
  font-size: var(--font-ui-code);
  resize: vertical;
}
</style>
