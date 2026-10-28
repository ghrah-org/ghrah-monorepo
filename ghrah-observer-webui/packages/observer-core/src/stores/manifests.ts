import type { ManifestAbilityEventPayload, ManifestAgentEventPayload } from "@ghrah/protocol";
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

export interface ValidationResult {
  is_valid: boolean;
  errors: string[];
}

export function extractAbilityList(data: unknown): AbilityManifestInfo[] | null {
  if (data == null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (!Array.isArray(d.abilities)) return null;
  return d.abilities as AbilityManifestInfo[];
}

export function extractAgentList(data: unknown): AgentManifestInfo[] | null {
  if (data == null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (!Array.isArray(d.agents)) return null;
  return d.agents as AgentManifestInfo[];
}

export function extractManifestEntry(
  data: unknown,
): AbilityManifestInfo | AgentManifestInfo | null {
  if (data == null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.manifest == null || typeof d.manifest !== "object") return null;
  return d.manifest as AbilityManifestInfo | AgentManifestInfo;
}

export function extractValidationResult(data: unknown): ValidationResult | null {
  if (data == null || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (typeof d.is_valid !== "boolean" || !Array.isArray(d.errors)) return null;
  return data as ValidationResult;
}

export const useManifestsStore = defineStore("ghrah-manifests", () => {
  const abilities = ref<Map<string, AbilityManifestInfo>>(new Map());
  const abilitiesLoading = ref(false);

  const agents = ref<Map<string, AgentManifestInfo>>(new Map());
  const agentsLoading = ref(false);

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

  function setAbilities(list: AbilityManifestInfo[]) {
    abilities.value = new Map(list.map((a) => [a.full_name, a]));
  }

  function upsertAbility(info: AbilityManifestInfo) {
    const next = new Map(abilities.value);
    next.set(info.full_name, info);
    abilities.value = next;
  }

  function removeAbility(fullName: string) {
    const next = new Map(abilities.value);
    next.delete(fullName);
    abilities.value = next;
    if (selectedAbilityFullName.value === fullName) {
      selectedAbilityFullName.value = null;
    }
  }

  function setAgents(list: AgentManifestInfo[]) {
    agents.value = new Map(list.map((a) => [a.full_name, a]));
  }

  function upsertAgent(info: AgentManifestInfo) {
    const next = new Map(agents.value);
    next.set(info.full_name, info);
    agents.value = next;
  }

  function removeAgent(fullName: string) {
    const next = new Map(agents.value);
    next.delete(fullName);
    agents.value = next;
    if (selectedAgentFullName.value === fullName) {
      selectedAgentFullName.value = null;
    }
  }

  function onManifestAbilityCreated(_payload: ManifestAbilityEventPayload) {
    // UI notification — caller should re-fetch list if interested
  }

  function onManifestAbilityInvalidated(payload: ManifestAbilityEventPayload) {
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
    abilityList,
    abilitiesByNamespace,
    agents,
    agentsLoading,
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
