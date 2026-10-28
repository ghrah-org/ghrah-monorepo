<script setup lang="ts">
import { useProjectsStore } from "@ghrah/observer-core";
import { type AgentSnapshot, AgentSnapshotSchema } from "@ghrah/protocol";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import BaseModal from "./ui/base-modal.vue";
import { useAgentEntityRequest } from "@/composables/useAgentEntity";

const props = defineProps<{ projectId: string; agentId?: string }>();
const emit = defineEmits<{ close: []; changed: []; created: [agentId: string] }>();
const { t } = useI18n();
const projects = useProjectsStore();
const { client, connected, busy, error, uncertain, run, stopWaiting } = useAgentEntityRequest();
const snapshot = ref<AgentSnapshot | null>(null);
const tab = ref<"overview" | "context" | "mailbox" | "abilities">("overview");
const tabs = ["overview", "context", "mailbox", "abilities"] as const;
const types = ref<string[]>([]);
const selectedTypes = ref<string[]>([]);
const modelName = ref("");
const modelNames = ref<string[]>([]);
const modelCatalogError = ref("");
const name = ref("");
const cluster = ref("");
const prompt = ref("");
const startNow = ref(false);
const source = ref<"types" | "manifest">("types");
const manifest = ref("");
const success = ref("");
const confirmation = ref<{ label: string; action: () => Promise<unknown> } | null>(null);
const project = computed(() => projects.projects.get(props.projectId));
const entity = () => client.value!.agent(props.projectId, props.agentId!);
const active = computed(() => project.value?.status === "active" && !project.value.archived_at);
const writable = computed(() => connected.value && !busy.value && !uncertain.value);
const canContext = computed(
  () =>
    writable.value &&
    active.value &&
    snapshot.value?.runtime.availability === "online" &&
    !snapshot.value.runtime.busy &&
    snapshot.value.specification.desired_state === "running",
);
const canConfigure = computed(
  () =>
    writable.value &&
    snapshot.value?.runtime.availability === "offline" &&
    snapshot.value.specification.desired_state === "stopped" &&
    !snapshot.value.specification.manifest_ref,
);
const canCreate = computed(
  () =>
    writable.value &&
    !!name.value.trim() &&
    !!cluster.value &&
    (!startNow.value || active.value) &&
    (source.value === "manifest"
      ? !!manifest.value.trim()
      : selectedTypes.value.length > 0 && !!modelName.value),
);
function stringArrayOrNull(value: unknown): string[] | null {
  const parsed = z.array(z.string()).safeParse(value);
  return parsed.success ? parsed.data : null;
}
const abilityView = computed(() => {
  const values = snapshot.value?.abilities;
  return {
    configured: stringArrayOrNull(values?.configured_types),
    loaded: stringArrayOrNull(values?.loaded_names),
  };
});

const catalogSchema = z.object({
  active_session_id: z.string().nullable(),
  sessions: z.array(
    z.object({
      session_id: z.string(),
      name: z.string().nullable(),
      active_branch_id: z.string(),
      lifecycle: z.string(),
    }),
  ),
  branches: z.array(
    z.object({
      branch_id: z.string(),
      session_id: z.string(),
      name: z.string(),
      lifecycle: z.string(),
    }),
  ),
});
const catalog = ref<z.infer<typeof catalogSchema> | null>(null);
const sessionId = ref("");
const branchId = ref("");
const sessionName = ref("");
const branches = computed(
  () => catalog.value?.branches.filter((b) => b.session_id === sessionId.value) ?? [],
);
const nodes = ref<
  Array<{ id: string; iteration: number; messages_delta?: unknown; action_results?: unknown }>
>([]);
const historyLoaded = ref(false);
const direction = ref<"inbox" | "outbox">("inbox");
const mailboxSchema = z.object({
  items: z.array(
    z.object({
      message: z.object({
        message_id: z.string(),
        sender_id: z.string(),
        content: z.string(),
        reply_to: z.string().nullable().optional(),
      }),
      deliveries: z.array(
        z.object({
          delivery_id: z.string(),
          recipient_id: z.string(),
          state: z.string(),
          error: z.string().nullable(),
          blocked_reason: z.string().nullable(),
          consumed_node_id: z.string().nullable(),
        }),
      ),
    }),
  ),
  next_before_seq: z.number().nullable(),
});
const mail = ref<z.infer<typeof mailboxSchema> | null>(null);
const deliveryStates = new Set([
  "pending",
  "processing",
  "completed",
  "failed",
  "interrupted",
  "blocked",
]);

function applySnapshot(value: AgentSnapshot) {
  snapshot.value = value;
  modelName.value = value.specification.agent_config_name ?? "";
  modelNames.value = stringArrayOrNull(value.abilities.model_config_names) ?? [];
  modelCatalogError.value =
    typeof value.abilities.model_catalog_error === "string"
      ? value.abilities.model_catalog_error
      : "";
  selectedTypes.value = [...(value.specification.abilities ?? [])];
  types.value = [
    ...new Set([
      ...z.array(z.string()).parse(value.abilities.available_types ?? []),
      ...selectedTypes.value,
    ]),
  ];
  uncertain.value = false;
}
async function refresh() {
  confirmation.value = null;
  if (!props.agentId) {
    await run(
      async () => {
        const result = await client.value!.listAgentEntities(props.projectId);
        if (!result.success)
          throw new Error(
            [result.error ?? t("agentEntity.failed"), result.error_detail]
              .filter(Boolean)
              .join(": "),
          );
        return z
          .object({
            available_ability_types: z.array(z.string()),
            model_config_names: z.array(z.string()).optional().default([]),
            model_catalog_error: z.string().optional(),
          })
          .parse(result.data);
      },
      (result) => {
        types.value = result.available_ability_types;
        modelNames.value = result.model_config_names;
        modelCatalogError.value = result.model_catalog_error ?? "";
      },
    );
    return;
  }
  await run(() => entity().snapshot(), applySnapshot);
}
async function loadContext() {
  await run(
    async () => catalogSchema.parse(await entity().context.listSessions()),
    (value) => {
      catalog.value = value;
      if (!value.sessions.some((s) => s.session_id === sessionId.value))
        sessionId.value = value.active_session_id ?? value.sessions[0]?.session_id ?? "";
      chooseSession();
    },
  );
}
function chooseSession() {
  const session = catalog.value?.sessions.find((s) => s.session_id === sessionId.value);
  branchId.value = session?.active_branch_id ?? "";
  nodes.value = [];
  historyLoaded.value = false;
}
async function loadHistory() {
  if (!sessionId.value || !branchId.value) return;
  await run(
    async () =>
      z
        .object({
          nodes: z.array(
            z.object({
              id: z.string(),
              iteration: z.number(),
              messages_delta: z.unknown(),
              action_results: z.unknown(),
            }),
          ),
        })
        .parse(await entity().context.getHistory(sessionId.value, branchId.value)),
    (value) => {
      nodes.value = value.nodes;
      historyLoaded.value = true;
    },
  );
}
async function loadMail(more = false) {
  const cursor = more ? (mail.value?.next_before_seq ?? undefined) : undefined;
  await run(
    async () =>
      mailboxSchema.parse(
        await (direction.value === "inbox"
          ? entity().mailbox.listInbox(20, cursor)
          : entity().mailbox.listOutbox(20, cursor)),
      ),
    (value) => {
      mail.value =
        more && mail.value
          ? { items: [...mail.value.items, ...value.items], next_before_seq: value.next_before_seq }
          : value;
    },
  );
}
async function changeTab(value: typeof tab.value) {
  tab.value = value;
  confirmation.value = null;
  if (value === "context") await loadContext();
  if (value === "mailbox") await loadMail();
}
async function mutate(action: () => Promise<unknown>) {
  success.value = "";
  confirmation.value = null;
  await run(
    async () => {
      await action();
      return entity().snapshot();
    },
    (value) => {
      applySnapshot(value);
      success.value = t("agentEntity.saved");
      emit("changed");
    },
    true,
  );
  if (!error.value && !uncertain.value) {
    if (tab.value === "context") await loadContext();
    if (tab.value === "mailbox") await loadMail();
  }
}
function confirmStop() {
  const current = snapshot.value!;
  confirmation.value = {
    label: t("agentEntity.stopHint"),
    action: () =>
      entity().stop(current.specification_revision, current.runtime.incarnation_id ?? undefined),
  };
}
function confirmDelivery(deliveryId: string, resume: boolean) {
  confirmation.value = {
    label: t(resume ? "agentEntity.resumeHint" : "agentEntity.retryHint"),
    action: () =>
      resume ? entity().mailbox.resume(deliveryId) : entity().mailbox.retry(deliveryId),
  };
}
async function create() {
  if (!canCreate.value) return;
  await run(
    async () => {
      const result = await client.value!.createAgentEntity({
        project_id: props.projectId,
        name: name.value.trim(),
        cluster_id: cluster.value,
        start: startNow.value,
        agent_config_name: source.value === "types" ? modelName.value : null,
        system_prompt: prompt.value,
        manifest_ref: source.value === "manifest" ? manifest.value.trim() : "",
        ability_types: source.value === "types" ? selectedTypes.value : null,
      });
      if (!result.success)
        throw new Error(
          [result.error ?? t("agentEntity.failed"), result.error_detail].filter(Boolean).join(": "),
        );
      return AgentSnapshotSchema.parse(result.data);
    },
    (value) => {
      emit("changed");
      emit("created", value.identity.agent_id);
    },
    true,
  );
}
function saveModel() {
  return mutate(() =>
    entity().configureModel(modelName.value, snapshot.value!.specification_revision),
  );
}
function startAgent() {
  return mutate(() => entity().start(snapshot.value!.specification_revision));
}
function saveAbilities() {
  return mutate(() =>
    entity().abilities.configure(selectedTypes.value, snapshot.value!.specification_revision),
  );
}
function activateSession() {
  return mutate(() => entity().context.activateSession(sessionId.value));
}
function createSession() {
  return mutate(() => entity().context.createSession(sessionName.value || undefined));
}
onMounted(() => {
  void refresh();
});
</script>

<template>
  <Teleport to="body">
    <BaseModal
      :title="
        agentId
          ? t('agentEntity.title', { name: snapshot?.specification.name ?? agentId })
          : t('agentEntity.create')
      "
      size="lg"
      @request-close="emit('close')"
    >
      <div class="entity-body">
        <div class="entity-actions">
          <button class="btn-secondary" :disabled="busy || !connected" @click="refresh">
            {{ t("agentEntity.refresh") }}
          </button>
          <button v-if="busy" class="btn-secondary" @click="stopWaiting">
            {{ t("communication.stopWaiting") }}
          </button>
          <span v-if="busy" role="status">{{ t("common.loading") }}</span>
        </div>
        <p v-if="!connected" role="status">{{ t("agents.notConnected") }}</p>
        <p v-if="error" class="entity-error" role="alert">{{ error }}</p>
        <p v-if="/unknown command.*agent_entity/i.test(error)" role="status">
          {{ t("agentEntity.backendUpdate") }}
        </p>
        <p v-if="uncertain" role="status">{{ t("agentEntity.uncertain") }}</p>
        <p v-if="success" role="status">{{ success }}</p>
        <form v-if="!agentId" class="entity-stack" @submit.prevent="create">
          <label
            >{{ t("agentEntity.name")
            }}<input v-model="name" :disabled="busy" required data-autofocus
          /></label>
          <label
            >{{ t("agentEntity.cluster")
            }}<select v-model="cluster" :disabled="busy" required>
              <option value="" disabled>{{ t("agentEntity.choose") }}</option>
              <option v-for="id in project?.cluster_ids ?? []" :key="id" :value="id">
                {{ id }}
              </option>
            </select></label
          >
          <label
            >{{ t("agentEntity.prompt") }}<textarea v-model="prompt" :disabled="busy" rows="3" />
          </label>
          <label
            >{{ t("agentEntity.source")
            }}<select v-model="source" :disabled="busy">
              <option value="types">{{ t("agentEntity.types") }}</option>
              <option value="manifest">{{ t("agentEntity.manifest") }}</option>
            </select></label
          >
          <label v-if="source === 'types'"
            >{{ t("agentEntity.modelConfig") }}
            <select v-model="modelName" :disabled="busy" required data-testid="model-config">
              <option value="" disabled>{{ t("agentEntity.choose") }}</option>
              <option v-for="item in modelNames" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>
          <p v-if="source === 'types' && !modelNames.length">
            {{ t("agentEntity.noModelConfigs") }}
          </p>
          <p v-if="modelCatalogError" role="alert">{{ modelCatalogError }}</p>
          <label v-if="source === 'manifest'"
            >{{ t("agentEntity.manifest") }}<input v-model="manifest" :disabled="busy" required
          /></label>
          <fieldset v-else :disabled="busy">
            <legend>{{ t("agentEntity.types") }}</legend>
            <div class="entity-options">
              <label v-for="type in types" :key="type" class="entity-check"
                ><input v-model="selectedTypes" type="checkbox" :value="type" />{{ type }}</label
              >
            </div>
            <p v-if="!types.length">{{ t("agentEntity.noTypes") }}</p>
          </fieldset>
          <label class="entity-check"
            ><input v-model="startNow" type="checkbox" :disabled="busy || !active" />{{
              t("agentEntity.startNow")
            }}</label
          >
          <p>{{ t("agentEntity.createHint") }}</p>
          <button class="btn-primary" type="submit" :disabled="!canCreate">
            {{ t("agentEntity.create") }}
          </button>
        </form>
        <template v-else-if="snapshot">
          <p class="entity-identity">
            {{ snapshot.identity.project_id }} / {{ snapshot.identity.agent_id }}
          </p>
          <nav class="entity-actions" :aria-label="t('agentEntity.sections')">
            <button
              v-for="item in tabs"
              :key="item"
              class="btn-secondary"
              :aria-pressed="tab === item"
              :disabled="busy"
              @click="changeTab(item)"
            >
              {{ t(`agentEntity.tabs.${item}`) }}
            </button>
          </nav>
          <div v-if="confirmation" class="entity-card" role="alert">
            <p>{{ confirmation.label }}</p>
            <div class="entity-actions">
              <button
                class="btn-primary"
                :disabled="!writable"
                @click="mutate(confirmation!.action)"
              >
                {{ t("agentEntity.confirm") }}</button
              ><button class="btn-secondary" @click="confirmation = null">
                {{ t("common.cancel") }}
              </button>
            </div>
          </div>
          <section v-if="tab === 'overview'" class="entity-stack">
            <dl class="entity-facts">
              <dt>{{ t("agentEntity.desired") }}</dt>
              <dd>{{ t(`agentEntity.desiredStates.${snapshot.specification.desired_state}`) }}</dd>
              <dt>{{ t("agentEntity.actual") }}</dt>
              <dd>
                {{ t(`agentEntity.runtime.${snapshot.runtime.availability}`) }}
                <span v-if="snapshot.runtime.busy">{{ t("agentEntity.busy") }}</span>
              </dd>
              <dt>{{ t("agentEntity.cluster") }}</dt>
              <dd>{{ snapshot.specification.cluster_id }}</dd>
              <dt>{{ t("agentEntity.incarnation") }}</dt>
              <dd>{{ snapshot.runtime.incarnation_id ?? t("agentEntity.none") }}</dd>
            </dl>
            <p
              v-if="snapshot.specification.runtime_error || snapshot.runtime.error"
              class="entity-error"
            >
              {{ snapshot.runtime.error || snapshot.specification.runtime_error }}
            </p>
            <p v-if="snapshot.context.error" class="entity-error">
              {{ t("agentEntity.tabs.context") }}: {{ snapshot.context.error }}
            </p>
            <p v-if="snapshot.mailbox.error" class="entity-error">
              {{ t("agentEntity.tabs.mailbox") }}: {{ snapshot.mailbox.error }}
            </p>
            <div class="entity-actions">
              <button
                class="btn-primary"
                :disabled="
                  !writable ||
                  !active ||
                  snapshot.runtime.availability === 'unknown' ||
                  (snapshot.specification.desired_state === 'running' &&
                    snapshot.runtime.availability === 'online')
                "
                @click="startAgent"
              >
                {{ t("agentEntity.start") }}</button
              ><button
                class="btn-secondary"
                :disabled="
                  !writable ||
                  snapshot.runtime.availability === 'unknown' ||
                  (snapshot.specification.desired_state === 'stopped' &&
                    snapshot.runtime.availability === 'offline')
                "
                @click="confirmStop"
              >
                {{ t("agentEntity.stop") }}
              </button>
            </div>
            <p>{{ t("agentEntity.lifecycleHint") }}</p>
            <p>{{ t("agentEntity.modelHint") }}</p>
            <p v-if="snapshot.specification.manifest_ref">{{ t("agentEntity.modelManifest") }}</p>
            <template v-else>
              <label
                >{{ t("agentEntity.modelConfig") }}
                <select v-model="modelName" :disabled="!canConfigure" data-testid="model-config">
                  <option value="" disabled>
                    {{ snapshot.specification.name }} ({{ t("agentEntity.legacyModel") }})
                  </option>
                  <option v-for="item in modelNames" :key="item" :value="item">{{ item }}</option>
                </select>
              </label>
              <p v-if="!modelNames.length">{{ t("agentEntity.noModelConfigs") }}</p>
              <p v-if="modelCatalogError" role="alert">{{ modelCatalogError }}</p>
              <button
                class="btn-secondary"
                :disabled="!canConfigure || !modelName"
                @click="saveModel"
              >
                {{ t("agentEntity.saveModel") }}
              </button>
            </template>
          </section>
          <section v-if="tab === 'abilities'" class="entity-stack">
            <p>
              {{ t("agentEntity.configured") }}:
              {{
                abilityView.configured?.join(", ") ||
                snapshot.specification.manifest_ref ||
                t("agentEntity.defaultAbilities")
              }}
            </p>
            <p>
              {{ t("agentEntity.loaded") }}:
              {{ abilityView.loaded?.join(", ") || t("agentEntity.none") }}
            </p>
            <p>{{ t("agentEntity.abilityHint") }}</p>
            <p v-if="snapshot.specification.manifest_ref">{{ t("agentEntity.manifestLocked") }}</p>
            <fieldset :disabled="!canConfigure">
              <legend>{{ t("agentEntity.types") }}</legend>
              <div class="entity-options">
                <label v-for="type in types" :key="type" class="entity-check"
                  ><input v-model="selectedTypes" type="checkbox" :value="type" />{{ type }}</label
                >
              </div>
            </fieldset>
            <button
              class="btn-primary"
              :disabled="!canConfigure || !selectedTypes.length"
              @click="saveAbilities"
            >
              {{ t("agentEntity.saveAbilities") }}
            </button>
          </section>
          <section v-if="tab === 'context'" class="entity-stack">
            <button class="btn-secondary" :disabled="busy || !connected" @click="loadContext">
              {{ t("agentEntity.reloadSessions") }}
            </button>
            <template v-if="catalog"
              ><p v-if="!catalog.sessions.length">{{ t("agentEntity.noSessions") }}</p>
              <label
                >{{ t("agentEntity.session")
                }}<select v-model="sessionId" :disabled="busy" @change="chooseSession">
                  <option
                    v-for="session in catalog.sessions"
                    :key="session.session_id"
                    :value="session.session_id"
                  >
                    {{ session.name || session.session_id }}
                    {{
                      session.session_id === catalog.active_session_id
                        ? t("agentEntity.active")
                        : ""
                    }}
                  </option>
                </select></label
              >
              <label
                >{{ t("agentEntity.branch")
                }}<select
                  v-model="branchId"
                  :disabled="busy"
                  @change="
                    nodes = [];
                    historyLoaded = false;
                  "
                >
                  <option
                    v-for="branch in branches"
                    :key="branch.branch_id"
                    :value="branch.branch_id"
                  >
                    {{ branch.name }} · {{ branch.branch_id }}
                  </option>
                </select></label
              >
              <div class="entity-actions">
                <button
                  class="btn-secondary"
                  :disabled="busy || !connected || !branchId"
                  @click="loadHistory"
                >
                  {{ t("agentEntity.history") }}</button
                ><button
                  class="btn-secondary"
                  :disabled="!canContext || !sessionId || sessionId === catalog.active_session_id"
                  @click="activateSession"
                >
                  {{ t("agentEntity.activate") }}
                </button>
              </div>
            </template>
            <form class="entity-actions" @submit.prevent="createSession">
              <label
                >{{ t("agentEntity.sessionName")
                }}<input v-model="sessionName" :disabled="!canContext" /></label
              ><button class="btn-secondary" type="submit" :disabled="!canContext">
                {{ t("agentEntity.createSession") }}
              </button>
            </form>
            <p>{{ t("agentEntity.historyHint") }}</p>
            <p v-if="historyLoaded && !nodes.length">{{ t("agentEntity.noHistory") }}</p>
            <details v-for="node in nodes" :key="node.id" class="entity-card">
              <summary>
                {{ t("agentEntity.node", { iteration: node.iteration }) }} · {{ node.id }}
              </summary>
              <pre>{{
                JSON.stringify(
                  { messages: node.messages_delta, results: node.action_results },
                  null,
                  2,
                )
              }}</pre>
            </details>
          </section>
          <section v-if="tab === 'mailbox'" class="entity-stack">
            <label
              >{{ t("agentEntity.direction")
              }}<select
                v-model="direction"
                :disabled="busy"
                @change="
                  mail = null;
                  loadMail();
                "
              >
                <option value="inbox">{{ t("agentEntity.inbox") }}</option>
                <option value="outbox">{{ t("agentEntity.outbox") }}</option>
              </select></label
            >
            <button class="btn-secondary" :disabled="busy || !connected" @click="loadMail()">
              {{ t("agentEntity.reloadMessages") }}
            </button>
            <p v-if="mail && !mail.items.length">{{ t("agentEntity.noMessages") }}</p>
            <article
              v-for="item in mail?.items ?? []"
              :key="item.message.message_id"
              class="entity-card"
            >
              <p class="entity-identity">{{ item.message.message_id }}</p>
              <p>{{ t("agentEntity.sender") }}: {{ item.message.sender_id }}</p>
              <p class="entity-content">{{ item.message.content }}</p>
              <p v-if="item.message.reply_to">
                {{ t("agentEntity.replyTo") }}: {{ item.message.reply_to }}
              </p>
              <div
                v-for="delivery in item.deliveries"
                :key="delivery.delivery_id"
                class="entity-delivery"
              >
                <p>
                  {{ delivery.recipient_id }} ·
                  {{
                    t(
                      `agentEntity.deliveryStates.${deliveryStates.has(delivery.state) ? delivery.state : "unknown"}`,
                    )
                  }}
                </p>
                <p v-if="delivery.error || delivery.blocked_reason" class="entity-error">
                  {{ delivery.blocked_reason || delivery.error }}
                </p>
                <div
                  v-if="
                    delivery.recipient_id === agentId &&
                    ['failed', 'interrupted'].includes(delivery.state)
                  "
                  class="entity-actions"
                >
                  <button
                    v-if="!delivery.consumed_node_id && !delivery.blocked_reason"
                    class="btn-secondary"
                    :disabled="
                      !writable ||
                      !active ||
                      snapshot.runtime.availability !== 'online' ||
                      snapshot.specification.desired_state !== 'running'
                    "
                    @click="confirmDelivery(delivery.delivery_id, false)"
                  >
                    {{ t("agentEntity.retry") }}</button
                  ><button
                    v-else
                    class="btn-secondary"
                    :disabled="
                      !writable ||
                      !active ||
                      snapshot.runtime.availability !== 'online' ||
                      snapshot.specification.desired_state !== 'running'
                    "
                    @click="confirmDelivery(delivery.delivery_id, true)"
                  >
                    {{ t("agentEntity.resume") }}
                  </button>
                </div>
              </div>
            </article>
            <button
              v-if="mail?.next_before_seq"
              class="btn-secondary"
              :disabled="busy || !connected"
              @click="loadMail(true)"
            >
              {{ t("agentEntity.more") }}
            </button>
          </section>
        </template>
      </div>
    </BaseModal>
  </Teleport>
</template>

<style scoped>
.entity-body,
.entity-stack,
label {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}
.entity-body {
  font-size: var(--font-ui-body);
}
.entity-actions,
.entity-options {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-2);
  align-items: center;
}
.entity-check {
  flex-direction: row;
  align-items: center;
}
.entity-card,
fieldset {
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-3);
  min-width: 0;
}
input,
select,
textarea {
  font: inherit;
  color: inherit;
  background: transparent;
  border: 1px solid var(--ac-node-border);
  border-radius: var(--radius-md);
  padding: var(--spacing-2);
  min-width: 0;
}
.entity-facts {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: var(--spacing-3);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.entity-identity,
pre {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.entity-content {
  white-space: pre-wrap;
}
.entity-error {
  color: var(--ac-write);
  overflow-wrap: anywhere;
}
.entity-delivery {
  border-top: 1px solid var(--ac-node-border);
  padding-block: var(--spacing-2);
}
button[aria-pressed="true"] {
  text-decoration: underline;
}
</style>
