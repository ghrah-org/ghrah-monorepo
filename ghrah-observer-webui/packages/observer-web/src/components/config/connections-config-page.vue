<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { HttpClient, HttpError, useConnectionStore } from "@ghrah/observer-core";
import {
  AgentconfCapabilityListViewSchema,
  AgentconfEndpointListViewSchema,
  AgentconfEndpointResolveViewSchema,
  AgentconfEndpointViewSchema,
  AgentconfMcpProviderListViewSchema,
  AgentconfMcpProviderReferencesViewSchema,
  AgentconfMcpProviderViewSchema,
  AgentconfMcpServerListViewSchema,
  AgentconfMcpServerResolveViewSchema,
  AgentconfMcpServerViewSchema,
  AgentconfProviderListViewSchema,
  AgentconfProviderReferencesViewSchema,
  AgentconfProviderViewSchema,
  AgentconfStatusViewSchema,
  type AgentconfCapabilitySummary,
  type AgentconfEndpointView,
  type AgentconfMcpProviderView,
  type AgentconfMcpServerView,
  type AgentconfProviderView,
} from "@ghrah/protocol";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import UiButton from "@/components/ui/button.vue";
import UiSelect from "@/components/ui/select.vue";
import UiTextField from "@/components/ui/text-field.vue";

type Tab = "providers" | "endpoints" | "mcpProviders" | "mcpServers";
type Phase = "loading" | "ready" | "migration" | "unavailable" | "failed";
type Named = { id: string; name: string };
type Row = { id: string; name: string; detail: string };
type Saved =
  AgentconfProviderView | AgentconfEndpointView | AgentconfMcpProviderView | AgentconfMcpServerView;

const PATH = {
  providers: "/api/v1/agentconf/providers",
  endpoints: "/api/v1/agentconf/endpoints",
  mcpProviders: "/api/v1/agentconf/mcp-providers",
  mcpServers: "/api/v1/agentconf/mcp-servers",
} as const;
const MODEL_TYPES = ["llm", "embedding", "rerank"] as const;
const tabs: Tab[] = ["providers", "endpoints", "mcpProviders", "mcpServers"];

class FormError extends Error {
  constructor(readonly kind: "json" | "args" | "timeout") {
    super(kind);
  }
}

const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();
const { t } = useI18n();
const tab = ref<Tab>("providers");
const phase = ref<Phase>("loading");
const busy = ref(false);
const dirty = ref(false);
const creating = ref(false);
const resourceId = ref("");
const revision = ref(1);
const keyConfigured = ref(false);
const referenceConfigured = ref(false);
const failure = ref("");
const notice = ref("");
const preview = ref("");
const references = ref<string[]>([]);
const suggestions = ref<AgentconfCapabilitySummary[]>([]);
const rows = ref<Row[]>([]);
const providerChoices = ref<Named[]>([]);
const mcpChoices = ref<Named[]>([]);
const modelFilter = ref("");
const transportFilter = ref("");
const dialog = ref<null | "delete" | "discard">(null);
const pendingTab = ref<Tab | null>(null);
const capabilitySearch = ref<"idle" | "done">("idle");
const form = reactive({
  name: "",
  providerType: "openai",
  baseUrl: "",
  apiKey: "",
  clearKey: false,
  optionsText: "{}",
  providerName: "",
  modelName: "",
  modelType: "llm",
  apiFormat: "openai.responses",
  capabilitiesKey: "",
  vendor: "",
  useBinding: false,
  bindingTarget: "http_header" as "http_header" | "stdio_env",
  bindingName: "Authorization",
  bindingTemplate: "Bearer {{auth}}",
  transportChoice: "streamable_http" as "stdio" | "streamable_http" | "custom",
  customTransport: "",
  command: "",
  argsText: "[]",
  cwd: "",
  url: "",
  headersText: "{}",
  envText: "{}",
  timeout: "",
  protocolHint: "",
  authMode: "none" as "none" | "inline" | "provider" | "external",
  mcpProviderId: "",
  bindingMode: "inherit" as "inherit" | "override",
  reference: "",
  clearReference: false,
});
const baseline = ref(JSON.stringify(form));
const modelTypeOptions = computed(() => {
  const values = new Set<string>(MODEL_TYPES);
  if (form.modelType) values.add(form.modelType);
  return [...values];
});
const endpointProviders = computed(() => {
  const names = providerChoices.value.map((item) => item.name);
  if (form.providerName && !names.includes(form.providerName)) names.unshift(form.providerName);
  return names;
});
const mcpProviderOptions = computed(() => {
  const options = [...mcpChoices.value];
  if (form.mcpProviderId && !options.some((item) => item.id === form.mcpProviderId)) {
    options.unshift({ id: form.mcpProviderId, name: form.mcpProviderId });
  }
  return options;
});
const showBinding = computed(
  () =>
    (tab.value === "mcpProviders" && form.useBinding) ||
    (tab.value === "mcpServers" &&
      (form.authMode === "external" ||
        (form.authMode === "provider" && form.bindingMode === "override"))),
);
const showReferences = computed(
  () =>
    !creating.value &&
    !!resourceId.value &&
    (tab.value === "providers" || tab.value === "mcpProviders"),
);

watch(
  () => JSON.stringify(form),
  (value) => {
    dirty.value = value !== baseline.value;
  },
);
watch(dirty, (value) => emit("dirtyChange", value));

function client() {
  const connection = useConnectionStore();
  return new HttpClient({ baseUrl: connection.httpBaseUrl, token: connection.apiToken });
}

function capture() {
  baseline.value = JSON.stringify(form);
  dirty.value = false;
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function parseObject(text: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text.trim() || "{}");
  } catch {
    throw new FormError("json");
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new FormError("json");
  }
  return value as Record<string, unknown>;
}

function parseArgs(text: string): string[] {
  let value: unknown;
  try {
    value = JSON.parse(text.trim() || "[]");
  } catch {
    throw new FormError("args");
  }
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new FormError("args");
  }
  return value;
}

function timeoutOrNull(): number | null {
  const text = form.timeout.trim();
  if (!text) return null;
  const value = Number(text);
  if (!Number.isFinite(value)) throw new FormError("timeout");
  return value;
}

function binding() {
  return {
    target: form.bindingTarget,
    name: form.bindingName.trim(),
    value_template: form.bindingTemplate,
  };
}

function explain(error: unknown): string {
  if (error instanceof FormError) {
    if (error.kind === "args") return t("config.connections.invalidArgs");
    if (error.kind === "timeout") return t("config.connections.invalidTimeout");
    return t("config.connections.invalidJson");
  }
  if (error instanceof HttpError) {
    if (error.code === "migration_required") return t("config.connections.migration");
    if (error.code === "capability_unavailable") return t("config.connections.unavailable");
    if (error.code === "conflict") return t("config.connections.conflict");
    if (error.code === "already_exists") return t("config.connections.alreadyExists");
    if (error.code === "resource_in_use") {
      const listed = referenceLines(error.detail);
      if (listed.length) references.value = listed;
      return t("config.connections.inUse");
    }
    return error.message;
  }
  return t("config.connections.loadFailed");
}

function referenceLines(detail: unknown): string[] {
  if (!detail || typeof detail !== "object" || !("references" in detail)) return [];
  const value = detail.references;
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object" && "name" in item) return String(item.name);
    return String(item);
  });
}

function blank() {
  form.name = "";
  form.providerType = "openai";
  form.baseUrl = "";
  form.apiKey = "";
  form.clearKey = false;
  form.optionsText = "{}";
  form.providerName = "";
  form.modelName = "";
  form.modelType = "llm";
  form.apiFormat = "openai.responses";
  form.capabilitiesKey = "";
  form.vendor = "";
  form.useBinding = false;
  form.bindingTarget = "http_header";
  form.bindingName = "Authorization";
  form.bindingTemplate = "Bearer {{auth}}";
  form.transportChoice = "streamable_http";
  form.customTransport = "";
  form.command = "";
  form.argsText = "[]";
  form.cwd = "";
  form.url = "";
  form.headersText = "{}";
  form.envText = "{}";
  form.timeout = "";
  form.protocolHint = "";
  form.authMode = "none";
  form.mcpProviderId = "";
  form.bindingMode = "inherit";
  form.reference = "";
  form.clearReference = false;
}

function assignBinding(
  value: { target: "http_header" | "stdio_env"; name: string; value_template: string } | null,
) {
  if (!value) {
    form.useBinding = false;
    return;
  }
  form.useBinding = true;
  form.bindingTarget = value.target;
  form.bindingName = value.name;
  form.bindingTemplate = value.value_template;
}

function fillProvider(view: AgentconfProviderView) {
  blank();
  resourceId.value = view.id;
  revision.value = view.revision;
  keyConfigured.value = view.api_key_configured;
  form.name = view.name;
  form.providerType = view.provider_type;
  form.baseUrl = view.base_url;
  form.optionsText = pretty(view.connection_options);
}

function fillEndpoint(view: AgentconfEndpointView) {
  blank();
  resourceId.value = view.id;
  revision.value = view.revision;
  form.name = view.name;
  form.providerName = view.provider_name;
  form.modelName = view.model_name;
  form.modelType = view.model_type;
  form.apiFormat = view.api_format;
  form.capabilitiesKey = view.capabilities_key ?? "";
}

function fillMcpProvider(view: AgentconfMcpProviderView) {
  blank();
  resourceId.value = view.id;
  revision.value = view.revision;
  keyConfigured.value = view.api_key_configured;
  form.name = view.name;
  form.vendor = view.vendor ?? "";
  assignBinding(view.default_binding);
}

function fillServer(view: AgentconfMcpServerView) {
  blank();
  resourceId.value = view.id;
  revision.value = view.revision;
  referenceConfigured.value = view.reference_configured;
  form.name = view.name;
  if (view.transport === "stdio" || view.transport === "streamable_http") {
    form.transportChoice = view.transport;
  } else {
    form.transportChoice = "custom";
    form.customTransport = view.transport;
  }
  form.command = view.command ?? "";
  form.argsText = JSON.stringify(view.args, null, 2);
  form.envText = pretty(view.env);
  form.cwd = view.cwd ?? "";
  form.url = view.url ?? "";
  form.headersText = pretty(view.headers);
  form.protocolHint = view.protocol_version_hint ?? "";
  form.optionsText = pretty(view.options);
  form.timeout = view.connect_timeout === null ? "" : String(view.connect_timeout);
  form.authMode = view.auth_mode;
  form.mcpProviderId = view.provider_id ?? "";
  form.bindingMode = view.binding_mode ?? "inherit";
  assignBinding(view.binding);
  if (view.auth_mode !== "none" && view.binding) form.useBinding = true;
}

function applySaved(view: Saved) {
  creating.value = false;
  if (tab.value === "providers") fillProvider(view as AgentconfProviderView);
  else if (tab.value === "endpoints") fillEndpoint(view as AgentconfEndpointView);
  else if (tab.value === "mcpProviders") fillMcpProvider(view as AgentconfMcpProviderView);
  else fillServer(view as AgentconfMcpServerView);
  capture();
}

function secretField(body: Record<string, unknown>) {
  if (form.clearKey) body.api_key = null;
  else if (form.apiKey) body.api_key = form.apiKey;
}

function providerBody(update: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    provider_type: form.providerType.trim(),
    base_url: form.baseUrl.trim(),
    connection_options: parseObject(form.optionsText),
  };
  if (update) body.expected_revision = revision.value;
  secretField(body);
  return body;
}

function endpointBody(update: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    provider_name: form.providerName,
    model_name: form.modelName.trim(),
    model_type: form.modelType,
    api_format: form.apiFormat.trim(),
    capabilities_key: form.capabilitiesKey.trim() || null,
  };
  if (update) body.expected_revision = revision.value;
  return body;
}

function mcpProviderBody(update: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    vendor: form.vendor.trim() || null,
    default_binding: form.useBinding ? binding() : null,
  };
  if (update) body.expected_revision = revision.value;
  secretField(body);
  return body;
}

function serverTransport(): string {
  return form.transportChoice === "custom" ? form.customTransport.trim() : form.transportChoice;
}

function serverBody(update: boolean): Record<string, unknown> {
  const transport = serverTransport();
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    transport,
    auth_mode: form.authMode,
  };
  if (update) body.expected_revision = revision.value;
  if (transport === "stdio") {
    body.command = form.command;
    body.args = parseArgs(form.argsText);
    body.env = parseObject(form.envText);
    body.cwd = form.cwd.trim() || null;
    body.connect_timeout = timeoutOrNull();
  } else if (transport === "streamable_http") {
    body.url = form.url.trim();
    body.headers = parseObject(form.headersText);
    body.connect_timeout = timeoutOrNull();
    body.protocol_version_hint = form.protocolHint.trim() || null;
  } else {
    body.options = parseObject(form.optionsText);
  }
  if (form.authMode === "provider") {
    body.provider_id = form.mcpProviderId || null;
    body.binding_mode = form.bindingMode;
    if (form.bindingMode === "override") body.binding = binding();
  } else if (form.authMode === "external") {
    body.binding = binding();
    if (!update || form.clearReference) body.reference = form.reference.trim() || null;
    else if (form.reference.trim()) body.reference = form.reference.trim();
  }
  return body;
}

async function refreshRows() {
  const http = client();
  if (tab.value === "providers") {
    const page = AgentconfProviderListViewSchema.parse(await http.get(PATH.providers));
    providerChoices.value = page.providers.map((item) => ({ id: item.id, name: item.name }));
    rows.value = page.providers.map((item) => ({
      id: item.id,
      name: item.name,
      detail: item.provider_type,
    }));
    return;
  }
  if (tab.value === "endpoints") {
    const providers = AgentconfProviderListViewSchema.parse(await http.get(PATH.providers));
    providerChoices.value = providers.providers.map((item) => ({ id: item.id, name: item.name }));
    const page = AgentconfEndpointListViewSchema.parse(
      await http.get(PATH.endpoints, {
        query: modelFilter.value ? { model_type: modelFilter.value } : undefined,
      }),
    );
    rows.value = page.endpoints.map((item) => ({
      id: item.id,
      name: item.name,
      detail: `${item.model_type} · ${item.provider_name}`,
    }));
    return;
  }
  if (tab.value === "mcpProviders") {
    const page = AgentconfMcpProviderListViewSchema.parse(await http.get(PATH.mcpProviders));
    mcpChoices.value = page.providers.map((item) => ({ id: item.id, name: item.name }));
    rows.value = page.providers.map((item) => ({
      id: item.id,
      name: item.name,
      detail: item.vendor ?? "",
    }));
    return;
  }
  const providers = AgentconfMcpProviderListViewSchema.parse(await http.get(PATH.mcpProviders));
  mcpChoices.value = providers.providers.map((item) => ({ id: item.id, name: item.name }));
  const page = AgentconfMcpServerListViewSchema.parse(
    await http.get(PATH.mcpServers, {
      query: transportFilter.value ? { transport: transportFilter.value } : undefined,
    }),
  );
  rows.value = page.servers.map((item) => ({
    id: item.id,
    name: item.name,
    detail: item.transport,
  }));
}

async function loadTab() {
  failure.value = "";
  notice.value = "";
  preview.value = "";
  references.value = [];
  suggestions.value = [];
  capabilitySearch.value = "idle";
  creating.value = false;
  resourceId.value = "";
  keyConfigured.value = false;
  referenceConfigured.value = false;
  blank();
  capture();
  try {
    await refreshRows();
  } catch (error) {
    rows.value = [];
    failure.value = explain(error);
  }
}

async function load() {
  phase.value = "loading";
  failure.value = "";
  try {
    AgentconfStatusViewSchema.parse(await client().get("/api/v1/agentconf/status"));
    phase.value = "ready";
    await loadTab();
  } catch (error) {
    if (error instanceof HttpError && error.code === "migration_required") {
      phase.value = "migration";
      return;
    }
    if (error instanceof HttpError && error.code === "capability_unavailable") {
      phase.value = "unavailable";
      return;
    }
    phase.value = "failed";
    failure.value = explain(error);
  }
}

async function loadReferences(id: string) {
  references.value = [];
  if (tab.value === "providers") {
    const page = AgentconfProviderReferencesViewSchema.parse(
      await client().get(`${PATH.providers}/${encodeURIComponent(id)}/references`),
    );
    references.value = page.names;
    return;
  }
  if (tab.value === "mcpProviders") {
    const page = AgentconfMcpProviderReferencesViewSchema.parse(
      await client().get(`${PATH.mcpProviders}/${encodeURIComponent(id)}/references`),
    );
    references.value = page.servers.map((item) => `${item.name} (${item.transport})`);
  }
}

async function openRow(id: string) {
  failure.value = "";
  notice.value = "";
  preview.value = "";
  suggestions.value = [];
  capabilitySearch.value = "idle";
  try {
    creating.value = false;
    const encoded = encodeURIComponent(id);
    if (tab.value === "providers") {
      fillProvider(
        AgentconfProviderViewSchema.parse(await client().get(`${PATH.providers}/${encoded}`)),
      );
    } else if (tab.value === "endpoints") {
      fillEndpoint(
        AgentconfEndpointViewSchema.parse(await client().get(`${PATH.endpoints}/${encoded}`)),
      );
    } else if (tab.value === "mcpProviders") {
      fillMcpProvider(
        AgentconfMcpProviderViewSchema.parse(await client().get(`${PATH.mcpProviders}/${encoded}`)),
      );
    } else {
      fillServer(
        AgentconfMcpServerViewSchema.parse(await client().get(`${PATH.mcpServers}/${encoded}`)),
      );
    }
    capture();
    await loadReferences(id);
  } catch (error) {
    failure.value = explain(error);
  }
}

function startCreate() {
  failure.value = "";
  notice.value = "";
  preview.value = "";
  references.value = [];
  suggestions.value = [];
  capabilitySearch.value = "idle";
  creating.value = true;
  resourceId.value = "";
  revision.value = 1;
  keyConfigured.value = false;
  referenceConfigured.value = false;
  blank();
  form.providerName = providerChoices.value[0]?.name ?? "";
  form.mcpProviderId = mcpChoices.value[0]?.id ?? "";
  capture();
}

async function createCurrent(): Promise<Saved> {
  const http = client();
  if (tab.value === "providers") {
    return AgentconfProviderViewSchema.parse(await http.post(PATH.providers, providerBody(false)));
  }
  if (tab.value === "endpoints") {
    return AgentconfEndpointViewSchema.parse(await http.post(PATH.endpoints, endpointBody(false)));
  }
  if (tab.value === "mcpProviders") {
    return AgentconfMcpProviderViewSchema.parse(
      await http.post(PATH.mcpProviders, mcpProviderBody(false)),
    );
  }
  return AgentconfMcpServerViewSchema.parse(await http.post(PATH.mcpServers, serverBody(false)));
}

async function patchCurrent(): Promise<Saved> {
  const http = client();
  const encoded = encodeURIComponent(resourceId.value);
  if (tab.value === "providers") {
    return AgentconfProviderViewSchema.parse(
      await http.patch(`${PATH.providers}/${encoded}`, providerBody(true)),
    );
  }
  if (tab.value === "endpoints") {
    return AgentconfEndpointViewSchema.parse(
      await http.patch(`${PATH.endpoints}/${encoded}`, endpointBody(true)),
    );
  }
  if (tab.value === "mcpProviders") {
    return AgentconfMcpProviderViewSchema.parse(
      await http.patch(`${PATH.mcpProviders}/${encoded}`, mcpProviderBody(true)),
    );
  }
  return AgentconfMcpServerViewSchema.parse(
    await http.patch(`${PATH.mcpServers}/${encoded}`, serverBody(true)),
  );
}

async function save() {
  failure.value = "";
  notice.value = "";
  busy.value = true;
  try {
    const view = creating.value ? await createCurrent() : await patchCurrent();
    applySaved(view);
    notice.value = t("config.connections.saved");
    try {
      await refreshRows();
      if (tab.value === "providers" || tab.value === "mcpProviders") await loadReferences(view.id);
    } catch (error) {
      rows.value = [];
      references.value = [];
      failure.value = explain(error);
    }
  } catch (error) {
    failure.value = explain(error);
  } finally {
    busy.value = false;
  }
}

async function searchCapabilities() {
  failure.value = "";
  try {
    const page = AgentconfCapabilityListViewSchema.parse(
      await client().get("/api/v1/agentconf/capabilities", {
        query: {
          model_type: form.modelType || undefined,
          q: form.capabilitiesKey || undefined,
        },
      }),
    );
    suggestions.value = page.capabilities;
    capabilitySearch.value = "done";
  } catch (error) {
    failure.value = explain(error);
  }
}

async function resolvePreview() {
  failure.value = "";
  try {
    if (tab.value === "endpoints") {
      const view = AgentconfEndpointResolveViewSchema.parse(
        await client().post(
          `${PATH.endpoints}/${encodeURIComponent(resourceId.value)}/resolve`,
          undefined,
        ),
      );
      preview.value = JSON.stringify(
        {
          model_name: view.model_name,
          model_type: view.model_type,
          api_format: view.api_format,
          sdk_hint: view.sdk_hint,
          api_key_configured: view.api_key_configured,
          capabilities: view.capabilities,
        },
        null,
        2,
      );
      return;
    }
    const view = AgentconfMcpServerResolveViewSchema.parse(
      await client().post(
        `${PATH.mcpServers}/${encodeURIComponent(resourceId.value)}/resolve`,
        undefined,
      ),
    );
    preview.value = JSON.stringify(
      {
        transport: view.transport,
        key_configured: view.key_configured,
        provider_name: view.provider_name,
        binding_mode: view.binding_mode,
        reference_configured: view.reference_configured,
      },
      null,
      2,
    );
  } catch (error) {
    failure.value = explain(error);
  }
}

function requestDelete() {
  if (!resourceId.value || creating.value) return;
  if ((tab.value === "providers" || tab.value === "mcpProviders") && references.value.length) {
    failure.value = t("config.connections.inUse");
    return;
  }
  dialog.value = "delete";
}

async function performDelete() {
  dialog.value = null;
  busy.value = true;
  try {
    const encoded = encodeURIComponent(resourceId.value);
    const path =
      tab.value === "providers"
        ? PATH.providers
        : tab.value === "endpoints"
          ? PATH.endpoints
          : tab.value === "mcpProviders"
            ? PATH.mcpProviders
            : PATH.mcpServers;
    await client().delete(`${path}/${encoded}`, { body: { expected_revision: revision.value } });
    notice.value = t("config.connections.deleted");
    await loadTab();
  } catch (error) {
    failure.value = explain(error);
  } finally {
    busy.value = false;
  }
}

function setModelFilter(value: string) {
  modelFilter.value = value;
  void loadTab();
}

function setTransportFilter(value: string) {
  transportFilter.value = value;
  void loadTab();
}

function chooseTab(next: Tab) {
  if (next === tab.value) return;
  if (dirty.value) {
    pendingTab.value = next;
    dialog.value = "discard";
    return;
  }
  void switchTab(next);
}

async function switchTab(next: Tab) {
  tab.value = next;
  modelFilter.value = "";
  transportFilter.value = "";
  await loadTab();
}

function confirmDialog() {
  if (dialog.value === "delete") {
    void performDelete();
    return;
  }
  const next = pendingTab.value;
  pendingTab.value = null;
  dialog.value = null;
  if (next) {
    dirty.value = false;
    void switchTab(next);
  }
}

function cancelDialog() {
  pendingTab.value = null;
  dialog.value = null;
}

onMounted(() => void load());
</script>

<template>
  <section class="connections">
    <p class="connections-note">{{ t("config.connections.scope") }}</p>
    <p class="connections-note">{{ t("config.connections.restart") }}</p>
    <p class="connections-note">{{ t("config.connections.secretHint") }}</p>
    <p v-if="phase === 'migration'" role="status">{{ t("config.connections.migration") }}</p>
    <p v-else-if="phase === 'unavailable'" role="status">
      {{ t("config.connections.unavailable") }}
    </p>
    <p v-else-if="phase === 'failed'" role="alert">{{ failure }}</p>
    <div v-else-if="phase === 'ready'" class="connections-layout">
      <div class="connections-list">
        <div class="connections-tabs" role="tablist" :aria-label="t('config.nav.connections')">
          <button
            v-for="item in tabs"
            :key="item"
            type="button"
            role="tab"
            :aria-selected="tab === item"
            @click="chooseTab(item)"
          >
            {{ t(`config.connections.tabs.${item}`) }}
          </button>
        </div>
        <label v-if="tab === 'endpoints'" class="connections-field">
          <span>{{ t("config.connections.modelType") }}</span>
          <UiSelect :model-value="modelFilter" @update:model-value="setModelFilter">
            <option value="">{{ t("config.connections.all") }}</option>
            <option v-for="item in MODEL_TYPES" :key="item" :value="item">{{ item }}</option>
          </UiSelect>
        </label>
        <label v-else-if="tab === 'mcpServers'" class="connections-field">
          <span>{{ t("config.connections.transport") }}</span>
          <UiSelect :model-value="transportFilter" @update:model-value="setTransportFilter">
            <option value="">{{ t("config.connections.all") }}</option>
            <option value="stdio">{{ t("config.connections.transports.stdio") }}</option>
            <option value="streamable_http">
              {{ t("config.connections.transports.streamable_http") }}
            </option>
          </UiSelect>
        </label>
        <p v-if="failure && !creating && !resourceId" role="alert">{{ failure }}</p>
        <p v-if="!rows.length">{{ t("config.connections.empty") }}</p>
        <ul data-testid="connection-list">
          <li v-for="row in rows" :key="row.id">
            <button
              type="button"
              :aria-current="row.id === resourceId && !creating ? 'true' : undefined"
              @click="openRow(row.id)"
            >
              <strong>{{ row.name }}</strong>
              <span>{{ row.detail }}</span>
            </button>
          </li>
        </ul>
        <div class="connections-actions">
          <UiButton type="button" variant="secondary" @click="startCreate">
            {{ t("config.connections.create") }}
          </UiButton>
          <UiButton type="button" variant="ghost" @click="load">
            {{ t("config.connections.refresh") }}
          </UiButton>
        </div>
      </div>
      <form v-if="creating || resourceId" class="connections-form" @submit.prevent="save">
        <label class="connections-field">
          <span>{{ t("config.connections.name") }}</span>
          <UiTextField v-model="form.name" required data-testid="connection-name" />
        </label>

        <template v-if="tab === 'providers'">
          <label class="connections-field">
            <span>{{ t("config.connections.providerType") }}</span>
            <UiTextField v-model="form.providerType" required />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.baseUrl") }}</span>
            <UiTextField v-model="form.baseUrl" required />
            <small>{{ t("config.connections.urlHint") }}</small>
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.apiKey") }}</span>
            <UiTextField v-model="form.apiKey" type="password" autocomplete="new-password" />
            <small v-if="keyConfigured">{{ t("config.connections.apiKeyConfigured") }}</small>
            <small v-else-if="!creating">{{ t("config.connections.apiKeyMissing") }}</small>
          </label>
          <label v-if="keyConfigured" class="connections-check">
            <input v-model="form.clearKey" type="checkbox" />
            {{ t("config.connections.clearSecret") }}
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.options") }}</span>
            <UiTextField v-model="form.optionsText" multiline rows="6" />
          </label>
        </template>

        <template v-else-if="tab === 'endpoints'">
          <label class="connections-field">
            <span>{{ t("config.connections.provider") }}</span>
            <UiSelect v-model="form.providerName" required>
              <option v-for="name in endpointProviders" :key="name" :value="name">
                {{ name }}
              </option>
            </UiSelect>
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.modelName") }}</span>
            <UiTextField v-model="form.modelName" required />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.modelType") }}</span>
            <UiSelect v-model="form.modelType">
              <option v-for="item in modelTypeOptions" :key="item" :value="item">{{ item }}</option>
            </UiSelect>
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.apiFormat") }}</span>
            <UiTextField v-model="form.apiFormat" required />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.capabilities") }}</span>
            <UiTextField v-model="form.capabilitiesKey" />
          </label>
          <UiButton type="button" variant="secondary" @click="searchCapabilities">
            {{ t("config.connections.capabilities") }}
          </UiButton>
          <p
            v-if="capabilitySearch === 'done' && suggestions.length === 0"
            class="connections-note"
          >
            {{ t("config.connections.capabilitiesEmpty") }}
          </p>
          <ul v-else>
            <li v-for="item in suggestions" :key="item.model_key">
              <button type="button" @click="form.capabilitiesKey = item.model_key">
                {{ item.display_name }} ({{ item.model_key }})
              </button>
            </li>
          </ul>
        </template>

        <template v-else-if="tab === 'mcpProviders'">
          <label class="connections-field">
            <span>{{ t("config.connections.vendor") }}</span>
            <UiTextField v-model="form.vendor" />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.apiKey") }}</span>
            <UiTextField v-model="form.apiKey" type="password" autocomplete="new-password" />
            <small v-if="keyConfigured">{{ t("config.connections.apiKeyConfigured") }}</small>
          </label>
          <label v-if="keyConfigured" class="connections-check">
            <input v-model="form.clearKey" type="checkbox" />
            {{ t("config.connections.clearSecret") }}
          </label>
          <label class="connections-check">
            <input v-model="form.useBinding" type="checkbox" />
            {{ t("config.connections.binding") }}
          </label>
        </template>

        <template v-else>
          <label class="connections-field">
            <span>{{ t("config.connections.transport") }}</span>
            <UiSelect v-model="form.transportChoice">
              <option value="stdio">{{ t("config.connections.transports.stdio") }}</option>
              <option value="streamable_http">
                {{ t("config.connections.transports.streamable_http") }}
              </option>
              <option value="custom">{{ t("config.connections.transports.custom") }}</option>
            </UiSelect>
          </label>
          <label v-if="form.transportChoice === 'custom'" class="connections-field">
            <span>{{ t("config.connections.customTransport") }}</span>
            <UiTextField v-model="form.customTransport" required />
          </label>
          <template v-if="form.transportChoice === 'stdio'">
            <label class="connections-field">
              <span>{{ t("config.connections.command") }}</span>
              <UiTextField v-model="form.command" required />
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.args") }}</span>
              <UiTextField v-model="form.argsText" multiline rows="4" />
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.cwd") }}</span>
              <UiTextField v-model="form.cwd" />
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.env") }}</span>
              <UiTextField v-model="form.envText" multiline rows="4" />
            </label>
          </template>
          <template v-else-if="form.transportChoice === 'streamable_http'">
            <label class="connections-field">
              <span>{{ t("config.connections.url") }}</span>
              <UiTextField v-model="form.url" required />
              <small>{{ t("config.connections.urlHint") }}</small>
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.headers") }}</span>
              <UiTextField v-model="form.headersText" multiline rows="4" />
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.protocolHint") }}</span>
              <UiTextField v-model="form.protocolHint" />
            </label>
          </template>
          <label v-else class="connections-field">
            <span>{{ t("config.connections.options") }}</span>
            <UiTextField v-model="form.optionsText" multiline rows="6" />
          </label>
          <label v-if="form.transportChoice !== 'custom'" class="connections-field">
            <span>{{ t("config.connections.timeout") }}</span>
            <UiTextField v-model="form.timeout" />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.authMode") }}</span>
            <UiSelect v-model="form.authMode">
              <option value="none">{{ t("config.connections.authModes.none") }}</option>
              <option value="inline">{{ t("config.connections.authModes.inline") }}</option>
              <option value="provider">{{ t("config.connections.authModes.provider") }}</option>
              <option value="external">{{ t("config.connections.authModes.external") }}</option>
            </UiSelect>
          </label>
          <template v-if="form.authMode === 'provider'">
            <label class="connections-field">
              <span>{{ t("config.connections.mcpProvider") }}</span>
              <UiSelect v-model="form.mcpProviderId">
                <option v-for="item in mcpProviderOptions" :key="item.id" :value="item.id">
                  {{ item.name }}
                </option>
              </UiSelect>
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.bindingMode") }}</span>
              <UiSelect v-model="form.bindingMode">
                <option value="inherit">{{ t("config.connections.bindingModes.inherit") }}</option>
                <option value="override">
                  {{ t("config.connections.bindingModes.override") }}
                </option>
              </UiSelect>
            </label>
          </template>
          <template v-else-if="form.authMode === 'external'">
            <label class="connections-field">
              <span>{{ t("config.connections.reference") }}</span>
              <UiTextField v-model="form.reference" type="password" autocomplete="new-password" />
              <small v-if="referenceConfigured">
                {{ t("config.connections.referenceConfigured") }}
              </small>
            </label>
            <label v-if="referenceConfigured" class="connections-check">
              <input v-model="form.clearReference" type="checkbox" />
              {{ t("config.connections.clearSecret") }}
            </label>
          </template>
        </template>

        <template v-if="showBinding">
          <label class="connections-field">
            <span>{{ t("config.connections.bindingTarget") }}</span>
            <UiSelect v-model="form.bindingTarget">
              <option value="http_header">
                {{ t("config.connections.bindingTargets.http_header") }}
              </option>
              <option value="stdio_env">
                {{ t("config.connections.bindingTargets.stdio_env") }}
              </option>
            </UiSelect>
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.bindingName") }}</span>
            <UiTextField v-model="form.bindingName" />
          </label>
          <label class="connections-field">
            <span>{{ t("config.connections.bindingTemplate") }}</span>
            <UiTextField v-model="form.bindingTemplate" />
          </label>
        </template>

        <p v-if="failure" role="alert">{{ failure }}</p>
        <p v-if="notice" role="status">{{ notice }}</p>
        <div class="connections-actions">
          <UiButton type="submit" variant="primary" :disabled="busy" data-testid="connection-save">
            {{ t("config.connections.save") }}
          </UiButton>
          <UiButton
            v-if="!creating"
            type="button"
            variant="danger"
            :disabled="busy"
            @click="requestDelete"
          >
            {{ t("config.connections.delete") }}
          </UiButton>
          <UiButton
            v-if="!creating && (tab === 'endpoints' || tab === 'mcpServers')"
            type="button"
            variant="secondary"
            :disabled="busy"
            @click="resolvePreview"
          >
            {{ t("config.connections.preview") }}
          </UiButton>
        </div>
        <section v-if="showReferences">
          <h2>{{ t("config.connections.references") }}</h2>
          <p v-if="!references.length">{{ t("config.connections.noReferences") }}</p>
          <ul v-else>
            <li v-for="name in references" :key="name">{{ name }}</li>
          </ul>
        </section>
        <pre v-if="preview" class="connections-preview">{{ preview }}</pre>
      </form>
    </div>
    <ConfirmDialog
      v-if="dialog"
      :title="dialog === 'delete' ? t('config.connections.deleteTitle') : t('config.unsavedTitle')"
      :message="
        dialog === 'delete'
          ? t('config.connections.deleteMessage', { name: form.name })
          : t('config.unsavedConfirm')
      "
      :confirm-label="
        dialog === 'delete' ? t('config.connections.delete') : t('config.discardChanges')
      "
      :danger="dialog === 'delete'"
      @confirm="confirmDialog"
      @cancel="cancelDialog"
    />
  </section>
</template>

<style scoped>
.connections {
  display: grid;
  gap: 0.75rem;
  min-width: 0;
  color: var(--color-text);
  font-size: var(--font-ui-body);
}

.connections-note,
.connections-field small {
  color: var(--color-text-muted);
}

.connections-layout {
  display: grid;
  grid-template-columns: 16rem minmax(0, 1fr);
  gap: 1rem;
  min-width: 0;
}

.connections-tabs,
.connections-list,
.connections-form,
.connections-field,
.connections-actions {
  display: grid;
  gap: 0.5rem;
}

.connections-tabs button,
.connections-list li button {
  display: grid;
  border: 1px solid transparent;
  background: transparent;
  color: var(--color-text);
  text-align: start;
  padding: 0.35rem 0.5rem;
}

.connections-tabs button[aria-selected="true"],
.connections-list li button[aria-current="true"] {
  background: var(--color-selected);
  border-color: var(--color-border);
}

.connections-list ul,
.connections-form ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.connections-check {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}

.connections-preview {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  padding: 0.5rem;
}

.connections [role="alert"] {
  color: var(--color-danger);
}

@media (max-width: 720px) {
  .connections-layout {
    grid-template-columns: 1fr;
  }
}
</style>
