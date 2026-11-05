import {
  type ManifestAbilityEventPayload,
  type ManifestAgentEventPayload,
  ManifestGetAbilityResponsePayloadSchema,
  ManifestListAbilitiesResponsePayloadSchema,
  type ManifestListErrorPayload,
  type ManifestValidateResponsePayload,
  ManifestValidateResponsePayloadSchema,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

export interface AbilityManifestInfo {
  full_name: string;
  namespace: string;
  name: string;
  title: string;
  description: string;
  tags: string[];
  permissions: {
    require_hitl: boolean;
    fs_read_only: boolean;
    fs_write: boolean;
    net_access: boolean;
    shell_access: boolean;
    allowed_paths?: string[];
    denied_paths?: string[];
    allowed_commands?: string[];
    denied_commands?: string[];
  };
  implementation_type: string;
  has_hitl: boolean;
}

export interface AgentManifestInfo {
  full_name: string;
  namespace: string;
  name: string;
  title: string;
  description: string;
  tags: string[];
  endpoint_name: string;
  system_prompt: string;
  ability_refs: string[];
  max_iterations: number;
}

export type ValidationResult = ManifestValidateResponsePayload;

/** 原文与完整文档都是服务端权威的缓存，不从摘要重建或独立修改。 */
export interface ManifestDetail {
  full_name: string;
  manifest: Record<string, unknown>;
  source: string;
}

export type ManifestEntry =
  | { kind: "ability"; detail: ManifestDetail; summary: AbilityManifestInfo }
  | { kind: "agent"; detail: ManifestDetail; summary: AgentManifestInfo };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isAbilityInfo(
  value: Record<string, unknown>,
): value is AbilityManifestInfo & Record<string, unknown> {
  const permissions = value.permissions;
  return (
    ["full_name", "namespace", "name", "title", "description", "implementation_type"].every(
      (key) => typeof value[key] === "string",
    ) &&
    typeof value.has_hitl === "boolean" &&
    isStringArray(value.tags) &&
    isRecord(permissions) &&
    ["require_hitl", "fs_read_only", "fs_write", "net_access", "shell_access"].every(
      (key) => typeof permissions[key] === "boolean",
    ) &&
    ["allowed_paths", "denied_paths", "allowed_commands", "denied_commands"].every(
      (key) => permissions[key] === undefined || isStringArray(permissions[key]),
    )
  );
}

export function extractAbilityList(data: unknown): {
  abilities: AbilityManifestInfo[];
  errors: ManifestListErrorPayload[];
} | null {
  const parsed = ManifestListAbilitiesResponsePayloadSchema.safeParse(data);
  if (!parsed.success) return null;
  const abilities: AbilityManifestInfo[] = [];
  for (const item of parsed.data.abilities) {
    if (!isAbilityInfo(item)) return null;
    abilities.push(item);
  }
  return { abilities, errors: parsed.data.errors };
}

export function extractAgentList(data: unknown): AgentManifestInfo[] | null {
  if (data == null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (!Array.isArray(d.agents)) return null;
  return d.agents as AgentManifestInfo[];
}

export function extractManifestEntry(
  data: unknown,
  expected?: { kind: "ability" | "agent"; fullName: string },
): ManifestEntry | null {
  const parsed = ManifestGetAbilityResponsePayloadSchema.safeParse(data);
  if (!parsed.success) return null;
  const { manifest, source } = parsed.data;
  const metadata = manifest.metadata;
  if (
    !isRecord(metadata) ||
    typeof metadata.namespace !== "string" ||
    !metadata.namespace ||
    typeof metadata.name !== "string" ||
    !metadata.name ||
    typeof metadata.title !== "string" ||
    !isStringArray(metadata.tags)
  )
    return null;
  const fullName = `${metadata.namespace}.${metadata.name}`;
  if (expected && (expected.kind !== manifest.manifest || expected.fullName !== fullName)) {
    return null;
  }
  const detail: ManifestDetail = { full_name: fullName, manifest, source };
  const common = {
    full_name: fullName,
    namespace: metadata.namespace,
    name: metadata.name,
    title: metadata.title,
    tags: metadata.tags,
  };
  if (manifest.manifest === "ability") {
    if (!isRecord(manifest.implementation) || !isRecord(metadata.permissions)) return null;
    const summary = {
      ...common,
      description: metadata.description,
      permissions: metadata.permissions,
      implementation_type: manifest.implementation.type,
      has_hitl: metadata.permissions.require_hitl,
    };
    return isAbilityInfo(summary) ? { kind: "ability", detail, summary } : null;
  }
  if (manifest.manifest !== "agent" || !isRecord(manifest.model)) return null;
  if (
    typeof manifest.model.endpoint_name !== "string" ||
    typeof manifest.description !== "string" ||
    typeof manifest.system_prompt !== "string" ||
    typeof manifest.max_iterations !== "number" ||
    !Number.isInteger(manifest.max_iterations) ||
    !Array.isArray(manifest.abilities)
  )
    return null;
  const refs: string[] = [];
  for (const ability of manifest.abilities) {
    if (!isRecord(ability)) return null;
    const ref = ability.ref ?? ability.type;
    if (typeof ref !== "string" || !ref) return null;
    refs.push(ref);
  }
  return {
    kind: "agent",
    detail,
    summary: {
      ...common,
      description: manifest.description,
      endpoint_name: manifest.model.endpoint_name,
      system_prompt: manifest.system_prompt,
      ability_refs: refs,
      max_iterations: manifest.max_iterations,
    },
  };
}

export function extractValidationResult(data: unknown): ValidationResult | null {
  const parsed = ManifestValidateResponsePayloadSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export const useManifestsStore = defineStore("ghrah-manifests", () => {
  const abilities = ref<Map<string, AbilityManifestInfo>>(new Map());
  const abilitiesLoading = ref(false);
  const abilityErrors = ref<ManifestListErrorPayload[]>([]);
  const abilityDetails = ref<Map<string, ManifestDetail>>(new Map());

  const agents = ref<Map<string, AgentManifestInfo>>(new Map());
  const agentsLoading = ref(false);
  const agentDetails = ref<Map<string, ManifestDetail>>(new Map());

  const selectedAbilityFullName = ref<string | null>(null);
  const selectedAgentFullName = ref<string | null>(null);

  const validationResult = ref<ValidationResult | null>(null);
  const isValidating = ref(false);

  const abilityList = computed(() => [...abilities.value.values()]);
  const agentList = computed(() => [...agents.value.values()]);

  const selectedAbility = computed(() => {
    if (!selectedAbilityFullName.value) return null;
    return abilities.value.get(selectedAbilityFullName.value) ?? null;
  });

  const selectedAgent = computed(() => {
    if (!selectedAgentFullName.value) return null;
    return agents.value.get(selectedAgentFullName.value) ?? null;
  });

  const abilitiesByNamespace = computed(() => {
    const map = new Map<string, AbilityManifestInfo[]>();
    for (const a of abilities.value.values()) {
      const ns = a.namespace;
      if (!map.has(ns)) map.set(ns, []);
      map.get(ns)!.push(a);
    }
    return map;
  });

  const agentsByNamespace = computed(() => {
    const map = new Map<string, AgentManifestInfo[]>();
    for (const a of agents.value.values()) {
      const ns = a.namespace;
      if (!map.has(ns)) map.set(ns, []);
      map.get(ns)!.push(a);
    }
    return map;
  });

  function setAbilities(list: AbilityManifestInfo[], errors: ManifestListErrorPayload[] = []) {
    abilities.value = new Map(list.map((a) => [a.full_name, a]));
    abilityErrors.value = errors;
    for (const key of abilityDetails.value.keys()) {
      if (!abilities.value.has(key)) abilityDetails.value.delete(key);
    }
  }

  function upsertAbility(info: AbilityManifestInfo) {
    const next = new Map(abilities.value);
    next.set(info.full_name, info);
    abilities.value = next;
  }

  function removeAbility(fullName: string) {
    abilityDetails.value.delete(fullName);
    abilityErrors.value = abilityErrors.value.filter((item) => item.full_name !== fullName);
    const next = new Map(abilities.value);
    next.delete(fullName);
    abilities.value = next;
    if (selectedAbilityFullName.value === fullName) {
      selectedAbilityFullName.value = null;
    }
  }

  function setAgents(list: AgentManifestInfo[]) {
    agents.value = new Map(list.map((a) => [a.full_name, a]));
    for (const key of agentDetails.value.keys()) {
      if (!agents.value.has(key)) agentDetails.value.delete(key);
    }
  }

  function upsertAgent(info: AgentManifestInfo) {
    const next = new Map(agents.value);
    next.set(info.full_name, info);
    agents.value = next;
  }

  function removeAgent(fullName: string) {
    agentDetails.value.delete(fullName);
    const next = new Map(agents.value);
    next.delete(fullName);
    agents.value = next;
    if (selectedAgentFullName.value === fullName) {
      selectedAgentFullName.value = null;
    }
  }

  function upsertManifestEntry(entry: ManifestEntry) {
    if (entry.kind === "ability") {
      upsertAbility(entry.summary);
      abilityDetails.value.set(entry.detail.full_name, entry.detail);
      abilityErrors.value = abilityErrors.value.filter(
        (item) => item.full_name !== entry.detail.full_name,
      );
    } else {
      upsertAgent(entry.summary);
      agentDetails.value.set(entry.detail.full_name, entry.detail);
    }
  }

  function onManifestAbilityCreated(_payload: ManifestAbilityEventPayload) {
    // UI notification — caller should re-fetch list if interested
  }

  function onManifestAbilityInvalidated(payload: ManifestAbilityEventPayload) {
    abilityDetails.value.delete(payload.full_name);
    const next = new Map(abilities.value);
    next.delete(payload.full_name);
    abilities.value = next;
    if (selectedAbilityFullName.value === payload.full_name) {
      selectedAbilityFullName.value = null;
    }
  }

  function onManifestAbilityDeleted(payload: ManifestAbilityEventPayload) {
    removeAbility(payload.full_name);
  }

  function onManifestAgentCreated(_payload: ManifestAgentEventPayload) {
    // UI notification — caller should re-fetch list if interested
  }

  function onManifestAgentInvalidated(payload: ManifestAgentEventPayload) {
    agentDetails.value.delete(payload.full_name);
    const next = new Map(agents.value);
    next.delete(payload.full_name);
    agents.value = next;
    if (selectedAgentFullName.value === payload.full_name) {
      selectedAgentFullName.value = null;
    }
  }

  function onManifestAgentDeleted(payload: ManifestAgentEventPayload) {
    removeAgent(payload.full_name);
  }

  function selectAbility(fullName: string | null) {
    selectedAbilityFullName.value = fullName;
  }

  function selectAgent(fullName: string | null) {
    selectedAgentFullName.value = fullName;
  }

  function setValidationResult(result: ValidationResult | null) {
    validationResult.value = result;
  }

  function setValidating(v: boolean) {
    isValidating.value = v;
  }

  return {
    abilities,
    abilitiesLoading,
    abilityErrors,
    abilityDetails,
    abilityList,
    abilitiesByNamespace,
    agents,
    agentsLoading,
    agentDetails,
    agentList,
    agentsByNamespace,
    selectedAbilityFullName,
    selectedAgentFullName,
    selectedAbility,
    selectedAgent,
    validationResult,
    isValidating,
    setAbilities,
    upsertAbility,
    removeAbility,
    setAgents,
    upsertAgent,
    upsertManifestEntry,
    removeAgent,
    onManifestAbilityCreated,
    onManifestAbilityInvalidated,
    onManifestAbilityDeleted,
    onManifestAgentCreated,
    onManifestAgentInvalidated,
    onManifestAgentDeleted,
    selectAbility,
    selectAgent,
    setValidationResult,
    setValidating,
  };
});
