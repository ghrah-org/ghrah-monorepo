<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { z } from "zod";
import { RuntimeApiError, RuntimeObserverClient, type RuntimeResult } from "@ghrah/observer-core";
import {
  RuntimeAgentCreateExample,
  RuntimeAgentCreateSchema,
  RuntimeAgentPatchSchema,
  RuntimeGrantRequestSchema,
  type RuntimeAgentResource,
  type RuntimeAgentSummary,
  type RuntimeAbilitiesView,
  type RuntimeAbilityCatalog,
  type RuntimeAbilityChanges,
  type RuntimeDeliveryView,
  type RuntimeNodeView,
  type RuntimeTimelineEntry,
} from "@ghrah/protocol";
import { setLocale } from "@/i18n";
import UiButton from "@/components/ui/button.vue";

const { t, locale } = useI18n();
const baseUrl = import.meta.env.VITE_GHRAH_RUNTIME_URL;
const client = new RuntimeObserverClient({
  baseUrl,
  token: import.meta.env.VITE_GHRAH_RUNTIME_TOKEN,
});
const agents = ref<readonly RuntimeAgentSummary[]>([]);
const nextAgents = ref<string | null>(null);
const capabilities = ref<string[]>([]);
const ready = ref(false);
const selected = ref("");
const agent = ref<RuntimeAgentResource | null>(null);
const configEtag = ref("");
const abilityEtag = ref("");
const ability = ref<RuntimeAbilitiesView | null>(null);
const catalog = ref<RuntimeAbilityCatalog | null>(null);
const audit = ref<RuntimeAbilityChanges | null>(null);
const timeline = ref<readonly RuntimeTimelineEntry[]>([]);
const nextTimeline = ref<string | null>(null);
const node = ref<RuntimeNodeView | null>(null);
const delivery = ref<RuntimeDeliveryView | null>(null);
const acceptedDelivery = ref("");
const busy = ref(false);
const error = ref("");
const confirmingDelete = ref(false);
const externalRef = ref("");
const endpoint = ref(import.meta.env.VITE_GHRAH_RUNTIME_ENDPOINT ?? "");
const prompt = ref("");
const configDraft = ref("");
const message = ref("");
const rootsDraft = ref("[]");
const grantDraft = ref("");
const toolAlias = ref("");
const toolMember = ref("");
const toolName = ref("");
let createKey = crypto.randomUUID();
let messageKey = crypto.randomUUID();
let epoch = 0;
const controller = new AbortController();
onBeforeUnmount(() => {
  epoch += 1;
  controller.abort();
});
const manageAgents = computed(() => capabilities.value.includes("runtime.agents.management"));
const manageAbilities = computed(() => capabilities.value.includes("runtime.abilities.management"));
const stopped = computed(() => agent.value?.state === "stopped");
const reply = computed(() => {
  const messages = z
    .array(
      z.object({
        role: z.string(),
        content_blocks: z
          .array(z.object({ type: z.string(), text: z.string().optional() }))
          .optional(),
      }),
    )
    .safeParse(node.value?.payload.messages_delta);
  return messages.success
    ? messages.data
        .filter((item) => item.role === "ai")
        .flatMap((item) => item.content_blocks ?? [])
        .filter((block) => block.type === "text")
        .map((block) => block.text ?? "")
        .join("\n")
    : "";
});

function requireEtag<T>(result: RuntimeResult<T>): string {
  if (!result.etag) throw new Error("Missing resource ETag");
  return result.etag;
}

async function run(action: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (cause) {
    if (cause instanceof RuntimeApiError && cause.status === 412) {
      if (selected.value) await readAgent(selected.value);
      error.value = t("runtime.conflict");
    } else
      error.value = `${t("runtime.error")}: ${cause instanceof Error ? cause.message : String(cause)}`;
  } finally {
    busy.value = false;
  }
}

async function readAgent(agentId: string) {
  const version = ++epoch;
  const [resource, history, abilities, changes] = await Promise.all([
    client.agent(agentId),
    client.timeline(agentId),
    manageAbilities.value ? client.abilities(agentId) : null,
    manageAbilities.value ? client.changes(agentId) : null,
  ]);
  if (version !== epoch) return;
  selected.value = agentId;
  agent.value = resource.body;
  configEtag.value = requireEtag(resource);
  configDraft.value = JSON.stringify(resource.body.config, null, 2);
  timeline.value = history.body.items;
  nextTimeline.value = history.body.next_cursor;
  ability.value = abilities?.body ?? null;
  abilityEtag.value = abilities ? requireEtag(abilities) : "";
  audit.value = changes?.body ?? null;
  if (resource.body.current_delivery)
    acceptedDelivery.value = resource.body.current_delivery.delivery_id;
}

async function refresh() {
  const [status, meta, page] = await Promise.all([
    client.status(),
    client.meta(),
    client.listAgents(),
  ]);
  ready.value = status.body.ready;
  capabilities.value = meta.body.capabilities
    .filter((item) => item.version === 1)
    .map((item) => item.name);
  agents.value = page.body.items;
  nextAgents.value = page.body.next_cursor;
  if (manageAbilities.value) catalog.value = (await client.catalog()).body;
  if (selected.value) await readAgent(selected.value);
}

async function selectAgent(agentId: string) {
  node.value = null;
  delivery.value = null;
  acceptedDelivery.value = "";
  confirmingDelete.value = false;
  messageKey = crypto.randomUUID();
  await run(() => readAgent(agentId));
}

async function createAgent() {
  await run(async () => {
    const input = RuntimeAgentCreateSchema.parse(structuredClone(RuntimeAgentCreateExample));
    input.external_ref = externalRef.value;
    input.config.model.endpoint_name = endpoint.value;
    input.config.prompt.system_prompt = prompt.value;
    const result = await client.createAgent(input, createKey);
    createKey = crypto.randomUUID();
    selected.value = result.body.agent_id;
    externalRef.value = "";
    await refresh();
  });
}

async function setState(state: "running" | "stopped") {
  await run(async () => {
    await client.setState(selected.value, state, configEtag.value);
    await refresh();
  });
}

async function saveConfig() {
  await run(async () => {
    const input = RuntimeAgentPatchSchema.parse({ config: JSON.parse(configDraft.value) });
    await client.replaceConfig(selected.value, input, configEtag.value);
    await refresh();
  });
}

async function deleteAgent() {
  await run(async () => {
    await client.deleteAgent(selected.value, configEtag.value);
    selected.value = "";
    agent.value = null;
    node.value = null;
    ability.value = null;
    confirmingDelete.value = false;
    await refresh();
  });
}

async function readDelivery() {
  const agentId = selected.value;
  const deadline = Date.now() + 90_000;
  while (!controller.signal.aborted && agentId === selected.value) {
    const result = await client.delivery(acceptedDelivery.value, 1, controller.signal);
    delivery.value = result.body;
    if (["completed", "failed", "interrupted", "blocked"].includes(result.body.state)) {
      await refresh();
      if (result.body.last_node_id) node.value = (await client.node(result.body.last_node_id)).body;
      return;
    }
    if (Date.now() >= deadline) throw new Error(t("runtime.pollTimeout"));
  }
}

async function sendMessage() {
  await run(async () => {
    const accepted = await client.submitMessage(
      selected.value,
      { text: message.value, kind: "user" },
      messageKey,
    );
    acceptedDelivery.value = accepted.body.delivery_id;
    messageKey = crypto.randomUUID();
    await readDelivery();
  });
}

async function preparePackage(packageName: string) {
  await run(async () => {
    const roots = z.array(z.string()).parse(JSON.parse(rootsDraft.value));
    const result = await client.prepareInstance({ package: packageName, allowed_roots: roots });
    const member = result.body.operations.includes("session_info")
      ? "session_info"
      : result.body.operations[0];
    grantDraft.value = JSON.stringify(
      {
        alias: packageName.split(".").at(-1),
        instance_id: result.body.instance_id,
        members: member ? [member] : [],
        allowed_roots: roots,
      },
      null,
      2,
    );
  });
}

async function applyGrant() {
  await run(async () => {
    await client.grant(
      selected.value,
      RuntimeGrantRequestSchema.parse(JSON.parse(grantDraft.value)),
      abilityEtag.value,
    );
    await readAgent(selected.value);
  });
}

async function changeAbility(action: () => Promise<unknown>) {
  await run(async () => {
    await action();
    await readAgent(selected.value);
  });
}

async function moreAgents() {
  if (!nextAgents.value) return;
  const page = await client.listAgents(nextAgents.value);
  agents.value = [...agents.value, ...page.body.items];
  nextAgents.value = page.body.next_cursor;
}

async function moreTimeline() {
  if (!nextTimeline.value) return;
  const page = await client.timeline(selected.value, nextTimeline.value);
  timeline.value = [...timeline.value, ...page.body.items];
  nextTimeline.value = page.body.next_cursor;
}

onMounted(() => {
  void run(refresh);
});
</script>

<template>
  <main class="runtime-workspace">
    <header class="runtime-header">
      <div>
        <h1>{{ t("runtime.title") }}</h1>
        <p>
          {{ t("runtime.connection") }} <code>{{ baseUrl }}</code>
        </p>
      </div>
      <label
        >{{ t("runtime.language")
        }}<select
          :value="locale"
          @change="
            setLocale(($event.target as HTMLSelectElement).value === 'zh-CN' ? 'zh-CN' : 'en')
          "
        >
          <option value="en">{{ t("runtime.languageEnglish") }}</option>
          <option value="zh-CN">{{ t("runtime.languageChinese") }}</option>
        </select></label
      >
      <UiButton variant="secondary" :disabled="busy" @click="run(refresh)">{{
        t("runtime.refresh")
      }}</UiButton>
    </header>
    <p>
      {{ t("runtime.ready") }} <output>{{ ready }}</output>
    </p>
    <details>
      <summary>{{ t("runtime.capabilities") }}</summary>
      <code>{{ capabilities.join(", ") }}</code>
    </details>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="runtime-layout">
      <aside class="runtime-card">
        <h2>{{ t("runtime.agents") }}</h2>
        <p v-if="!agents.length">{{ t("runtime.empty") }}</p>
        <ul class="runtime-agents">
          <li v-for="item in agents" :key="item.agent_id">
            <button
              :disabled="busy"
              :aria-pressed="selected === item.agent_id"
              @click="selectAgent(item.agent_id)"
            >
              <strong>{{ item.external_ref }}</strong
              ><span>{{ item.state }}</span>
            </button>
          </li>
        </ul>
        <UiButton v-if="nextAgents" variant="secondary" :disabled="busy" @click="run(moreAgents)">{{
          t("runtime.more")
        }}</UiButton>
        <form @submit.prevent="createAgent">
          <label
            >{{ t("runtime.externalRef") }}<input v-model="externalRef" required :disabled="busy"
          /></label>
          <label
            >{{ t("runtime.endpoint") }}<input v-model="endpoint" required :disabled="busy"
          /></label>
          <label
            >{{ t("runtime.prompt") }}<textarea v-model="prompt" rows="3" :disabled="busy" />
          </label>
          <p>{{ t("runtime.createHint") }}</p>
          <UiButton
            variant="primary"
            type="submit"
            :disabled="busy || !ready || !externalRef || !endpoint"
            >{{ t("runtime.create") }}</UiButton
          >
        </form>
      </aside>
      <section class="runtime-content">
        <p v-if="!agent">{{ t("runtime.select") }}</p>
        <template v-else>
          <section class="runtime-card">
            <h2>{{ agent.external_ref }}</h2>
            <code>{{ agent.agent_id }}</code>
            <dl>
              <dt>{{ t("runtime.state") }}</dt>
              <dd data-testid="runtime-agent-state">{{ agent.state }}</dd>
              <dt>{{ t("runtime.revision") }}</dt>
              <dd data-testid="runtime-config-revision">{{ agent.config_revision }}</dd>
              <dt>{{ t("runtime.instance") }}</dt>
              <dd>{{ agent.incarnation_id }}</dd>
            </dl>
            <div class="runtime-actions">
              <UiButton
                variant="secondary"
                :disabled="busy || !stopped"
                @click="setState('running')"
                >{{ t("runtime.start") }}</UiButton
              ><UiButton
                variant="secondary"
                :disabled="busy || stopped"
                @click="setState('stopped')"
                >{{ t("runtime.stop") }}</UiButton
              ><UiButton
                variant="danger"
                :disabled="busy || !manageAgents"
                @click="confirmingDelete = true"
                >{{ t("runtime.remove") }}</UiButton
              >
            </div>
            <div v-if="confirmingDelete" role="alert">
              <p>{{ t("runtime.deleteHint") }}</p>
              <UiButton variant="danger" :disabled="busy" @click="deleteAgent">{{
                t("runtime.confirm")
              }}</UiButton
              ><UiButton variant="secondary" :disabled="busy" @click="confirmingDelete = false">{{
                t("runtime.cancel")
              }}</UiButton>
            </div>
            <details>
              <summary>{{ t("runtime.config") }}</summary>
              <form @submit.prevent="saveConfig">
                <label
                  >{{ t("runtime.config")
                  }}<textarea
                    v-model="configDraft"
                    rows="16"
                    :disabled="busy || !stopped || !manageAgents"
                  />
                </label>
                <p>{{ t("runtime.configHint") }}</p>
                <UiButton
                  variant="primary"
                  type="submit"
                  :disabled="busy || !stopped || !manageAgents"
                  >{{ t("runtime.save") }}</UiButton
                >
              </form>
            </details>
          </section>
          <section class="runtime-card">
            <h2>{{ t("runtime.messages") }}</h2>
            <p>{{ t("runtime.sendHint") }}</p>
            <form @submit.prevent="sendMessage">
              <label
                >{{ t("runtime.message")
                }}<textarea v-model="message" rows="3" :disabled="busy" /></label
              ><UiButton variant="primary" type="submit" :disabled="busy || stopped || !message">{{
                t("runtime.send")
              }}</UiButton>
            </form>
            <p v-if="acceptedDelivery">
              {{ t("runtime.accepted") }} <code>{{ acceptedDelivery }}</code>
            </p>
            <p v-if="delivery">
              {{ t("runtime.delivery") }}
              <output data-testid="runtime-delivery-state">{{ delivery.state }}</output>
            </p>
            <UiButton
              v-if="acceptedDelivery"
              variant="secondary"
              :disabled="busy"
              @click="run(readDelivery)"
              >{{ t("runtime.refreshDelivery") }}</UiButton
            >
            <h3 v-if="reply">{{ t("runtime.reply") }}</h3>
            <pre v-if="reply" data-testid="runtime-model-reply">{{ reply }}</pre>
          </section>
          <section class="runtime-card">
            <h2>{{ t("runtime.timeline") }}</h2>
            <p v-if="!timeline.length">{{ t("runtime.noHistory") }}</p>
            <ul>
              <li v-for="entry in timeline" :key="entry.node_id">
                <span>{{ entry.seq }} · {{ entry.kind }} · {{ entry.summary.text }}</span
                ><UiButton
                  variant="secondary"
                  :disabled="busy"
                  @click="
                    run(async () => {
                      node = (await client.node(entry.node_id)).body;
                    })
                  "
                  >{{ t("runtime.history") }}</UiButton
                >
              </li>
            </ul>
            <UiButton
              v-if="nextTimeline"
              variant="secondary"
              :disabled="busy"
              @click="run(moreTimeline)"
              >{{ t("runtime.more") }}</UiButton
            >
            <details v-if="node">
              <summary>{{ t("runtime.details") }}</summary>
              <pre>{{ JSON.stringify(node, null, 2) }}</pre>
            </details>
          </section>
          <section class="runtime-card">
            <h2>{{ t("runtime.abilities") }}</h2>
            <p v-if="!manageAbilities">{{ t("runtime.unsupported") }}</p>
            <template v-if="ability && manageAbilities">
              <p v-if="ability.requires_respawn">{{ t("runtime.pending") }}</p>
              <code>{{ abilityEtag }}</code>
              <details>
                <summary>{{ t("runtime.catalog") }}</summary>
                <label
                  >{{ t("runtime.roots") }}<input v-model="rootsDraft" :disabled="busy"
                /></label>
                <p>{{ t("runtime.instanceHint") }}</p>
                <ul>
                  <li v-for="item in catalog?.packages ?? []" :key="item.full_name">
                    <code>{{ item.full_name }}</code
                    ><UiButton
                      variant="secondary"
                      :disabled="busy"
                      @click="preparePackage(item.full_name)"
                      >{{ t("runtime.prepare") }}</UiButton
                    >
                  </li>
                </ul>
              </details>
              <h3>{{ t("runtime.grants") }}</h3>
              <ul>
                <li v-for="grant in ability.grants" :key="grant.alias">
                  <code>{{ grant.alias }} · {{ (grant.members ?? []).join(", ") }}</code
                  ><UiButton
                    variant="secondary"
                    :disabled="busy"
                    @click="
                      grantDraft = JSON.stringify(
                        {
                          alias: grant.alias,
                          instance_id: grant.instance_id,
                          members: grant.members ?? [],
                          allowed_roots: grant.roots,
                          axes: grant.axes,
                        },
                        null,
                        2,
                      )
                    "
                    >{{ t("runtime.editGrant") }}</UiButton
                  ><UiButton
                    variant="secondary"
                    :disabled="busy"
                    @click="changeAbility(() => client.revoke(selected, grant.alias, abilityEtag))"
                    >{{ t("runtime.revoke") }}</UiButton
                  >
                </li>
              </ul>
              <form @submit.prevent="applyGrant">
                <label
                  >{{ t("runtime.grant")
                  }}<textarea v-model="grantDraft" rows="7" :disabled="busy" /></label
                ><UiButton variant="primary" type="submit" :disabled="busy || !grantDraft">{{
                  t("runtime.grantSave")
                }}</UiButton>
              </form>
              <h3>{{ t("runtime.tools") }}</h3>
              <ul>
                <li v-for="tool in ability.bound_tools" :key="tool.name">
                  <code>{{ tool.name }} · {{ tool.alias }}/{{ tool.member }}</code
                  ><UiButton
                    variant="secondary"
                    :disabled="busy"
                    @click="
                      changeAbility(() => client.unbindTool(selected, tool.name, abilityEtag))
                    "
                    >{{ t("runtime.unbind") }}</UiButton
                  >
                </li>
              </ul>
              <form
                class="runtime-tool-form"
                @submit.prevent="
                  changeAbility(() =>
                    client.bindTool(
                      selected,
                      { alias: toolAlias, member: toolMember, tool_name: toolName },
                      abilityEtag,
                    ),
                  )
                "
              >
                <label
                  >{{ t("runtime.alias")
                  }}<input v-model="toolAlias" required :disabled="busy" /></label
                ><label
                  >{{ t("runtime.member")
                  }}<input v-model="toolMember" required :disabled="busy" /></label
                ><label
                  >{{ t("runtime.tool")
                  }}<input v-model="toolName" required :disabled="busy" /></label
                ><UiButton variant="primary" type="submit" :disabled="busy">{{
                  t("runtime.bind")
                }}</UiButton>
              </form>
              <details>
                <summary>{{ t("runtime.audit") }}</summary>
                <ul>
                  <li v-for="change in audit?.changes ?? []" :key="change.seq">
                    <code>{{ change.seq }} · {{ change.reason }} · {{ change.source }}</code>
                  </li>
                </ul>
                <UiButton
                  v-if="audit?.has_more"
                  variant="secondary"
                  :disabled="busy"
                  @click="
                    run(async () => {
                      const next = await client.changes(selected, audit?.next_after_seq);
                      audit = {
                        ...next.body,
                        changes: [...(audit?.changes ?? []), ...next.body.changes],
                      };
                    })
                  "
                  >{{ t("runtime.more") }}</UiButton
                >
              </details>
            </template>
          </section>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.runtime-workspace {
  min-height: 100vh;
  padding: 1.25rem;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: var(--font-ui-body);
  overflow-wrap: anywhere;
}

.runtime-header,
.runtime-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}

.runtime-header {
  justify-content: space-between;
}

.runtime-layout {
  display: grid;
  grid-template-columns: minmax(12rem, 18rem) minmax(0, 1fr);
  gap: 1rem;
  margin-top: 1rem;
  align-items: start;
}

.runtime-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 1rem;
}

.runtime-card {
  min-width: 0;
  padding: 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

form,
label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
}

form {
  margin-block: 1rem;
}

input,
textarea,
select {
  box-sizing: border-box;
  max-width: 100%;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}

textarea {
  width: 100%;
  resize: vertical;
}

pre {
  max-height: 24rem;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-code);
}

li {
  margin-block: 0.5rem;
}

.runtime-agents {
  padding: 0;
  list-style: none;
}

.runtime-agents button {
  display: flex;
  width: 100%;
  flex-direction: column;
  align-items: start;
  gap: 0.25rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text);
  text-align: start;
  overflow-wrap: anywhere;
}

.runtime-agents button[aria-pressed="true"] {
  border-color: var(--color-selected-line);
  background: var(--color-selected);
}

[role="alert"] {
  color: var(--color-danger);
}

@media (width <= 64rem) {
  .runtime-layout {
    grid-template-columns: minmax(10rem, 14rem) minmax(0, 1fr);
  }
}

@media (width <= 45rem) {
  .runtime-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
