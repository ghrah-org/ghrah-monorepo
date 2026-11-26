<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { HttpClient, HttpError, useConnectionStore } from "@ghrah/observer-core";
import {
  AbilityMcpDraftProbeViewSchema,
  AbilityMcpInstallViewSchema,
  AgentconfMcpProviderListViewSchema,
  type AbilityMcpDraftProbeView,
  type AbilityMcpInstallView,
  type AbilityMcpToolView,
  type AgentconfMcpProviderView,
} from "@ghrah/protocol";
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import UiButton from "@/components/ui/button.vue";
import UiSelect from "@/components/ui/select.vue";
import UiTextField from "@/components/ui/text-field.vue";

const emit = defineEmits<{ close: []; installed: [view: AbilityMcpInstallView] }>();
const { t } = useI18n();
const connection = useConnectionStore();

const PATH = {
  probe: "/api/v1/agentconf/mcp/probe",
  install: "/api/v1/ability-packages/mcp/install",
} as const;
const STAGES = ["agentconf", "members", "package", "lock", "catalog"] as const;
const STEP_KEYS = ["connect", "tools", "ability"] as const;
const NAME_RE = /^[a-z][a-z0-9_-]*$/;
const VERSION_RE = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const PREFIX_RE = /^[a-z0-9_]*$/;

type Step = 1 | 2 | 3 | "installing" | "success";
type ProbeState = "idle" | "testing" | "ok" | "error";
type StageState = "pending" | "running" | "done" | "failed";

const step = ref<Step>(1);
const probeState = ref<ProbeState>("idle");
const probeView = ref<AbilityMcpDraftProbeView | null>(null);
const probeError = ref("");
const tools = ref<AbilityMcpToolView[]>([]);
const selected = ref<Set<string>>(new Set());
const filter = ref("");
const expanded = ref<Set<string>>(new Set());
const providers = ref<AgentconfMcpProviderView[]>([]);
const providerId = ref("");
const stepError = ref("");
const busy = ref(false);
const installed = ref<AbilityMcpInstallView | null>(null);
const installFailure = ref("");
const stageStates = ref<StageState[]>(STAGES.map(() => "pending"));
const previewTab = ref<"members" | "yaml">("members");
const copied = ref(false);
const panel = ref<HTMLElement | null>(null);
let returnFocus: HTMLElement | null = null;
let copiedTimer: ReturnType<typeof setTimeout> | null = null;

const form = reactive({
  name: "",
  vendor: "",
  transport: "streamable_http",
  auth: "bearer",
  header: "X-API-Key",
  url: "",
  key: "",
  timeout: 5000,
});
const ability = reactive({
  name: "",
  version: "0.0.1",
  title: "",
  description: "",
  prefix: "",
});

const currentStep = computed(() =>
  step.value === "installing" || step.value === "success" ? 3 : step.value,
);
const steps = computed(() =>
  STEP_KEYS.map((key) => t(`config.connections.mcpInstall.steps.${key}`)),
);
const drawerSubtitle = computed(() => {
  if (step.value === 1) return t("config.connections.mcpInstall.subtitleConnect");
  if (step.value === 2) return t("config.connections.mcpInstall.subtitleTools");
  if (step.value === 3) return t("config.connections.mcpInstall.subtitleAbility");
  if (step.value === "installing") {
    return t("config.connections.mcpInstall.subtitleInstalling");
  }
  return t("config.connections.mcpInstall.subtitleSuccess");
});
const authMode = computed({
  get: () => form.auth,
  set: (value: string) => {
    form.auth = value === "header" ? "header" : "bearer";
  },
});
const selectedProvider = computed(
  () => providers.value.find((provider) => provider.id === providerId.value) ?? null,
);
const providerWithoutKey = computed(
  () => selectedProvider.value !== null && !selectedProvider.value.api_key_configured,
);
const selectedTools = computed(() => tools.value.filter((tool) => selected.value.has(tool.name)));
const allSelected = computed(
  () => tools.value.length > 0 && selected.value.size === tools.value.length,
);
const filteredTools = computed(() => {
  const needle = filter.value.trim().toLowerCase();
  if (!needle) return tools.value;
  return tools.value.filter((tool) =>
    `${tool.name} ${tool.title ?? ""}`.toLowerCase().includes(needle),
  );
});
const serverRef = computed(() => `${slugify(ability.name || "mcp")}_mcp`);
const packageName = computed(() => `mcp.${ability.name || "mcp"}`);
const installStages = computed(() =>
  STAGES.map((key, index) => ({
    key,
    state: stageStates.value[index],
    title: t(`config.connections.mcpInstall.stages.${key}.title`),
    detail: stageDetail(key),
  })),
);
const members = computed(() => memberEntities());
const previewText = computed(() =>
  previewTab.value === "yaml" ? abilityYaml() : JSON.stringify(members.value, null, 2),
);

watch(
  () => [form.name, form.vendor, form.url, form.auth, form.header, form.key, form.timeout],
  () => {
    if (probeState.value === "ok" || probeState.value === "error") {
      probeState.value = "idle";
      probeView.value = null;
      probeError.value = "";
      tools.value = [];
      selected.value = new Set();
      expanded.value = new Set();
    }
  },
);

watch(providerId, () => {
  const provider = selectedProvider.value;
  if (!provider) return;
  form.vendor = provider.vendor ?? "";
  if (!form.name) form.name = provider.name.replace(/-provider$/, "");
});

function client() {
  return new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken });
}

function slugify(value: string): string {
  const ascii = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!ascii) return "mcp";
  return /^[a-z]/.test(ascii) ? ascii : `mcp_${ascii}`;
}

function memberKey(name: string): string {
  return `${ability.prefix}${name}`.replace(/[^a-z0-9_]/g, "_");
}

function errorText(error: unknown): string {
  if (error instanceof HttpError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

function fieldCount(tool: AbilityMcpToolView, side: "input" | "output"): number {
  const schema = side === "input" ? tool.input_schema : tool.output_schema;
  const properties = schema?.properties;
  return properties && typeof properties === "object" ? Object.keys(properties).length : 0;
}

function schemaText(tool: AbilityMcpToolView): string {
  return JSON.stringify(
    { input_schema: tool.input_schema ?? {}, output_schema: tool.output_schema ?? {} },
    null,
    2,
  );
}

function authBody() {
  if (providerId.value) {
    return {
      mode: "provider" as const,
      provider_id: providerId.value,
      header: null,
      key: "",
    };
  }
  return form.auth === "header"
    ? { mode: "header" as const, header: form.header.trim(), key: form.key }
    : { mode: "bearer" as const, header: null, key: form.key };
}

function validateConnection(): string {
  if (!form.name.trim()) return t("config.connections.mcpInstall.validation.name");
  if (!form.url.trim()) return t("config.connections.mcpInstall.validation.url");
  let parsed: URL;
  try {
    parsed = new URL(form.url.trim());
  } catch {
    return t("config.connections.mcpInstall.validation.url");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    return t("config.connections.mcpInstall.validation.url");
  }
  if (!providerId.value && !form.key.trim()) {
    return t("config.connections.mcpInstall.validation.key");
  }
  if (!providerId.value && form.auth === "header" && !form.header.trim()) {
    return t("config.connections.mcpInstall.validation.header");
  }
  const timeout = Number(form.timeout);
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 10000) {
    return t("config.connections.mcpInstall.validation.timeout");
  }
  return "";
}

function ensureAbilityIdentity() {
  const slug = slugify(form.name);
  if (!ability.name) ability.name = slug;
  if (!ability.title) ability.title = form.name;
  if (!ability.description) {
    ability.description = t("config.connections.mcpInstall.defaultDescription", {
      name: form.name,
    });
  }
  if (!ability.prefix) ability.prefix = `${slug}_`;
}

async function probeServer() {
  stepError.value = "";
  probeError.value = "";
  const validation = validateConnection();
  if (validation) {
    stepError.value = validation;
    return;
  }
  busy.value = true;
  probeState.value = "testing";
  try {
    const view = AbilityMcpDraftProbeViewSchema.parse(
      await client().post(PATH.probe, {
        url: form.url.trim(),
        auth: authBody(),
        timeout_ms: Number(form.timeout),
        include_resources: false,
      }),
    );
    probeView.value = view;
    if (!view.ok) {
      tools.value = [];
      selected.value = new Set();
      probeState.value = "error";
      probeError.value = view.error?.message ?? t("config.connections.mcpInstall.probeFailed");
      return;
    }
    tools.value = view.tools;
    selected.value = new Set();
    expanded.value = new Set();
    ensureAbilityIdentity();
    probeState.value = "ok";
    if (!view.tools.length) {
      stepError.value = t("config.connections.mcpInstall.emptyTools");
    }
  } catch (error) {
    probeState.value = "error";
    probeError.value = errorText(error);
  } finally {
    busy.value = false;
  }
}

function toggleTool(name: string) {
  const next = new Set(selected.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  selected.value = next;
}

function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(tools.value.map((tool) => tool.name));
}

function toggleSchema(name: string) {
  const next = new Set(expanded.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  expanded.value = next;
}

function goNext() {
  stepError.value = "";
  if (step.value === 1) {
    if (probeState.value !== "ok" || !tools.value.length) return;
    step.value = 2;
    return;
  }
  if (step.value === 2) {
    if (!selected.value.size) {
      stepError.value = t("config.connections.mcpInstall.validation.selection");
      return;
    }
    step.value = 3;
  }
}

function goBack() {
  stepError.value = "";
  if (step.value === 3) step.value = 2;
  else if (step.value === 2) step.value = 1;
}

function validateAbility(): string {
  if (!NAME_RE.test(ability.name.trim())) {
    return t("config.connections.mcpInstall.validation.abilityName");
  }
  if (!VERSION_RE.test(ability.version.trim())) {
    return t("config.connections.mcpInstall.validation.version");
  }
  if (!PREFIX_RE.test(ability.prefix)) {
    return t("config.connections.mcpInstall.validation.prefix");
  }
  if (!selected.value.size) return t("config.connections.mcpInstall.validation.selection");
  return "";
}

function memberEntities() {
  const ref = serverRef.value;
  return selectedTools.value.map((tool) => ({
    namespace: "mcp",
    name: ability.name,
    member: `mcp.${ability.name}.${memberKey(tool.name)}`,
    type: "mcp",
    version: ability.version,
    ability_type: "external",
    axes: { visible: true, bind: true },
    data: {
      title: tool.title ?? tool.name,
      description: tool.description ?? "",
      server: ref,
      tool: tool.name,
      input_schema: tool.input_schema ?? {},
      output_schema: tool.output_schema ?? {},
      read_only: tool.read_only_hint === true,
      tags: ["mcp", ref],
      effects: ["net:request"],
      permissions: { net_access: true },
      resources: {},
      execution: {
        mode: "request",
        timeout_ms: 30000,
        cancellation: "supported",
        on_revoke: "drain",
        retry: "never",
      },
      expose: true,
    },
  }));
}

function abilityYaml(): string {
  const lines = [
    "format: ghrah.ability-definition",
    "schema_version: '2'",
    "origin: mcp",
    "identity:",
    "  namespace: mcp",
    `  name: ${ability.name}`,
    `  version: '${ability.version}'`,
    `title: ${ability.title}`,
    `description: ${ability.description}`,
    "mcp:",
    `  server: ${serverRef.value}`,
    "members:",
  ];
  members.value.forEach((entity, index) => {
    const data = entity.data;
    lines.push(
      "  - namespace: mcp",
      `    name: ${ability.name}`,
      `    member: ${entity.member}`,
      "    type: mcp",
      `    version: '${ability.version}'`,
      "    ability_type: external",
      "    axes: {visible: true, bind: true}",
      "    data:",
      `      title: ${data.title}`,
      `      description: ${data.description}`,
      `      server: ${data.server}`,
      `      tool: ${data.tool}`,
      `      input_schema: ${JSON.stringify(data.input_schema)}`,
      `      output_schema: ${JSON.stringify(data.output_schema)}`,
      `      read_only: ${data.read_only}`,
      `      execution: ${JSON.stringify(data.execution)}`,
      "      permissions: {net_access: true}",
      "      effects: [net:request]",
      "      expose: true",
    );
    if (index < members.value.length - 1) lines.push("");
  });
  return lines.join("\n");
}

function stageDetail(key: (typeof STAGES)[number]): string {
  if (key === "agentconf") {
    return t("config.connections.mcpInstall.stages.agentconf.detail", {
      name: ability.name,
      ref: serverRef.value,
    });
  }
  if (key === "members") {
    return t("config.connections.mcpInstall.stages.members.detail", { count: selected.value.size });
  }
  if (key === "package") {
    return `${packageName.value}@${ability.version}`;
  }
  if (key === "lock") return installed.value?.digest ?? "—";
  return t("config.connections.mcpInstall.stages.catalog.detail");
}

async function installAbility() {
  stepError.value = "";
  const validation = validateAbility();
  if (validation) {
    stepError.value = validation;
    return;
  }
  installFailure.value = "";
  stageStates.value = STAGES.map(() => "pending");
  step.value = "installing";
  busy.value = true;
  stageStates.value = STAGES.map((_, index) => (index === 0 ? "running" : "pending"));
  try {
    const view = AbilityMcpInstallViewSchema.parse(
      await client().post(PATH.install, {
        connection: {
          url: form.url.trim(),
          auth: authBody(),
          timeout_ms: Number(form.timeout),
        },
        server: {
          ref: serverRef.value,
          display_name: form.name.trim() || null,
          vendor: form.vendor.trim() || null,
        },
        tools: [...selected.value],
        ability: {
          namespace: "mcp",
          name: ability.name.trim(),
          version: ability.version.trim() || null,
          title: ability.title.trim() || ability.name.trim(),
          description: ability.description.trim(),
          prefix: ability.prefix,
        },
      }),
    );
    installed.value = view;
    stageStates.value = STAGES.map(() => "done");
    step.value = "success";
    emit("installed", view);
  } catch (error) {
    stageStates.value = STAGES.map((_, index) => (index === 0 ? "failed" : "pending"));
    installFailure.value = errorText(error);
  } finally {
    busy.value = false;
  }
}

async function copyPreview() {
  try {
    await navigator.clipboard?.writeText(previewText.value);
    copied.value = true;
    if (copiedTimer) clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => {
      copied.value = false;
    }, 1500);
  } catch {
    copied.value = false;
  }
}

function resetDialog() {
  step.value = 1;
  probeState.value = "idle";
  probeView.value = null;
  probeError.value = "";
  tools.value = [];
  selected.value = new Set();
  expanded.value = new Set();
  filter.value = "";
  stepError.value = "";
  installFailure.value = "";
  installed.value = null;
  previewTab.value = "members";
  stageStates.value = STAGES.map(() => "pending");
  ability.name = "";
  ability.version = "0.0.1";
  ability.title = "";
  ability.description = "";
  ability.prefix = "";
  nextTick(() => panel.value?.querySelector<HTMLElement>("input, select")?.focus());
}

function requestClose() {
  if (step.value === "installing" || busy.value) return;
  emit("close");
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    requestClose();
    return;
  }
  if (event.key !== "Tab" || !panel.value) return;
  const items = Array.from(
    panel.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((item) => !item.hasAttribute("hidden"));
  if (!items.length) {
    event.preventDefault();
    panel.value.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === panel.value)
  ) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

async function loadProviders() {
  try {
    const view = AgentconfMcpProviderListViewSchema.parse(
      await client().get("/api/v1/agentconf/mcp-providers"),
    );
    providers.value = view.providers;
    if (providers.value.length === 1) {
      providerId.value = providers.value[0].id;
    }
  } catch {
    providers.value = [];
  }
}

onMounted(async () => {
  returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  void loadProviders();
  await nextTick();
  panel.value?.querySelector<HTMLElement>("input, select")?.focus();
});

onUnmounted(() => {
  if (copiedTimer) clearTimeout(copiedTimer);
  const target = returnFocus;
  void nextTick(() => target?.focus());
});
</script>

<template>
  <Teleport to="body">
    <div class="mcp-drawer-scrim" @pointerdown.self="requestClose">
      <aside
        ref="panel"
        class="mcp-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-drawer-title"
        tabindex="-1"
        @keydown="onKeydown"
      >
        <header class="mcp-drawer-head">
          <div>
            <h2 id="mcp-drawer-title">{{ t("config.connections.mcpInstall.title") }}</h2>
            <p>{{ drawerSubtitle }}</p>
          </div>
          <UiButton
            type="button"
            variant="ghost"
            size="sm"
            :aria-label="t('common.close')"
            :disabled="busy"
            @click="requestClose"
          >
            ×
          </UiButton>
        </header>

        <ol class="mcp-steps">
          <li
            v-for="(label, index) in steps"
            :key="label"
            :data-state="
              step === 'success' || (step === 'installing' && index < 3)
                ? 'done'
                : index + 1 < currentStep
                  ? 'done'
                  : index + 1 === currentStep
                    ? 'active'
                    : ''
            "
          >
            <span class="mcp-step-num">{{
              step === "success" || index + 1 < currentStep ? "✓" : index + 1
            }}</span>
            {{ label }}
          </li>
        </ol>

        <div class="mcp-drawer-body">
          <p v-if="stepError" class="mcp-alert" role="alert">{{ stepError }}</p>

          <section v-if="step === 1" class="mcp-section">
            <div class="mcp-form-grid">
              <label class="mcp-field mcp-field-full">
                <span>{{ t("config.connections.mcpInstall.fields.provider") }}</span>
                <UiSelect v-model="providerId">
                  <option value="">
                    {{ t("config.connections.mcpInstall.providerNew") }}
                  </option>
                  <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                    {{ provider.name }}{{ provider.vendor ? ` · ${provider.vendor}` : ""
                    }}{{
                      provider.api_key_configured
                        ? ""
                        : ` · ${t("config.connections.mcpInstall.providerNoKey")}`
                    }}
                  </option>
                </UiSelect>
                <small>
                  {{
                    selectedProvider
                      ? providerWithoutKey
                        ? t("config.connections.mcpInstall.providerNoKeyHint")
                        : t("config.connections.mcpInstall.providerReuseHint")
                      : t("config.connections.mcpInstall.providerNewHint")
                  }}
                </small>
              </label>
              <label class="mcp-field">
                <span>{{ t("config.connections.mcpInstall.fields.name") }} *</span>
                <UiTextField
                  v-model="form.name"
                  :placeholder="t('config.connections.mcpInstall.placeholders.name')"
                />
              </label>
              <label class="mcp-field">
                <span>{{ t("config.connections.mcpInstall.fields.vendor") }}</span>
                <input v-if="providerId" class="mcp-native-input" :value="form.vendor" disabled />
                <UiTextField
                  v-else
                  v-model="form.vendor"
                  :placeholder="t('config.connections.mcpInstall.placeholders.vendor')"
                />
              </label>
              <label class="mcp-field">
                <span>{{ t("config.connections.mcpInstall.fields.transport") }}</span>
                <UiSelect v-model="form.transport">
                  <option value="streamable_http">
                    {{ t("config.connections.mcpInstall.transportHttp") }}
                  </option>
                  <option value="stdio" disabled>
                    {{ t("config.connections.mcpInstall.transportStdio") }}
                  </option>
                </UiSelect>
              </label>
              <template v-if="!providerId">
                <label class="mcp-field">
                  <span>{{ t("config.connections.mcpInstall.fields.auth") }}</span>
                  <UiSelect v-model="authMode">
                    <option value="bearer">
                      {{ t("config.connections.mcpInstall.authBearer") }}
                    </option>
                    <option value="header">
                      {{ t("config.connections.mcpInstall.authHeader") }}
                    </option>
                  </UiSelect>
                </label>
                <label v-if="form.auth === 'header'" class="mcp-field">
                  <span>{{ t("config.connections.mcpInstall.headerName") }}</span>
                  <UiTextField v-model="form.header" />
                </label>
              </template>
              <label class="mcp-field mcp-field-full">
                <span>{{ t("config.connections.mcpInstall.fields.url") }} *</span>
                <UiTextField
                  v-model="form.url"
                  :placeholder="t('config.connections.mcpInstall.placeholders.url')"
                />
                <small>{{ t("config.connections.mcpInstall.urlHint") }}</small>
              </label>
              <label v-if="!providerId" class="mcp-field mcp-field-full">
                <span>{{ t("config.connections.mcpInstall.fields.key") }} *</span>
                <UiTextField
                  v-model="form.key"
                  type="password"
                  autocomplete="new-password"
                  :placeholder="t('config.connections.mcpInstall.keyPlaceholder')"
                />
                <small>{{ t("config.connections.mcpInstall.keyHint") }}</small>
              </label>
              <p v-else class="mcp-field mcp-field-full mcp-note">
                {{
                  providerWithoutKey
                    ? t("config.connections.mcpInstall.providerNoKeyConnectionHint")
                    : t("config.connections.mcpInstall.providerCredentialHint")
                }}
              </p>
              <label class="mcp-field">
                <span>{{ t("config.connections.mcpInstall.fields.timeout") }}</span>
                <UiTextField v-model.number="form.timeout" type="number" min="1000" max="10000" />
                <small>{{ t("config.connections.mcpInstall.timeoutHint") }}</small>
              </label>
              <div class="mcp-field">
                <span>{{ t("config.connections.mcpInstall.fields.test") }}</span>
                <UiButton
                  type="button"
                  variant="secondary"
                  :disabled="busy || probeState === 'testing'"
                  @click="probeServer"
                >
                  {{
                    probeState === "testing"
                      ? t("config.connections.mcpInstall.probing")
                      : t("config.connections.mcpInstall.probe")
                  }}
                </UiButton>
              </div>
            </div>

            <div v-if="probeState === 'testing'" class="mcp-status" role="status">
              <span class="mcp-spinner" aria-hidden="true" />
              <div>
                <b>{{ t("config.connections.mcpInstall.probingTitle") }}</b>
                <small>{{ t("config.connections.mcpInstall.probingHint") }}</small>
              </div>
            </div>
            <div v-else-if="probeState === 'ok' && probeView" class="mcp-status mcp-status-ok">
              <div>
                <b>{{
                  t("config.connections.mcpInstall.probeOk", { ms: probeView.elapsed_ms })
                }}</b>
                <small>
                  {{
                    t("config.connections.mcpInstall.probeServer", {
                      name: probeView.server_info?.name ?? "unknown",
                      version: probeView.server_info?.version ?? "",
                      count: probeView.tools.length,
                    })
                  }}
                </small>
              </div>
            </div>
            <div v-else-if="probeState === 'error'" class="mcp-status mcp-status-error">
              <div>
                <b>{{ probeError || t("config.connections.mcpInstall.probeFailed") }}</b>
                <small>{{ t("config.connections.mcpInstall.probeErrorHint") }}</small>
              </div>
            </div>

            <p class="mcp-note">{{ t("config.connections.mcpInstall.probeIdleNote") }}</p>
          </section>

          <section v-else-if="step === 2" class="mcp-section">
            <div v-if="probeState === 'error'" class="mcp-status mcp-status-error">
              <div>
                <b>{{ probeError || t("config.connections.mcpInstall.probeFailed") }}</b>
                <small>{{ t("config.connections.mcpInstall.probeErrorHint") }}</small>
              </div>
            </div>
            <div class="mcp-tools-head">
              <span class="mcp-badge">{{ form.name || "MCP" }}</span>
              <span class="mcp-note">{{
                t("config.connections.mcpInstall.toolsFound", { count: tools.length })
              }}</span>
              <span class="mcp-spacer" />
              <UiButton
                type="button"
                variant="ghost"
                size="sm"
                :disabled="busy"
                @click="probeServer"
              >
                {{ t("config.connections.mcpInstall.reProbe") }}
              </UiButton>
            </div>
            <div class="mcp-tools-head">
              <label class="mcp-tool-filter">
                <input
                  v-model="filter"
                  type="search"
                  :placeholder="t('config.connections.mcpInstall.toolFilter')"
                />
              </label>
              <label class="mcp-check">
                <input type="checkbox" :checked="allSelected" @change="toggleAll" />
                {{ t("config.connections.mcpInstall.selectAll") }}
              </label>
              <span class="mcp-spacer" />
              <span class="mcp-note">
                {{ t("config.connections.mcpInstall.selected", { count: selected.size }) }}
              </span>
            </div>
            <div v-if="!filteredTools.length" class="mcp-empty">
              {{
                tools.length
                  ? t("config.connections.mcpInstall.noMatches")
                  : t("config.connections.mcpInstall.noTools")
              }}
            </div>
            <div class="mcp-tool-list">
              <article
                v-for="tool in filteredTools"
                :key="tool.name"
                class="mcp-tool"
                :data-selected="selected.has(tool.name)"
              >
                <div class="mcp-tool-main" @click="toggleTool(tool.name)">
                  <input
                    type="checkbox"
                    :checked="selected.has(tool.name)"
                    :aria-label="tool.title ?? tool.name"
                    @click.stop
                    @change="toggleTool(tool.name)"
                  />
                  <div class="mcp-tool-content">
                    <div class="mcp-tool-title">
                      <strong>{{ tool.title || tool.name }}</strong>
                      <code v-if="tool.title && tool.title !== tool.name">{{ tool.name }}</code>
                      <span v-if="tool.read_only_hint === true" class="mcp-badge mcp-badge-ok">
                        {{ t("config.connections.mcpInstall.readonly") }}
                      </span>
                      <span v-else class="mcp-badge mcp-badge-warn">
                        {{ t("config.connections.mcpInstall.write") }}
                      </span>
                    </div>
                    <p v-if="tool.description" class="mcp-tool-desc">{{ tool.description }}</p>
                    <div class="mcp-tool-meta">
                      {{
                        t("config.connections.mcpInstall.toolMeta", {
                          input: fieldCount(tool, "input"),
                          output: fieldCount(tool, "output"),
                          hint: tool.read_only_hint === true,
                        })
                      }}
                    </div>
                  </div>
                  <UiButton
                    type="button"
                    variant="ghost"
                    size="xs"
                    @click.stop="toggleSchema(tool.name)"
                  >
                    {{
                      expanded.has(tool.name)
                        ? t("config.connections.mcpInstall.hideSchema")
                        : t("config.connections.mcpInstall.schema")
                    }}
                  </UiButton>
                </div>
                <pre v-if="expanded.has(tool.name)" class="mcp-schema">{{ schemaText(tool) }}</pre>
              </article>
            </div>
            <div class="mcp-selected-bar">
              <span>{{ t("config.connections.mcpInstall.selectedBar") }}</span>
              <span class="mcp-spacer" />
              <span>{{
                t("config.connections.mcpInstall.selectedFooter", {
                  count: selected.size,
                  total: tools.length,
                })
              }}</span>
            </div>
          </section>

          <section v-else-if="step === 3" class="mcp-section">
            <div class="mcp-ability-layout">
              <div>
                <h3 class="mcp-section-title">
                  {{ t("config.connections.mcpInstall.abilityTitle") }}
                </h3>
                <div class="mcp-form-grid">
                  <label class="mcp-field">
                    <span>{{ t("config.connections.mcpInstall.namespace") }}</span>
                    <input class="mcp-native-input" value="mcp" disabled />
                  </label>
                  <label class="mcp-field">
                    <span>{{ t("config.connections.mcpInstall.abilityName") }} *</span>
                    <UiTextField v-model="ability.name" />
                  </label>
                  <label class="mcp-field">
                    <span>{{ t("config.connections.mcpInstall.abilityVersion") }}</span>
                    <UiTextField v-model="ability.version" />
                  </label>
                  <label class="mcp-field">
                    <span>{{ t("config.connections.mcpInstall.prefix") }}</span>
                    <UiTextField v-model="ability.prefix" />
                  </label>
                  <label class="mcp-field mcp-field-full">
                    <span>{{ t("config.connections.mcpInstall.abilityTitleLabel") }}</span>
                    <UiTextField v-model="ability.title" />
                  </label>
                  <label class="mcp-field mcp-field-full">
                    <span>{{ t("config.connections.mcpInstall.abilityDescription") }}</span>
                    <UiTextField v-model="ability.description" />
                  </label>
                </div>
              </div>
              <aside class="mcp-summary">
                <h3>{{ t("config.connections.mcpInstall.summary") }}</h3>
                <dl>
                  <dt>{{ t("config.connections.mcpInstall.summaryServer") }}</dt>
                  <dd>
                    <code>{{ serverRef }}</code>
                  </dd>
                  <dt>{{ t("config.connections.mcpInstall.summaryProvider") }}</dt>
                  <dd>
                    <code>{{ ability.name }}</code>
                  </dd>
                  <dt>{{ t("config.connections.mcpInstall.summaryMembers") }}</dt>
                  <dd>
                    {{ t("config.connections.mcpInstall.memberCount", { count: selected.size }) }}
                  </dd>
                  <dt>{{ t("config.connections.mcpInstall.summaryPackage") }}</dt>
                  <dd>
                    <code>{{ packageName }}@{{ ability.version }}</code>
                  </dd>
                </dl>
                <div class="mcp-chips">
                  <code v-for="tool in selectedTools" :key="tool.name" class="mcp-chip">
                    {{ `mcp.${ability.name}.${memberKey(tool.name)}` }}
                  </code>
                </div>
              </aside>
            </div>

            <div class="mcp-preview-head">
              <button
                type="button"
                :aria-selected="previewTab === 'members'"
                @click="previewTab = 'members'"
              >
                {{ t("config.connections.mcpInstall.previewMembers", { count: members.length }) }}
              </button>
              <button
                type="button"
                :aria-selected="previewTab === 'yaml'"
                @click="previewTab = 'yaml'"
              >
                {{ t("config.connections.mcpInstall.previewYaml") }}
              </button>
              <span class="mcp-spacer" />
              <UiButton type="button" variant="ghost" size="sm" @click="copyPreview">
                {{
                  copied
                    ? t("config.connections.mcpInstall.copied")
                    : t("config.connections.mcpInstall.copy")
                }}
              </UiButton>
            </div>
            <pre class="mcp-preview">{{ previewText }}</pre>
            <p class="mcp-note">{{ t("config.connections.mcpInstall.previewNote") }}</p>
          </section>

          <section v-else-if="step === 'installing'" class="mcp-section">
            <h3 class="mcp-section-title">
              {{ t("config.connections.mcpInstall.installingTitle") }}
            </h3>
            <p v-if="installFailure" class="mcp-alert" role="alert">{{ installFailure }}</p>
            <ol class="mcp-install-stages">
              <li v-for="stage in installStages" :key="stage.key" :data-state="stage.state">
                <span class="mcp-stage-icon">
                  {{
                    stage.state === "done"
                      ? "✓"
                      : stage.state === "failed"
                        ? "×"
                        : stage.state === "running"
                          ? "…"
                          : "•"
                  }}
                </span>
                <div>
                  <b>{{ stage.title }}</b>
                  <small>{{ stage.detail }}</small>
                </div>
              </li>
            </ol>
          </section>

          <section v-else class="mcp-section mcp-success">
            <div class="mcp-success-mark">✓</div>
            <h3>{{ t("config.connections.mcpInstall.successTitle") }}</h3>
            <p>
              {{
                t("config.connections.mcpInstall.successBody", {
                  count: installed?.member_count ?? 0,
                })
              }}
            </p>
            <dl class="mcp-success-facts">
              <dt>{{ t("config.connections.mcpInstall.summaryPackage") }}</dt>
              <dd>
                <code>{{ installed?.package }}@{{ installed?.version }}</code>
              </dd>
              <dt>{{ t("config.connections.mcpInstall.summaryMembers") }}</dt>
              <dd>
                {{
                  t("config.connections.mcpInstall.memberCount", {
                    count: installed?.member_count ?? 0,
                  })
                }}
              </dd>
              <dt>{{ t("config.connections.mcpInstall.successDigest") }}</dt>
              <dd>
                <code>{{ installed?.digest }}</code>
              </dd>
              <dt>{{ t("config.connections.mcpInstall.successAgentconf") }}</dt>
              <dd>
                {{
                  t("config.connections.mcpInstall.successAgentconfDetail", {
                    provider: installed?.agentconf.provider_id,
                    server: installed?.agentconf.server_id,
                  })
                }}
              </dd>
              <dt>{{ t("config.connections.mcpInstall.successVisibility") }}</dt>
              <dd>{{ t("config.connections.mcpInstall.successVisibilityDetail") }}</dd>
            </dl>
          </section>
        </div>

        <footer class="mcp-drawer-foot">
          <span v-if="step === 1" class="mcp-note">{{
            t("config.connections.mcpInstall.footerSecret")
          }}</span>
          <span v-else-if="step === 2" class="mcp-note">
            {{
              t("config.connections.mcpInstall.selectedFooter", {
                count: selected.size,
                total: tools.length,
              })
            }}
          </span>
          <span v-else-if="step === 3" class="mcp-note">
            {{ t("config.connections.mcpInstall.footerAbility", { count: selected.size }) }}
          </span>
          <span v-else-if="step === 'installing'" class="mcp-note">
            {{ t("config.connections.mcpInstall.installingNote") }}
          </span>
          <span v-else class="mcp-note">{{
            t("config.connections.mcpInstall.successVisibilityDetail")
          }}</span>
          <span class="mcp-spacer" />
          <template v-if="step === 1">
            <UiButton type="button" variant="ghost" :disabled="busy" @click="requestClose">
              {{ t("config.connections.mcpInstall.cancel") }}
            </UiButton>
            <UiButton
              type="button"
              variant="primary"
              :disabled="probeState !== 'ok' || !tools.length || busy"
              @click="goNext"
            >
              {{ t("config.connections.mcpInstall.nextTools") }}
            </UiButton>
          </template>
          <template v-else-if="step === 2">
            <UiButton type="button" variant="ghost" @click="goBack">
              {{ t("config.connections.mcpInstall.back") }}
            </UiButton>
            <UiButton type="button" variant="primary" :disabled="!selected.size" @click="goNext">
              {{ t("config.connections.mcpInstall.nextAbility") }}
            </UiButton>
          </template>
          <template v-else-if="step === 3">
            <UiButton type="button" variant="ghost" @click="goBack">
              {{ t("config.connections.mcpInstall.back") }}
            </UiButton>
            <UiButton type="button" variant="primary" :disabled="busy" @click="installAbility">
              {{ t("config.connections.mcpInstall.generate") }}
            </UiButton>
          </template>
          <template v-else-if="step === 'installing'">
            <UiButton v-if="installFailure" type="button" variant="ghost" @click="step = 3">
              {{ t("config.connections.mcpInstall.back") }}
            </UiButton>
            <UiButton
              v-if="installFailure"
              type="button"
              variant="primary"
              :disabled="busy"
              @click="installAbility"
            >
              {{ t("config.connections.mcpInstall.retry") }}
            </UiButton>
            <UiButton v-else type="button" variant="primary" disabled>
              {{ t("config.connections.mcpInstall.installing") }}
            </UiButton>
          </template>
          <template v-else>
            <UiButton type="button" variant="ghost" @click="resetDialog">
              {{ t("config.connections.mcpInstall.again") }}
            </UiButton>
            <UiButton type="button" variant="primary" @click="requestClose">
              {{ t("config.connections.mcpInstall.done") }}
            </UiButton>
          </template>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.mcp-drawer-scrim {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  justify-content: flex-end;
  background: var(--ui-overlay);
}

.mcp-drawer {
  display: flex;
  flex-direction: column;
  width: min(48rem, 96vw);
  height: 100%;
  border-left: 1px solid var(--ui-panel-border);
  background: var(--ui-panel);
  box-shadow: var(--ui-panel-shadow);
}

.mcp-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  border-bottom: 1px solid var(--ui-panel-border);
}

.mcp-drawer-head h2 {
  margin: 0;
  font-size: var(--font-ui-body);
}

.mcp-drawer-head p {
  margin: var(--spacing-2) 0 0;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-steps {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  margin: 0;
  padding: var(--spacing-3) var(--spacing-4);
  overflow-x: auto;
  border-bottom: 1px solid var(--ui-panel-border);
  list-style: none;
  font-size: var(--font-ui-note);
}

.mcp-steps li {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  white-space: nowrap;
  color: var(--color-text-subtle);
}

.mcp-steps li[data-state="active"] {
  color: var(--color-text);
  font-weight: 600;
}

.mcp-steps li[data-state="done"] {
  color: var(--color-success);
}

.mcp-step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  border: 1px solid currentcolor;
  border-radius: 50%;
  font-size: var(--font-ui-note);
}

.mcp-drawer-body {
  flex: 1;
  min-height: 0;
  padding: var(--spacing-4);
  overflow: auto;
}

.mcp-drawer-foot {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  padding: var(--spacing-3) var(--spacing-4);
  border-top: 1px solid var(--ui-panel-border);
}

.mcp-spacer {
  flex: 1;
}

.mcp-note {
  margin: 0;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-alert {
  margin: 0 0 var(--spacing-3);
  padding: var(--spacing-2) var(--spacing-3);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  color: var(--color-danger);
  font-size: var(--font-ui-note);
}

.mcp-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-3);
}

.mcp-field {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
  font-size: var(--font-ui-note);
}

.mcp-field-full {
  grid-column: 1 / -1;
}

.mcp-field small {
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-native-input {
  padding: var(--spacing-2);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--ui-panel-muted);
  font: inherit;
}

.mcp-status {
  display: flex;
  gap: var(--spacing-2);
  align-items: flex-start;
  margin-top: var(--spacing-3);
  padding: var(--spacing-3);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  font-size: var(--font-ui-note);
}

.mcp-status b,
.mcp-status small {
  display: block;
}

.mcp-status small {
  margin-top: var(--spacing-2);
  color: var(--ui-panel-muted);
}

.mcp-status-ok {
  border-color: var(--color-success);
}

.mcp-status-error {
  border-color: var(--color-danger);
}

.mcp-spinner {
  width: 0.75rem;
  height: 0.75rem;
  margin-top: var(--spacing-2);
  border: 1px solid var(--color-border-strong);
  border-top-color: var(--color-text);
  border-radius: 50%;
  animation: mcp-spin 0.8s linear infinite;
}

@keyframes mcp-spin {
  to {
    transform: rotate(360deg);
  }
}

.mcp-tools-head {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  margin-bottom: var(--spacing-2);
}

.mcp-badge {
  display: inline-flex;
  padding: 0 var(--spacing-2);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-badge-ok {
  border-color: var(--color-success);
  color: var(--color-success);
}

.mcp-badge-warn {
  border-color: var(--color-danger);
  color: var(--color-danger);
}

.mcp-tool-filter {
  flex: 1;
  min-width: 10rem;
}

.mcp-tool-filter input,
.mcp-check input {
  accent-color: var(--color-text);
}

.mcp-tool-filter input {
  width: 100%;
  padding: var(--spacing-2);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text);
}

.mcp-check {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  font-size: var(--font-ui-note);
}

.mcp-empty {
  padding: var(--spacing-4);
  color: var(--ui-panel-muted);
  text-align: center;
}

.mcp-tool-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.mcp-tool {
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
}

.mcp-tool[data-selected="true"] {
  border-color: var(--color-text);
  background: var(--color-selected);
}

.mcp-tool-main {
  display: flex;
  gap: var(--spacing-2);
  align-items: flex-start;
  padding: var(--spacing-3);
  cursor: pointer;
}

.mcp-tool-main > input {
  margin-top: var(--spacing-2);
}

.mcp-tool-content {
  flex: 1;
  min-width: 0;
}

.mcp-tool-title {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  flex-wrap: wrap;
}

.mcp-tool-title code,
.mcp-tool-meta {
  font-family: var(--font-ui-code);
  font-size: var(--font-ui-note);
}

.mcp-tool-desc {
  margin: var(--spacing-2) 0 0;
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-tool-meta {
  margin-top: var(--spacing-2);
  color: var(--color-text-subtle);
}

.mcp-schema {
  margin: 0;
  padding: var(--spacing-3);
  overflow: auto;
  border-top: 1px solid var(--ui-panel-border);
  background: var(--color-surface-2);
  font-size: var(--font-ui-note);
}

.mcp-selected-bar {
  display: flex;
  gap: var(--spacing-2);
  margin-top: var(--spacing-3);
  padding: var(--spacing-2) var(--spacing-3);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-ability-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(14rem, 20rem);
  gap: var(--spacing-4);
}

.mcp-ability-layout .mcp-form-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.mcp-section-title {
  margin: 0 0 var(--spacing-2);
  font-size: var(--font-ui-note);
}

.mcp-summary {
  padding: var(--spacing-3);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  font-size: var(--font-ui-note);
}

.mcp-summary h3 {
  margin: 0 0 var(--spacing-2);
  font-size: var(--font-ui-note);
}

.mcp-summary dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--spacing-2);
  margin: 0;
}

.mcp-summary dt {
  color: var(--ui-panel-muted);
}

.mcp-summary dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.mcp-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-2);
  margin-top: var(--spacing-2);
}

.mcp-chip {
  padding: 0 var(--spacing-2);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--font-ui-note);
  overflow-wrap: anywhere;
}

.mcp-preview-head {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  margin: var(--spacing-4) 0 var(--spacing-2);
}

.mcp-preview-head button {
  padding: var(--spacing-2);
  border: 0;
  border-bottom: 1px solid transparent;
  background: transparent;
  color: var(--ui-panel-muted);
  cursor: pointer;
  font: inherit;
}

.mcp-preview-head button[aria-selected="true"] {
  border-bottom-color: var(--color-text);
  color: var(--color-text);
}

.mcp-preview {
  max-height: 20rem;
  margin: 0;
  padding: var(--spacing-3);
  overflow: auto;
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  font-size: var(--font-ui-note);
}

.mcp-install-stages {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.mcp-install-stages li {
  display: flex;
  gap: var(--spacing-2);
  align-items: center;
  padding: var(--spacing-2);
  border: 1px solid var(--ui-panel-border);
  border-radius: var(--radius-md);
}

.mcp-install-stages li[data-state="done"] {
  border-color: var(--color-success);
}

.mcp-install-stages li[data-state="failed"] {
  border-color: var(--color-danger);
}

.mcp-stage-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  border: 1px solid currentcolor;
  border-radius: 50%;
}

.mcp-install-stages b,
.mcp-install-stages small {
  display: block;
}

.mcp-install-stages small {
  margin-top: var(--spacing-2);
  color: var(--ui-panel-muted);
  font-size: var(--font-ui-note);
}

.mcp-success {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--spacing-2);
}

.mcp-success-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  border: 1px solid var(--color-success);
  border-radius: 50%;
  color: var(--color-success);
  font-size: var(--font-ui-body);
}

.mcp-success h3 {
  margin: 0;
  font-size: var(--font-ui-body);
}

.mcp-success-facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--spacing-2);
  margin: var(--spacing-2) 0 0;
  font-size: var(--font-ui-note);
}

.mcp-success-facts dt {
  color: var(--ui-panel-muted);
}

.mcp-success-facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}

@media (max-width: 46rem) {
  .mcp-drawer {
    width: 100%;
  }

  .mcp-ability-layout {
    grid-template-columns: 1fr;
  }

  .mcp-form-grid,
  .mcp-ability-layout .mcp-form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
