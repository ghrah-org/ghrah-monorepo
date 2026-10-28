import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import {
  type AbilityManifestInfo,
  type AgentManifestInfo,
  extractAbilityList,
  extractAgentList,
  extractManifestEntry,
  extractValidationResult,
  useManifestsStore,
} from "./manifests.js";

function makeAbility(
  fullName: string,
  overrides?: Partial<AbilityManifestInfo>,
): AbilityManifestInfo {
  const parts = fullName.split(".");
  const name = parts[parts.length - 1];
  const namespace = parts.slice(0, -1).join(".");
  return {
    full_name: fullName,
    namespace,
    name,
    title: name.replace(/_/g, " "),
    description: "Test ability",
    tags: [],
    permissions: {
      require_hitl: false,
      fs_read_only: true,
      fs_write: false,
      net_access: false,
      shell_access: false,
    },
    implementation_type: "builtin",
    has_hitl: false,
    ...overrides,
  };
}

function makeAgent(fullName: string, overrides?: Partial<AgentManifestInfo>): AgentManifestInfo {
  const parts = fullName.split(".");
  const name = parts[parts.length - 1];
  const namespace = parts.slice(0, -1).join(".");
  return {
    full_name: fullName,
    namespace,
    name,
    title: name,
    description: "Test agent",
    tags: ["test"],
    endpoint_name: "default",
    system_prompt: "You are helpful.",
    ability_refs: [],
    max_iterations: 10,
    ...overrides,
  };
}

describe("useManifestsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  // ── 初始化 ──

  it("starts with empty abilities and agents maps", () => {
    const store = useManifestsStore();
    expect(store.abilities.size).toBe(0);
    expect(store.agents.size).toBe(0);
    expect(store.abilityList).toHaveLength(0);
    expect(store.agentList).toHaveLength(0);
  });

  it("starts with null selection", () => {
    const store = useManifestsStore();
    expect(store.selectedAbility).toBeNull();
    expect(store.selectedAgent).toBeNull();
  });

  // ── Ability CRUD ──

  it("setAbilities populates map from list", () => {
    const store = useManifestsStore();
    const list = [makeAbility("ghrah.fs.read_file"), makeAbility("ghrah.fs.write_file")];
    store.setAbilities(list);
    expect(store.abilities.size).toBe(2);
    expect(store.abilities.get("ghrah.fs.read_file")?.name).toBe("read_file");
  });

  it("upsertAbility adds new entry", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    expect(store.abilities.size).toBe(1);
    expect(store.abilities.get("ghrah.fs.read_file")?.name).toBe("read_file");
  });

  it("upsertAbility updates existing entry with all fields", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file", { title: "old", tags: ["v1"] }));
    store.upsertAbility(
      makeAbility("ghrah.fs.read_file", { title: "new", tags: ["v2", "updated"] }),
    );
    const ability = store.abilities.get("ghrah.fs.read_file");
    expect(ability?.title).toBe("new");
    expect(ability?.tags).toEqual(["v2", "updated"]);
  });

  it("removeAbility deletes entry and clears selection if selected", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    store.selectAbility("ghrah.fs.read_file");
    expect(store.selectedAbilityFullName).toBe("ghrah.fs.read_file");

    store.removeAbility("ghrah.fs.read_file");
    expect(store.abilities.has("ghrah.fs.read_file")).toBe(false);
    expect(store.selectedAbilityFullName).toBeNull();
  });

  it("removeAbility does NOT clear selection if deleting different entry", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    store.upsertAbility(makeAbility("ghrah.fs.write_file"));
    store.selectAbility("ghrah.fs.read_file");

    store.removeAbility("ghrah.fs.write_file");
    expect(store.selectedAbilityFullName).toBe("ghrah.fs.read_file");
  });

  // ── Agent CRUD ──

  it("setAgents populates map from list", () => {
    const store = useManifestsStore();
    const list = [makeAgent("ghrah.designer"), makeAgent("ghrah.coder")];
    store.setAgents(list);
    expect(store.agents.size).toBe(2);
    expect(store.agents.get("ghrah.designer")?.name).toBe("designer");
  });

  it("upsertAgent adds new entry", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    expect(store.agents.size).toBe(1);
  });

  it("upsertAgent updates existing entry with all fields", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer", { max_iterations: 5, system_prompt: "old" }));
    store.upsertAgent(makeAgent("ghrah.designer", { max_iterations: 15, system_prompt: "new" }));
    const agent = store.agents.get("ghrah.designer");
    expect(agent?.max_iterations).toBe(15);
    expect(agent?.system_prompt).toBe("new");
  });

  it("removeAgent deletes entry and clears selection if selected", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    store.selectAgent("ghrah.designer");
    expect(store.selectedAgentFullName).toBe("ghrah.designer");

    store.removeAgent("ghrah.designer");
    expect(store.agents.has("ghrah.designer")).toBe(false);
    expect(store.selectedAgentFullName).toBeNull();
  });

  it("removeAgent does NOT clear selection if deleting different entry", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    store.upsertAgent(makeAgent("ghrah.coder"));
    store.selectAgent("ghrah.designer");

    store.removeAgent("ghrah.coder");
    expect(store.selectedAgentFullName).toBe("ghrah.designer");
  });

  // ── Namespace grouping ──

  it("abilitiesByNamespace groups by namespace", () => {
    const store = useManifestsStore();
    store.setAbilities([
      makeAbility("ghrah.fs.read_file"),
      makeAbility("ghrah.fs.write_file"),
      makeAbility("ghrah.shell.execute_command"),
    ]);

    const byNs = store.abilitiesByNamespace;
    expect(byNs.get("ghrah.fs")).toHaveLength(2);
    expect(byNs.get("ghrah.shell")).toHaveLength(1);
  });

  it("agentsByNamespace groups by namespace", () => {
    const store = useManifestsStore();
    store.setAgents([
      makeAgent("ghrah.designer"),
      makeAgent("ghrah.coder"),
      makeAgent("my_project.helper"),
    ]);

    const byNs = store.agentsByNamespace;
    expect(byNs.get("ghrah")).toHaveLength(2);
    expect(byNs.get("my_project")).toHaveLength(1);
  });

  // ── Computed ──

  it("abilityList returns correct array after setAbilities", () => {
    const store = useManifestsStore();
    const a1 = makeAbility("ghrah.fs.read_file");
    const a2 = makeAbility("ghrah.fs.write_file");
    store.setAbilities([a1, a2]);
    expect(store.abilityList).toHaveLength(2);
    expect(store.abilityList).toContainEqual(a1);
    expect(store.abilityList).toContainEqual(a2);
  });

  it("agentList returns correct array after setAgents", () => {
    const store = useManifestsStore();
    const g1 = makeAgent("ghrah.designer");
    const g2 = makeAgent("ghrah.coder");
    store.setAgents([g1, g2]);
    expect(store.agentList).toHaveLength(2);
    expect(store.agentList).toContainEqual(g1);
    expect(store.agentList).toContainEqual(g2);
  });

  it("abilityList includes new item after upsertAbility", () => {
    const store = useManifestsStore();
    store.setAbilities([makeAbility("ghrah.fs.read_file")]);
    expect(store.abilityList).toHaveLength(1);

    store.upsertAbility(makeAbility("ghrah.fs.write_file"));
    expect(store.abilityList).toHaveLength(2);
  });

  it("agentList includes new item after upsertAgent", () => {
    const store = useManifestsStore();
    store.setAgents([makeAgent("ghrah.designer")]);
    expect(store.agentList).toHaveLength(1);

    store.upsertAgent(makeAgent("ghrah.coder"));
    expect(store.agentList).toHaveLength(2);
  });

  // ── Selection ──

  it("selectAbility updates selectedAbilityFullName and selectedAbility computed", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    store.selectAbility("ghrah.fs.read_file");
    expect(store.selectedAbilityFullName).toBe("ghrah.fs.read_file");
    expect(store.selectedAbility?.name).toBe("read_file");
  });

  it("selectAgent updates selectedAgentFullName and selectedAgent computed", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    store.selectAgent("ghrah.designer");
    expect(store.selectedAgentFullName).toBe("ghrah.designer");
    expect(store.selectedAgent?.name).toBe("designer");
  });

  it("selectedAbility returns null when fullName not found", () => {
    const store = useManifestsStore();
    store.selectAbility("ghrah.fs.nonexistent");
    expect(store.selectedAbility).toBeNull();
  });

  it("selectedAgent returns null when fullName not found", () => {
    const store = useManifestsStore();
    store.selectAgent("ghrah.nonexistent");
    expect(store.selectedAgent).toBeNull();
  });

  // ── Event handlers ──

  it("onManifestAbilityCreated does not throw", () => {
    const store = useManifestsStore();
    expect(() =>
      store.onManifestAbilityCreated({ full_name: "ghrah.fs.read_file", namespace: "ghrah.fs" }),
    ).not.toThrow();
  });

  it("onManifestAbilityInvalidated removes cached ability", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    expect(store.abilities.has("ghrah.fs.read_file")).toBe(true);

    store.onManifestAbilityInvalidated({ full_name: "ghrah.fs.read_file", namespace: "ghrah.fs" });
    expect(store.abilities.has("ghrah.fs.read_file")).toBe(false);
  });

  it("onManifestAbilityInvalidated clears selection if selected", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    store.selectAbility("ghrah.fs.read_file");

    store.onManifestAbilityInvalidated({ full_name: "ghrah.fs.read_file", namespace: "ghrah.fs" });
    expect(store.selectedAbilityFullName).toBeNull();
  });

  it("onManifestAbilityDeleted removes cached ability and selection", () => {
    const store = useManifestsStore();
    store.upsertAbility(makeAbility("ghrah.fs.read_file"));
    store.selectAbility("ghrah.fs.read_file");

    store.onManifestAbilityDeleted({ full_name: "ghrah.fs.read_file", namespace: "ghrah.fs" });
    expect(store.abilities.has("ghrah.fs.read_file")).toBe(false);
    expect(store.selectedAbilityFullName).toBeNull();
  });

  it("onManifestAgentCreated does not throw", () => {
    const store = useManifestsStore();
    expect(() =>
      store.onManifestAgentCreated({ full_name: "ghrah.designer", namespace: "ghrah" }),
    ).not.toThrow();
  });

  it("onManifestAgentInvalidated removes cached agent", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    expect(store.agents.has("ghrah.designer")).toBe(true);

    store.onManifestAgentInvalidated({ full_name: "ghrah.designer", namespace: "ghrah" });
    expect(store.agents.has("ghrah.designer")).toBe(false);
  });

  it("onManifestAgentInvalidated clears selection if selected", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    store.selectAgent("ghrah.designer");

    store.onManifestAgentInvalidated({ full_name: "ghrah.designer", namespace: "ghrah" });
    expect(store.selectedAgentFullName).toBeNull();
  });

  it("onManifestAgentDeleted removes cached agent and selection", () => {
    const store = useManifestsStore();
    store.upsertAgent(makeAgent("ghrah.designer"));
    store.selectAgent("ghrah.designer");

    store.onManifestAgentDeleted({ full_name: "ghrah.designer", namespace: "ghrah" });
    expect(store.agents.has("ghrah.designer")).toBe(false);
    expect(store.selectedAgentFullName).toBeNull();
  });

  // ── Validation state ──

  it("setValidationResult updates validation state", () => {
    const store = useManifestsStore();
    store.setValidationResult({ is_valid: false, errors: ["Invalid YAML"] });
    expect(store.validationResult?.is_valid).toBe(false);
    expect(store.validationResult?.errors).toEqual(["Invalid YAML"]);
  });

  it("setValidationResult(null) resets validation state", () => {
    const store = useManifestsStore();
    store.setValidationResult({ is_valid: false, errors: ["Invalid YAML"] });
    expect(store.validationResult).not.toBeNull();

    store.setValidationResult(null);
    expect(store.validationResult).toBeNull();
  });

  it("setValidating toggles loading flag", () => {
    const store = useManifestsStore();
    expect(store.isValidating).toBe(false);
    store.setValidating(true);
    expect(store.isValidating).toBe(true);
    store.setValidating(false);
    expect(store.isValidating).toBe(false);
  });
});

// ── Extract helpers ──

describe("extractAbilityList", () => {
  it("returns abilities array from valid data", () => {
    const data = { abilities: [{ full_name: "a" }] };
    const result = extractAbilityList(data);
    expect(result).toEqual([{ full_name: "a" }]);
  });

  it("returns null for null data", () => {
    expect(extractAbilityList(null)).toBeNull();
  });

  it("returns null for non-object data", () => {
    expect(extractAbilityList("string")).toBeNull();
  });

  it("returns null when abilities is not an array", () => {
    expect(extractAbilityList({ abilities: "not-array" })).toBeNull();
  });

  it("returns null when abilities key is missing", () => {
    expect(extractAbilityList({})).toBeNull();
  });
});

describe("extractAgentList", () => {
  it("returns agents array from valid data", () => {
    const data = { agents: [{ full_name: "g" }] };
    const result = extractAgentList(data);
    expect(result).toEqual([{ full_name: "g" }]);
  });

  it("returns null for null data", () => {
    expect(extractAgentList(null)).toBeNull();
  });

  it("returns null when agents is not an array", () => {
    expect(extractAgentList({ agents: 42 })).toBeNull();
  });
});

describe("extractManifestEntry", () => {
  it("returns manifest from valid data", () => {
    const manifest = { full_name: "a", namespace: "ns" };
    const data = { manifest };
    const result = extractManifestEntry(data);
    expect(result).toEqual(manifest);
  });

  it("returns null for null data", () => {
    expect(extractManifestEntry(null)).toBeNull();
  });

  it("returns null when manifest is null", () => {
    expect(extractManifestEntry({ manifest: null })).toBeNull();
  });

  it("returns null when manifest is a string", () => {
    expect(extractManifestEntry({ manifest: "not-object" })).toBeNull();
  });

  it("returns null when manifest key is missing", () => {
    expect(extractManifestEntry({})).toBeNull();
  });
});

describe("extractValidationResult", () => {
  it("returns ValidationResult from valid data", () => {
    const data = { is_valid: true, errors: [] };
    const result = extractValidationResult(data);
    expect(result).toEqual({ is_valid: true, errors: [] });
  });

  it("returns ValidationResult with errors", () => {
    const data = { is_valid: false, errors: ["err1", "err2"] };
    const result = extractValidationResult(data);
    expect(result).toEqual({ is_valid: false, errors: ["err1", "err2"] });
  });

  it("returns null for null data", () => {
    expect(extractValidationResult(null)).toBeNull();
  });

  it("returns null when is_valid is not boolean", () => {
    expect(extractValidationResult({ is_valid: "yes", errors: [] })).toBeNull();
  });

  it("returns null when errors is not an array", () => {
    expect(extractValidationResult({ is_valid: true, errors: "not-array" })).toBeNull();
  });
});
