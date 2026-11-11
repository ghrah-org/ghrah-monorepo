<!--
SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>

SPDX-License-Identifier: Apache-2.0
-->

<script setup lang="ts">
import { HttpClient, HttpError, useConnectionStore } from "@ghrah/observer-core";
import {
  AgentconfCapabilityDetailViewSchema,
  AgentconfCapabilityListViewSchema,
  AgentconfCapabilityUpdatePreviewViewSchema,
  AgentconfCapabilityUpdateResultViewSchema,
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
  type AgentconfCapabilityDetailView,
  type AgentconfCapabilitySummary,
  type AgentconfCapabilityUpdatePreviewView,
  type AgentconfEndpointView,
  type AgentconfMcpProviderView,
  type AgentconfMcpServerView,
  type AgentconfProviderView,
} from "@ghrah/protocol";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import BaseModal from "@/components/ui/base-modal.vue";
import ConfirmDialog from "@/components/ui/confirm-dialog.vue";
import UiButton from "@/components/ui/button.vue";
import UiSelect from "@/components/ui/select.vue";
import UiTextField from "@/components/ui/text-field.vue";

type Domain = "models" | "mcp";
type Tab = "providers" | "endpoints" | "capabilities" | "mcpProviders" | "mcpServers";
type Phase = "loading" | "ready" | "migration" | "unavailable" | "failed";
type Named = { id: string; name: string };
type Row = { id: string; cells: string[] };
type Saved =
  AgentconfProviderView | AgentconfEndpointView | AgentconfMcpProviderView | AgentconfMcpServerView;
type ApiFormat = (typeof API_FORMATS)[number];

const PATH = {
  providers: "/api/v1/agentconf/providers",
  endpoints: "/api/v1/agentconf/endpoints",
  mcpProviders: "/api/v1/agentconf/mcp-providers",
  mcpServers: "/api/v1/agentconf/mcp-servers",
  capabilities: "/api/v1/agentconf/capabilities",
} as const;
const MODEL_TYPES = ["llm", "embedding", "rerank"] as const;
const MODEL_TABS: Tab[] = ["providers", "endpoints", "capabilities"];
const MCP_TABS: Tab[] = ["mcpProviders", "mcpServers"];
const API_FORMATS = [
  "openai.chat_completions",
  "openai.responses",
  "openai.embeddings",
  "openai.audio.transcriptions",
  "openai.audio.speech",
  "anthropic.messages",
  "google.generate_content",
  "ollama.chat",
] as const;
const CAPABILITY_FIELDS = [
  "max_context_length",
  "max_output_length",
  "max_input_length",
  "dimensions",
  "top_n",
  "supports_streaming",
  "supports_function_calling",
  "supports_reasoning",
  "supports_vision",
  "supports_json_mode",
  "input_modalities",
  "output_modalities",
  "languages",
  "voices",
  "supported_sizes",
  "extra",
] as const;
const COLUMN_KEYS: Record<Tab, string[]> = {
  providers: ["name", "providerType", "baseUrl", "secret"],
  endpoints: ["name", "modelName", "modelType", "apiFormat", "provider"],
  capabilities: ["modelKey", "displayName", "modelType", "origin"],
  mcpProviders: ["name", "vendor", "secret"],
  mcpServers: ["name", "transport"],
};

class FormError extends Error {
  constructor(readonly kind: "json" | "args" | "timeout") {
    super(kind);
  }
}

const emit = defineEmits<{ dirtyChange: [dirty: boolean] }>();
const { t, te } = useI18n();
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
const capabilityOptions = ref<AgentconfCapabilitySummary[]>([]);
const capabilityDetail = ref<AgentconfCapabilityDetailView | null>(null);
const capabilityDetailFailure = ref("");
const profileKey = ref("");
const profile = ref<AgentconfCapabilityDetailView | null>(null);
const profileFailure = ref("");
let endpointCapabilityToken = 0;
let profileToken = 0;
const capabilityFilter = ref("");
const catalogQuery = ref("");
const updatePreview = ref<AgentconfCapabilityUpdatePreviewView | null>(null);
const rows = ref<Row[]>([]);
const providerChoices = ref<Named[]>([]);
const mcpChoices = ref<Named[]>([]);
const modelFilter = ref("");
const transportFilter = ref("");
const dialog = ref<null | "delete" | "discard" | "update">(null);
const pendingTab = ref<Tab | null>(null);
const pendingClose = ref(false);
const formPanel = ref<HTMLFormElement | null>(null);
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
  apiFormatChoice: "openai.responses" as ApiFormat | "custom",
  apiFormatCustom: "",
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
const domain = computed<Domain>(() =>
  tab.value === "mcpProviders" || tab.value === "mcpServers" ? "mcp" : "models",
);
const subtabs = computed(() => (domain.value === "models" ? MODEL_TABS : MCP_TABS));
const columns = computed(() =>
  COLUMN_KEYS[tab.value].map((key) => t(`config.connections.columns.${key}`)),
);
const showForm = computed(
  () => tab.value !== "capabilities" && (creating.value || resourceId.value !== ""),
);
const formTitle = computed(() =>
  creating.value ? t("config.connections.create") : t(`config.connections.tabs.${tab.value}`),
);
const endpointCapabilityEntries = computed(() =>
  capabilityDetail.value ? capabilityEntries(capabilityDetail.value.capabilities) : [],
);
const profileEntries = computed(() =>
  profile.value ? capabilityEntries(profile.value.capabilities) : [],
);
const capabilityChoices = computed(() => {
  const needle = capabilityFilter.value.trim().toLowerCase();
  const matched = capabilityOptions.value.filter((item) => {
    if (!needle) return true;
    return (
      item.model_key.toLowerCase().includes(needle) ||
      item.display_name.toLowerCase().includes(needle)
    );
  });
  if (form.capabilitiesKey && !matched.some((item) => item.model_key === form.capabilitiesKey)) {
    matched.unshift({
      model_key: form.capabilitiesKey,
      display_name: form.capabilitiesKey,
      model_type: form.modelType,
      origin: "",
    });
  }
  return matched;
});
const dialogTitle = computed(() => {
  if (dialog.value === "delete") return t("config.connections.deleteTitle");
  if (dialog.value === "update") return t("config.connections.updateTitle");
  return t("config.unsavedTitle");
});
const dialogMessage = computed(() => {
  if (dialog.value === "delete") {
    return t("config.connections.deleteMessage", { name: form.name });
  }
  if (dialog.value === "update") return updateMessage();
  return t("config.unsavedConfirm");
});
const dialogConfirm = computed(() => {
  if (dialog.value === "delete") return t("config.connections.delete");
  if (dialog.value === "update") return t("config.connections.updateConfirm");
  return t("config.discardChanges");
});

watch(
  () => JSON.stringify(form),
  (value) => {
    dirty.value = value !== baseline.value;
  },
);
watch(dirty, (value) => emit("dirtyChange", value));
watch(showForm, (open) => {
  if (open) void nextTick(() => formPanel.value?.focus());
});
watch(
  () => form.capabilitiesKey,
  (key) => {
    void loadEndpointCapabilities(key);
  },
);

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
    if (error.code === "not_found") return t("config.connections.notFound");
    if (error.code === "catalog_unavailable") return t("config.connections.catalogUnavailable");
    if (error.code === "catalog_source_changed") return t("config.connections.catalogChanged");
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
  form.apiFormatChoice = "openai.responses";
  form.apiFormatCustom = "";
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
  if ((API_FORMATS as readonly string[]).includes(view.api_format)) {
    form.apiFormatChoice = view.api_format as ApiFormat;
    form.apiFormatCustom = "";
  } else {
    form.apiFormatChoice = "custom";
    form.apiFormatCustom = view.api_format;
  }
  form.capabilitiesKey = view.capabilities_key ?? "";
  void loadCapabilityOptions();
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
    api_format:
      form.apiFormatChoice === "custom" ? form.apiFormatCustom.trim() : form.apiFormatChoice,
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

function secretLabel(configured: boolean): string {
  return configured ? t("config.connections.configured") : t("config.connections.notConfigured");
}

async function refreshRows() {
  const http = client();
  if (tab.value === "providers") {
    const page = AgentconfProviderListViewSchema.parse(await http.get(PATH.providers));
    providerChoices.value = page.providers.map((item) => ({ id: item.id, name: item.name }));
    rows.value = page.providers.map((item) => ({
      id: item.id,
      cells: [item.name, item.provider_type, item.base_url, secretLabel(item.api_key_configured)],
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
      cells: [item.name, item.model_name, item.model_type, item.api_format, item.provider_name],
    }));
    return;
  }
  if (tab.value === "capabilities") {
    const query: Record<string, string> = {};
    if (modelFilter.value) query.model_type = modelFilter.value;
    if (catalogQuery.value.trim()) query.q = catalogQuery.value.trim();
    const page = AgentconfCapabilityListViewSchema.parse(
      await http.get(PATH.capabilities, { query }),
    );
    rows.value = page.capabilities.map((item) => ({
      id: item.model_key,
      cells: [item.model_key, item.display_name, item.model_type, item.origin],
    }));
    return;
  }
  if (tab.value === "mcpProviders") {
    const page = AgentconfMcpProviderListViewSchema.parse(await http.get(PATH.mcpProviders));
    mcpChoices.value = page.providers.map((item) => ({ id: item.id, name: item.name }));
    rows.value = page.providers.map((item) => ({
      id: item.id,
      cells: [item.name, item.vendor ?? "", secretLabel(item.api_key_configured)],
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
    cells: [item.name, item.transport],
  }));
}

function capabilityLabel(name: string): string {
  const key = `config.connections.capabilityFields.${name}`;
  return te(key) ? t(key) : name;
}

function formatCapability(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? t("config.connections.yes") : t("config.connections.no");
  }
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function capabilityEntries(capabilities: Record<string, unknown>) {
  const known = CAPABILITY_FIELDS.filter((name) => capabilities[name] != null);
  const rest = Object.keys(capabilities).filter(
    (name) => !CAPABILITY_FIELDS.includes(name as (typeof CAPABILITY_FIELDS)[number]),
  );
  return [...known, ...rest].map((name) => ({
    name,
    text: formatCapability(capabilities[name]),
  }));
}

async function fetchCapability(key: string): Promise<AgentconfCapabilityDetailView> {
  return AgentconfCapabilityDetailViewSchema.parse(
    await client().get(`${PATH.capabilities}/profile`, { query: { model_key: key } }),
  );
}

async function loadEndpointCapabilities(key: string) {
  const token = ++endpointCapabilityToken;
  capabilityDetail.value = null;
  capabilityDetailFailure.value = "";
  if (!key) return;
  try {
    const view = await fetchCapability(key);
    if (token !== endpointCapabilityToken) return;
    capabilityDetail.value = view;
  } catch (error) {
    if (token !== endpointCapabilityToken) return;
    capabilityDetailFailure.value = explain(error);
  }
}

function closeProfile() {
  profileToken += 1;
  profileKey.value = "";
  profile.value = null;
  profileFailure.value = "";
}

async function openProfile(key: string) {
  const token = ++profileToken;
  profileKey.value = key;
  profile.value = null;
  profileFailure.value = "";
  try {
    const view = await fetchCapability(key);
    if (token !== profileToken) return;
    profile.value = view;
  } catch (error) {
    if (token !== profileToken) return;
    profileFailure.value = explain(error);
  }
}

function rowSelected(id: string): "true" | undefined {
  if (tab.value === "capabilities") return profileKey.value === id ? "true" : undefined;
  if (!creating.value && resourceId.value === id) return "true";
  return undefined;
}

async function loadCapabilityOptions() {
  try {
    const page = AgentconfCapabilityListViewSchema.parse(
      await client().get(PATH.capabilities, { query: { model_type: form.modelType } }),
    );
    capabilityOptions.value = page.capabilities;
  } catch (error) {
    capabilityOptions.value = [];
    failure.value = explain(error);
  }
}

async function loadTab() {
  failure.value = "";
  notice.value = "";
  preview.value = "";
  references.value = [];
  capabilityOptions.value = [];
  capabilityFilter.value = "";
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
  capabilityOptions.value = [];
  capabilityFilter.value = "";
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
  capabilityOptions.value = [];
  capabilityFilter.value = "";
  creating.value = true;
  resourceId.value = "";
  revision.value = 1;
  keyConfigured.value = false;
  referenceConfigured.value = false;
  blank();
  form.providerName = providerChoices.value[0]?.name ?? "";
  form.mcpProviderId = mcpChoices.value[0]?.id ?? "";
  capture();
  if (tab.value === "endpoints") void loadCapabilityOptions();
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

function updateMessage(): string {
  const current = updatePreview.value;
  if (!current) return "";
  const summary = t("config.connections.updateSummary", {
    added: current.added_count,
    updatable: current.updatable_count,
    protected: current.protected_count,
    conflicts: current.conflict_count,
    missing: current.missing_count,
  });
  const reasons = current.conflicts.slice(0, 3).map((item) => `${item.key}: ${item.reason}`);
  const extra =
    current.conflicts.length > 3
      ? t("config.connections.updateMoreConflicts", { count: current.conflicts.length - 3 })
      : "";
  return [summary, ...reasons, extra].filter(Boolean).join(" ");
}

async function requestCatalogUpdate() {
  failure.value = "";
  notice.value = "";
  busy.value = true;
  try {
    updatePreview.value = AgentconfCapabilityUpdatePreviewViewSchema.parse(
      await client().post(`${PATH.capabilities}/updates/preview`, {}),
    );
    dialog.value = "update";
  } catch (error) {
    failure.value = explain(error);
  } finally {
    busy.value = false;
  }
}

async function applyCatalogUpdate() {
  const current = updatePreview.value;
  dialog.value = null;
  if (!current) return;
  busy.value = true;
  try {
    const result = AgentconfCapabilityUpdateResultViewSchema.parse(
      await client().post(`${PATH.capabilities}/updates`, {
        source_sha256: current.source_sha256,
      }),
    );
    notice.value = t("config.connections.updated", {
      added: result.added_count,
      updated: result.updated_count,
    });
    await refreshRows();
  } catch (error) {
    failure.value = explain(error);
  } finally {
    busy.value = false;
    updatePreview.value = null;
  }
}

function setEndpointModelType(value: string) {
  form.modelType = value;
  capabilityFilter.value = "";
  void loadCapabilityOptions();
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

function closeForm() {
  creating.value = false;
  resourceId.value = "";
  preview.value = "";
  failure.value = "";
  notice.value = "";
  references.value = [];
  capture();
}

function requestClose() {
  if (dialog.value) return;
  if (dirty.value) {
    pendingTab.value = null;
    pendingClose.value = true;
    dialog.value = "discard";
    return;
  }
  closeForm();
}

function chooseTab(next: Tab) {
  if (next === tab.value) return;
  if (dirty.value) {
    pendingClose.value = false;
    pendingTab.value = next;
    dialog.value = "discard";
    return;
  }
  void switchTab(next);
}

function chooseDomain(next: Domain) {
  if (domain.value === next) return;
  chooseTab(next === "models" ? "providers" : "mcpProviders");
}

async function switchTab(next: Tab) {
  tab.value = next;
  modelFilter.value = "";
  transportFilter.value = "";
  catalogQuery.value = "";
  await loadTab();
}

function confirmDialog() {
  if (dialog.value === "delete") {
    void performDelete();
    return;
  }
  if (dialog.value === "update") {
    void applyCatalogUpdate();
    return;
  }
  const next = pendingTab.value;
  const closing = pendingClose.value;
  pendingTab.value = null;
  pendingClose.value = false;
  dialog.value = null;
  dirty.value = false;
  if (next) void switchTab(next);
  else if (closing) closeForm();
}

function cancelDialog() {
  pendingTab.value = null;
  pendingClose.value = false;
  updatePreview.value = null;
  dialog.value = null;
}

onMounted(() => void load());
</script>

<template>
  <section class="connections">
    <p class="connections-note">{{ t("config.connections.scope") }}</p>
    <p v-if="phase === 'migration'" role="status">{{ t("config.connections.migration") }}</p>
    <p v-else-if="phase === 'unavailable'" role="status">
      {{ t("config.connections.unavailable") }}
    </p>
    <p v-else-if="phase === 'failed'" role="alert">{{ failure }}</p>
    <div v-else-if="phase === 'ready'" class="connections-body">
      <div class="connections-nav" role="tablist" :aria-label="t('config.nav.connections')">
        <button
          type="button"
          role="tab"
          :aria-selected="domain === 'models'"
          @click="chooseDomain('models')"
        >
          {{ t("config.connections.domains.models") }}
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="domain === 'mcp'"
          @click="chooseDomain('mcp')"
        >
          {{ t("config.connections.domains.mcp") }}
        </button>
      </div>
      <div
        class="connections-nav"
        role="tablist"
        :aria-label="t(`config.connections.domains.${domain}`)"
      >
        <button
          v-for="item in subtabs"
          :key="item"
          type="button"
          role="tab"
          :aria-selected="tab === item"
          @click="chooseTab(item)"
        >
          {{ t(`config.connections.tabs.${item}`) }}
        </button>
      </div>
      <div class="connections-toolbar">
        <UiButton
          v-if="tab !== 'capabilities'"
          type="button"
          variant="secondary"
          @click="startCreate"
        >
          {{ t("config.connections.create") }}
        </UiButton>
        <UiButton type="button" variant="ghost" @click="load">
          {{ t("config.connections.refresh") }}
        </UiButton>
        <UiButton
          v-if="tab === 'capabilities'"
          type="button"
          variant="secondary"
          :disabled="busy"
          @click="requestCatalogUpdate"
        >
          {{ t("config.connections.updateCatalog") }}
        </UiButton>
        <label v-if="tab === 'endpoints' || tab === 'capabilities'" class="connections-field">
          <span>{{ t("config.connections.modelType") }}</span>
          <UiSelect :model-value="modelFilter" @update:model-value="setModelFilter">
            <option value="">{{ t("config.connections.all") }}</option>
            <option v-for="item in MODEL_TYPES" :key="item" :value="item">{{ item }}</option>
          </UiSelect>
        </label>
        <label v-if="tab === 'capabilities'" class="connections-field">
          <span>{{ t("config.connections.filter") }}</span>
          <UiTextField v-model="catalogQuery" @keydown.enter.prevent="loadTab" />
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
      </div>
      <p v-if="failure && !showForm" role="alert">{{ failure }}</p>
      <p v-if="notice && !showForm" role="status">{{ notice }}</p>
      <div class="connections-workspace">
        <div class="connections-table-wrap">
          <table class="connections-table" data-testid="connection-list">
            <thead>
              <tr>
                <th v-for="column in columns" :key="column" scope="col">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.id"
                :aria-selected="rowSelected(row.id)"
                @click="tab === 'capabilities' ? openProfile(row.id) : openRow(row.id)"
              >
                <td v-for="(cell, index) in row.cells" :key="`${row.id}-${index}`">
                  <button
                    v-if="index === 0"
                    type="button"
                    @click.stop="tab === 'capabilities' ? openProfile(row.id) : openRow(row.id)"
                  >
                    {{ cell }}
                  </button>
                  <template v-else>{{ cell }}</template>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!rows.length">{{ t("config.connections.empty") }}</p>
        </div>
      </div>
      <div v-if="showForm" class="connections-drawer">
        <button
          type="button"
          class="connections-drawer-backdrop"
          :aria-label="t('common.close')"
          @click="requestClose"
        />
        <form
          ref="formPanel"
          class="connections-form"
          role="dialog"
          aria-modal="true"
          tabindex="-1"
          :aria-label="formTitle"
          @submit.prevent="save"
          @keydown.esc.prevent="requestClose"
        >
          <header class="connections-form-head">
            <h2>{{ formTitle }}</h2>
            <UiButton type="button" variant="secondary" @click="requestClose">
              {{ t("common.close") }}
            </UiButton>
          </header>
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
              <small>{{ t("config.connections.secretHint") }}</small>
            </label>
            <label v-if="keyConfigured" class="connections-check">
              <input v-model="form.clearKey" type="checkbox" />
              {{ t("config.connections.clearSecret") }}
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.options") }}</span>
              <UiTextField v-model="form.optionsText" multiline rows="6" />
              <small>{{ t("config.connections.secretHint") }}</small>
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
              <small>{{ t("config.connections.restart") }}</small>
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.modelType") }}</span>
              <UiSelect :model-value="form.modelType" @update:model-value="setEndpointModelType">
                <option v-for="item in modelTypeOptions" :key="item" :value="item">
                  {{ item }}
                </option>
              </UiSelect>
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.apiFormat") }}</span>
              <UiSelect v-model="form.apiFormatChoice">
                <option v-for="item in API_FORMATS" :key="item" :value="item">{{ item }}</option>
                <option value="custom">{{ t("config.connections.customFormat") }}</option>
              </UiSelect>
            </label>
            <label v-if="form.apiFormatChoice === 'custom'" class="connections-field">
              <span>{{ t("config.connections.customFormat") }}</span>
              <UiTextField v-model="form.apiFormatCustom" required />
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.capabilities") }}</span>
              <UiSelect v-model="form.capabilitiesKey">
                <option value="">{{ t("config.connections.unbound") }}</option>
                <option
                  v-for="item in capabilityChoices"
                  :key="item.model_key"
                  :value="item.model_key"
                >
                  {{ item.display_name }} ({{ item.model_key }})
                </option>
              </UiSelect>
            </label>
            <label class="connections-field">
              <span>{{ t("config.connections.capabilityFilter") }}</span>
              <UiTextField v-model="capabilityFilter" />
            </label>
            <section
              v-if="form.capabilitiesKey"
              class="connections-capability-block"
              data-testid="endpoint-capabilities"
            >
              <h2>{{ t("config.connections.capabilityHeading") }}</h2>
              <p v-if="capabilityDetailFailure" role="alert">{{ capabilityDetailFailure }}</p>
              <p v-else-if="!capabilityDetail">{{ t("common.loading") }}</p>
              <p v-else-if="!endpointCapabilityEntries.length">
                {{ t("config.connections.capabilitiesUndeclared") }}
              </p>
              <dl v-else class="connections-capabilities">
                <template v-for="entry in endpointCapabilityEntries" :key="entry.name">
                  <dt>{{ capabilityLabel(entry.name) }}</dt>
                  <dd>{{ entry.text }}</dd>
                </template>
              </dl>
            </section>
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
              <small>{{ t("config.connections.secretHint") }}</small>
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
                <small>{{ t("config.connections.secretHint") }}</small>
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
                <small>{{ t("config.connections.secretHint") }}</small>
              </label>
              <label class="connections-field">
                <span>{{ t("config.connections.protocolHint") }}</span>
                <UiTextField v-model="form.protocolHint" />
              </label>
            </template>
            <label v-else class="connections-field">
              <span>{{ t("config.connections.options") }}</span>
              <UiTextField v-model="form.optionsText" multiline rows="6" />
              <small>{{ t("config.connections.secretHint") }}</small>
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
                  <option value="inherit">
                    {{ t("config.connections.bindingModes.inherit") }}
                  </option>
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
            <UiButton
              type="submit"
              variant="primary"
              :disabled="busy"
              data-testid="connection-save"
            >
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
    </div>
    <BaseModal
      v-if="profileKey"
      :title="profile?.display_name || profileKey"
      size="md"
      @request-close="closeProfile"
    >
      <template v-if="profile" #subtitle>
        <p class="connections-note">
          {{ profile.model_key }} · {{ profile.model_type }} · {{ profile.origin }}
        </p>
      </template>
      <p v-if="profileFailure" role="alert">{{ profileFailure }}</p>
      <p v-else-if="!profile">{{ t("common.loading") }}</p>
      <p v-else-if="!profileEntries.length">{{ t("config.connections.capabilitiesUndeclared") }}</p>
      <dl v-else class="connections-capabilities">
        <template v-for="entry in profileEntries" :key="entry.name">
          <dt>{{ capabilityLabel(entry.name) }}</dt>
          <dd>{{ entry.text }}</dd>
        </template>
      </dl>
    </BaseModal>
    <ConfirmDialog
      v-if="dialog"
      :title="dialogTitle"
      :message="dialogMessage"
      :confirm-label="dialogConfirm"
      :danger="dialog === 'delete'"
      @confirm="confirmDialog"
      @cancel="cancelDialog"
    />
  </section>
</template>

<style scoped>
.connections {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  height: 100%;
  min-width: 0;
  min-height: 0;
  color: var(--color-text);
  font-size: var(--font-ui-body);
  container-name: connections;
  container-type: inline-size;
}

.connections-note,
.connections-field small {
  color: var(--color-text-muted);
}

.connections-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0.5rem;
  min-height: 0;
}

.connections-field,
.connections-actions {
  display: grid;
  gap: 0.5rem;
}

.connections-nav,
.connections-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: end;
}

.connections-nav button {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text);
  padding: 0.35rem 0.75rem;
}

.connections-nav button[aria-selected="true"] {
  background: var(--color-selected);
}

.connections-toolbar .connections-field {
  min-width: 12rem;
}

.connections-workspace {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
}

.connections-drawer {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: flex;
  justify-content: flex-end;
}

.connections-drawer-backdrop {
  position: absolute;
  inset: 0;
  border: 0;
  padding: 0;
  background: var(--ui-overlay);
  cursor: pointer;
}

.connections-form {
  position: relative;
  display: grid;
  align-content: start;
  gap: 0.5rem;
  width: min(32rem, 100%);
  height: 100%;
  margin: 0;
  padding: 0.75rem;
  overflow: auto;
  border: 0;
  border-left: 1px solid var(--ui-panel-border);
  background: var(--ui-panel);
  box-shadow: var(--ui-panel-shadow);
}

.connections-form:focus {
  outline: none;
}

.connections-form-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding-bottom: 0.5rem;
  background: var(--ui-panel);
}

.connections-form-head h2 {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.connections-table-wrap {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.connections-table {
  width: 100%;
  border-collapse: collapse;
}

.connections-table th,
.connections-table td {
  padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--color-border);
  text-align: start;
  overflow-wrap: anywhere;
}

.connections-table tr[aria-selected="true"] {
  background: var(--color-selected);
}

.connections-table button {
  border: 0;
  background: transparent;
  color: var(--color-text);
  padding: 0;
  text-align: start;
}

.connections-form ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.connections-check {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}

.connections-capability-block {
  display: grid;
  gap: 0.5rem;
}

.connections-capability-block h2 {
  margin: 0;
  font-size: var(--font-ui-body);
  font-weight: 600;
}

.connections-capabilities {
  display: grid;
  grid-template-columns: minmax(8rem, 12rem) minmax(0, 1fr);
  gap: 0.35rem 0.75rem;
  margin: 0;
}

.connections-capabilities dt {
  color: var(--color-text-muted);
}

.connections-capabilities dd {
  margin: 0;
  overflow-wrap: anywhere;
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

@container connections (max-width: 52rem) {
  .connections-body {
    overflow: auto;
  }

  .connections-workspace {
    flex: none;
  }

  .connections-table-wrap {
    max-height: 24rem;
  }

  .connections-drawer {
    position: static;
    justify-content: start;
  }

  .connections-drawer-backdrop {
    display: none;
  }

  .connections-form {
    width: min(32rem, 100%);
    height: auto;
    border-left: 0;
    border-top: 1px solid var(--ui-panel-border);
    box-shadow: none;
  }
}
</style>
