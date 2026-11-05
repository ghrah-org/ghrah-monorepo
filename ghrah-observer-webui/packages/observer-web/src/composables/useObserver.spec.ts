import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../protocol/src/__fixtures__/manifest-responses.json";
import { useObserver } from "./useObserver";

vi.mock("./usePlugins", () => ({ usePlugins: vi.fn() }));

describe("manifest response integration", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    const observer = useObserver();
    observer.client.value = null;
    observer.error.value = null;
  });

  function clientStub() {
    const observer = useObserver();
    const client = {
      getAgent: vi.fn().mockResolvedValue({ success: true, data: fixture.agent }),
      putAgent: vi.fn().mockResolvedValue({ success: true, data: fixture.agent }),
      getAbility: vi.fn().mockResolvedValue({ success: true, data: fixture.ability }),
      putAbility: vi.fn().mockResolvedValue({ success: true, data: fixture.ability }),
      listManifestAbilities: vi
        .fn()
        .mockResolvedValue({ success: true, data: fixture.ability_list }),
      validateManifest: vi
        .fn()
        .mockResolvedValue({ success: true, data: fixture.validation_valid }),
      resolveAgent: vi.fn().mockResolvedValue({ success: true, data: fixture.resolved_agent }),
    };
    observer.client.value = client as unknown as NonNullable<typeof observer.client.value>;
    return { observer, client };
  }

  it("caches actual get/put detail shapes under the requested identity", async () => {
    const { observer } = clientStub();
    for (const method of [
      observer.getAgent,
      (name: string) => observer.putAgent(name, "input", true),
    ]) {
      expect((await method("audit.helper"))?.success).toBe(true);
      expect([...observer.manifests.agents.keys()]).toEqual(["audit.helper"]);
      expect(observer.manifests.agentDetails.get("audit.helper")).toMatchObject(fixture.agent);
    }
    for (const method of [
      observer.getAbility,
      (name: string) => observer.putAbility(name, "input", true),
    ]) {
      expect((await method("audit.fs.read_file"))?.success).toBe(true);
      expect([...observer.manifests.abilities.keys()]).toEqual(["audit.fs.read_file"]);
      expect(observer.manifests.abilityDetails.get("audit.fs.read_file")).toMatchObject(
        fixture.ability,
      );
    }
  });

  it("rejects a response belonging to another manifest without poisoning the cache", async () => {
    const { observer } = clientStub();
    expect((await observer.getAgent("other.agent"))?.success).toBe(false);
    expect(observer.manifests.agents.size).toBe(0);
    expect(observer.manifests.agentDetails.size).toBe(0);
  });

  it("forwards draft preview input without updating the manifest cache", async () => {
    const { observer, client } = clientStub();
    const result = await observer.resolveAgent("audit.helper", null, fixture.agent.source);
    expect(client.resolveAgent).toHaveBeenCalledWith("audit.helper", null, fixture.agent.source);
    expect(result?.data).toEqual(fixture.resolved_agent);
    expect(observer.manifests.agentDetails.size).toBe(0);
    expect(observer.manifests.agents.size).toBe(0);
  });

  it("stores corrupt-file errors separately from valid ability summaries", async () => {
    const { observer, client } = clientStub();
    const errors = [{ full_name: "audit.broken", error: "Invalid YAML" }];
    client.listManifestAbilities.mockResolvedValue({
      success: true,
      data: { ...fixture.ability_list, errors },
    });
    await observer.listManifestAbilities();
    expect([...observer.manifests.abilities.keys()]).toEqual(["audit.fs.read_file"]);
    expect(observer.manifests.abilityErrors).toEqual(errors);
    expect(observer.manifests.abilitiesLoading).toBe(false);
  });

  it.each([fixture.validation_valid, fixture.validation_invalid])(
    "stores the wire validation result %j",
    async (data) => {
      const { observer, client } = clientStub();
      client.validateManifest.mockResolvedValue({ success: true, data });
      await observer.validateManifest("input", "agent");
      expect(observer.manifests.validationResult).toEqual(data);
      expect(observer.manifests.isValidating).toBe(false);
    },
  );

  it("clears loading and reports a rejected request", async () => {
    const { observer, client } = clientStub();
    client.validateManifest.mockRejectedValue(new Error("Connection lost"));
    expect(await observer.validateManifest("input", "agent")).toBeNull();
    expect(observer.manifests.validationResult).toEqual({
      valid: false,
      errors: ["Connection lost"],
    });
    expect(observer.manifests.isValidating).toBe(false);
  });

  it("clears loading and reports disconnection", async () => {
    const observer = useObserver();
    expect(await observer.validateManifest("input", "agent")).toBeNull();
    expect(observer.manifests.validationResult?.valid).toBe(false);
    expect(observer.manifests.validationResult?.errors).toHaveLength(1);
    expect(observer.manifests.isValidating).toBe(false);
  });

  it("reports malformed success payloads instead of leaving validation empty", async () => {
    const { observer, client } = clientStub();
    client.validateManifest.mockResolvedValue({
      success: true,
      data: { is_valid: true, errors: [] },
    });
    expect((await observer.validateManifest("input", "agent"))?.success).toBe(false);
    expect(observer.manifests.validationResult?.valid).toBe(false);
    expect(observer.manifests.validationResult?.errors).toHaveLength(1);
    expect(observer.manifests.isValidating).toBe(false);
  });
});

it("passes Ability source hashes through the Observer wrapper", async () => {
  const observer = useObserver();
  const putAbility = vi.fn().mockResolvedValue({ success: true, data: fixture.ability });
  observer.client.value = { putAbility } as unknown as NonNullable<typeof observer.client.value>;
  await observer.putAbility(
    "audit.fs.read_file",
    fixture.ability.source,
    true,
    fixture.ability.source_hash,
  );
  expect(putAbility).toHaveBeenCalledWith(
    "audit.fs.read_file",
    fixture.ability.source,
    true,
    fixture.ability.source_hash,
  );
});
