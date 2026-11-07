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
    <p>{{ t("schemeAbilities.scope", { agent: agentId }) }}</p>
    <button type="button" class="btn-secondary" :disabled="loading || saving" @click="refresh">
      {{ t("schemeAbilities.refresh") }}
    </button>
    <p v-if="loading" role="status">{{ t("schemeAbilities.loading") }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="view">
      <p>{{ t("schemeAbilities.revision", { revision: view.revision, run: view.run_id }) }}</p>
      <p v-if="!choices.length">{{ t("schemeAbilities.empty") }}</p>
      <p v-if="choices.some((item) => !item.available)">{{ t("schemeAbilities.unavailable") }}</p>
      <form v-if="!readonly" @submit.prevent="change()">
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
        <button type="submit" class="btn-primary" :disabled="saving || !request">
          {{ t("schemeAbilities.bind") }}
        </button>
      </form>
      <h4>{{ t("schemeAbilities.content") }}</h4>
      <ul>
        <li v-for="ability in view.abilities" :key="ability.alias">
          <code>{{ ability.alias }}</code>
          <p>{{ t("schemeAbilities.version", { version: ability.version_digest }) }}</p>
          <template v-if="ability.skill">
            <p>{{ ability.skill.title }} — {{ ability.skill.description }}</p>
            <button
              v-if="ability.skill.body_allowed"
              type="button"
              class="btn-secondary"
              :disabled="reading"
              @click="
                readContent(ability.alias, `skill/${ability.skill.key}`, ability.version_digest)
              "
            >
              {{ t("schemeAbilities.readBody") }}
            </button>
            <ul v-for="role in ['assets', 'references'] as const" :key="role">
              <li v-for="entry in ability.skill[role]" :key="entry.member">
                <code>{{ role }}/{{ entry.key }}</code>
                <span>{{ entry.media_type }} · {{ entry.size }}</span>
                <button
                  type="button"
                  class="btn-secondary"
                  :disabled="reading"
                  @click="readContent(ability.alias, entry.member, ability.version_digest)"
                >
                  {{ t("schemeAbilities.readContent") }}
                </button>
              </li>
            </ul>
          </template>
        </li>
      </ul>
      <div v-if="content">
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
        <button type="button" class="btn-secondary" @click="downloadChunk">
          {{ t("schemeAbilities.downloadChunk") }}
        </button>
        <button
          v-if="content.next_offset !== null && contentTarget"
          type="button"
          class="btn-secondary"
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
        </button>
      </div>
      <h4>{{ t("schemeAbilities.bound") }}</h4>
      <p v-if="!view.bound_tools.length">{{ t("schemeAbilities.noTools") }}</p>
      <ul class="bound-tools">
        <li v-for="tool in view.bound_tools" :key="tool.name">
          <code>{{ tool.name }}</code>
          <span>{{ tool.alias }}/{{ tool.member }}</span>
          <button
            v-if="!readonly"
            type="button"
            class="btn-secondary"
            :disabled="saving"
            @click="change(tool.name)"
          >
            {{ t("schemeAbilities.unbind") }}
          </button>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.scheme-abilities {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-body);
}
</style>
