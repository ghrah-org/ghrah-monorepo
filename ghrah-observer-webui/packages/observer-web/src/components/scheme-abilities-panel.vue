<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import {
  type AbilityTreeClient,
  createAbilityControlClient,
  createAbilityTreeClient,
  createRestAbilityTreeTransport,
  HttpClient,
  useConnectionStore,
} from "@ghrah/observer-core";
import {
  BindToolRequestSchema,
  type AbilityTreeView,
  type ContentReadResult,
} from "@ghrah/protocol";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";

const props = withDefaults(
  defineProps<{
    agentId: string;
    client?: AbilityTreeClient;
    projectId?: string;
    readonly?: boolean;
  }>(),
  { readonly: false, client: undefined, projectId: undefined },
);
const { t } = useI18n();
const view = ref<AbilityTreeView | null>(null);
const selected = ref("");
const toolName = ref("");
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const content = ref<ContentReadResult | null>(null);
const contentTarget = ref<{ alias: string; member: string; version: string } | null>(null);
const reading = ref(false);
let epoch = 0;
const choices = computed(() =>
  (view.value?.abilities ?? []).flatMap((ability) =>
    ability.operations.map((operation) => ({
      value: `${ability.alias}/${operation.key}`,
      alias: ability.alias,
      member: operation.key,
      available:
        operation.availability === "ready" ||
        (operation.availability === "unknown" && ability.availability === "ready"),
    })),
  ),
);
const request = computed(() => {
  const choice = choices.value.find((item) => item.value === selected.value && item.available);
  if (!choice || !view.value) return null;
  const result = BindToolRequestSchema.safeParse({
    alias: choice.alias,
    member: choice.member,
    tool_name: toolName.value,
    expected_revision: view.value.revision,
  });
  return result.success ? result.data : null;
});

function resolveClient(): AbilityTreeClient | null {
  if (props.client) return props.client;
  if (!props.projectId) return null;
  const connection = useConnectionStore();
  const http = new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken });
  const control = createAbilityControlClient(http, { projectId: props.projectId });
  return createAbilityTreeClient(
    props.projectId,
    createRestAbilityTreeTransport(control, props.projectId),
  );
}

async function refresh() {
  const current = ++epoch;
  const target = props.agentId;
  view.value = null;
  content.value = null;
  contentTarget.value = null;
  reading.value = false;
  selected.value = "";
  loading.value = true;
  saving.value = false;
  error.value = "";
  try {
    const client = resolveClient();
    if (!client) throw new Error("missing client");
    const snapshot = await client.read(target);
    if (current !== epoch) return;
    if (snapshot.agent_id !== target) throw new Error("Target mismatch");
    view.value = snapshot;
  } catch {
    if (current === epoch) error.value = t("schemeAbilities.failed");
  } finally {
    if (current === epoch) loading.value = false;
  }
}

async function change(tool?: string) {
  if (props.readonly || !view.value || saving.value || (!tool && !request.value)) return;
  const client = resolveClient();
  if (!client) return;
  const current = epoch;
  const target = props.agentId;
  saving.value = true;
  error.value = "";
  try {
    const result = tool
      ? await client.unbindTool(target, {
          tool_name: tool,
          expected_revision: view.value.revision,
        })
      : await client.bindTool(target, request.value!);
    if (current !== epoch) return;
    if (result.error) {
      error.value = t(`schemeAbilities.errors.${result.error}`);
    } else if (result.view?.agent_id === target) {
      view.value = result.view;
    } else {
      error.value = t("schemeAbilities.failed");
    }
  } catch {
    if (current === epoch) error.value = t("schemeAbilities.uncertain");
  } finally {
    if (current === epoch) saving.value = false;
  }
}
async function readContent(alias: string, member: string, version: string, offset = 0) {
  const current = epoch;
  reading.value = true;
  error.value = "";
  content.value = null;
  contentTarget.value = { alias, member, version };
  try {
    const client = resolveClient();
    if (!client) throw new Error("missing client");
    const result = await client.readContent(props.agentId, alias, member, version, offset);
    if (current === epoch) content.value = result;
  } catch {
    if (current === epoch) error.value = t("schemeAbilities.contentFailed");
  } finally {
    if (current === epoch) reading.value = false;
  }
}

function downloadChunk() {
  if (!content.value) return;
  const item = content.value;
  const bytes =
    item.encoding === "base64"
      ? Uint8Array.from(atob(item.content), (char) => char.charCodeAt(0))
      : new TextEncoder().encode(item.content);
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${item.member.replaceAll("/", "-")}.${item.offset}.bin`;
  link.click();
  URL.revokeObjectURL(url);
}

watch(
  () => [props.agentId, props.client, props.projectId] as const,
  () => void refresh(),
  { immediate: true },
);
</script>

<template>
  <section class="scheme-abilities">
    <h3>{{ t("schemeAbilities.title") }}</h3>
    <p class="scheme-note">{{ t("schemeAbilities.scope", { agent: agentId }) }}</p>
    <UiButton type="button" variant="secondary" :disabled="loading || saving" @click="refresh">
      {{ t("schemeAbilities.refresh") }}
    </UiButton>
    <p v-if="loading" class="scheme-state" role="status">{{ t("schemeAbilities.loading") }}</p>
    <p v-if="error" class="scheme-alert" role="alert">{{ error }}</p>
    <template v-if="view">
      <p class="scheme-note">
        {{ t("schemeAbilities.revision", { revision: view.revision, run: view.run_id }) }}
      </p>
      <p v-if="!choices.length" class="scheme-state">{{ t("schemeAbilities.empty") }}</p>
      <p v-if="choices.some((item) => !item.available)" class="scheme-state">
        {{ t("schemeAbilities.unavailable") }}
      </p>
      <form v-if="!readonly" class="scheme-form" @submit.prevent="change()">
        <label>
          {{ t("schemeAbilities.operation") }}
          <select v-model="selected" :disabled="saving">
            <option value="">{{ t("schemeAbilities.choose") }}</option>
            <option
              v-for="choice in choices"
              :key="choice.value"
              :value="choice.value"
              :disabled="!choice.available"
            >
              {{ choice.value }}
            </option>
          </select>
        </label>
        <label>
          {{ t("schemeAbilities.toolName") }}
          <input v-model="toolName" :disabled="saving" />
        </label>
        <UiButton type="submit" variant="primary" :disabled="saving || !request">
          {{ t("schemeAbilities.bind") }}
        </UiButton>
      </form>
      <h4>{{ t("schemeAbilities.content") }}</h4>
      <ul class="scheme-cards">
        <li v-for="ability in view.abilities" :key="ability.alias" class="scheme-card">
          <code>{{ ability.alias }}</code>
          <p>{{ t("schemeAbilities.version", { version: ability.version_digest }) }}</p>
          <template v-if="ability.skill">
            <p>{{ ability.skill.title }} — {{ ability.skill.description }}</p>
            <UiButton
              v-if="ability.skill.body_allowed"
              type="button"
              variant="secondary"
              :disabled="reading"
              @click="
                readContent(ability.alias, `skill/${ability.skill.key}`, ability.version_digest)
              "
            >
              {{ t("schemeAbilities.readBody") }}
            </UiButton>
            <ul v-for="role in ['assets', 'references'] as const" :key="role" class="scheme-files">
              <li v-for="entry in ability.skill[role]" :key="entry.member">
                <code>{{ role }}/{{ entry.key }}</code>
                <span>{{ entry.media_type }} · {{ entry.size }}</span>
                <UiButton
                  type="button"
                  variant="secondary"
                  :disabled="reading"
                  @click="readContent(ability.alias, entry.member, ability.version_digest)"
                >
                  {{ t("schemeAbilities.readContent") }}
                </UiButton>
              </li>
            </ul>
          </template>
        </li>
      </ul>
      <div v-if="content" class="scheme-content">
        <p>
          {{
            t("schemeAbilities.range", {
              offset: content.offset,
              count: content.byte_count,
              total: content.total_bytes,
            })
          }}
        </p>
        <pre v-if="content.encoding === 'utf-8'">{{ content.content }}</pre>
        <p v-else>{{ t("schemeAbilities.binary") }}</p>
        <UiButton type="button" variant="secondary" @click="downloadChunk">
          {{ t("schemeAbilities.downloadChunk") }}
        </UiButton>
        <UiButton
          v-if="content.next_offset !== null && contentTarget"
          type="button"
          variant="secondary"
          :disabled="reading"
          @click="
            readContent(
              contentTarget.alias,
              contentTarget.member,
              contentTarget.version,
              content.next_offset,
            )
          "
        >
          {{ t("schemeAbilities.nextChunk") }}
        </UiButton>
      </div>
      <h4>{{ t("schemeAbilities.bound") }}</h4>
      <p v-if="!view.bound_tools.length" class="scheme-state">{{ t("schemeAbilities.noTools") }}</p>
      <ul v-else class="scheme-cards bound-tools">
        <li v-for="tool in view.bound_tools" :key="tool.name" class="scheme-card">
          <code>{{ tool.name }}</code>
          <span>{{ tool.alias }}/{{ tool.member }}</span>
          <UiButton
            v-if="!readonly"
            type="button"
            variant="secondary"
            :disabled="saving"
            @click="change(tool.name)"
          >
            {{ t("schemeAbilities.unbind") }}
          </UiButton>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.scheme-abilities {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  color: var(--color-text);
  overflow-wrap: anywhere;
  font-size: var(--font-ui-body);
}

.scheme-abilities h3,
.scheme-abilities h4,
.scheme-note,
.scheme-state,
.scheme-alert {
  margin: 0;
}

.scheme-abilities h3,
.scheme-abilities h4 {
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.scheme-note,
.scheme-state,
.scheme-alert {
  font-size: var(--font-ui-note);
  line-height: 1.5;
}

.scheme-note {
  color: var(--color-text-muted);
}

.scheme-state,
.scheme-alert {
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.scheme-state {
  background: var(--color-surface-2);
  color: var(--color-text-muted);
}

.scheme-alert {
  border-color: var(--color-danger);
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-surface));
  color: var(--color-danger);
}

.scheme-form {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.scheme-form label {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.scheme-form select,
.scheme-form input {
  min-width: 0;
  padding: 0.25rem 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}

.scheme-cards,
.scheme-files {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.scheme-card,
.scheme-content {
  min-width: 0;
  padding: 0.75rem 1rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.scheme-card p,
.scheme-content p {
  margin: 0.25rem 0 0;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.scheme-card code,
.scheme-content pre {
  font-size: var(--font-ui-code);
}

.scheme-files li {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem 0.75rem;
  color: var(--color-text-muted);
  font-size: var(--font-ui-note);
}

.scheme-content pre {
  margin: 0.5rem 0 0;
  padding: 0.75rem;
  overflow: auto;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text);
}
</style>
