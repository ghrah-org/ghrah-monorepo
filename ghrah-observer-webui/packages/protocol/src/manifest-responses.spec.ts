import { describe, expect, it } from "vitest";
import fixture from "./__fixtures__/manifest-responses.json";
import {
  ManifestGetAbilityResponsePayloadSchema,
  ManifestGetAgentResponsePayloadSchema,
  ManifestListAbilitiesResponsePayloadSchema,
  ManifestPutPayloadSchema,
  ManifestValidateResponsePayloadSchema,
  ManifestResolvePayloadSchema,
  ManifestResolveAgentResponsePayloadSchema,
} from "./payloads/manifest.js";

describe("Manifest response alignment", () => {
  it("reads the same complete response fixtures as Python", () => {
    expect(ManifestGetAbilityResponsePayloadSchema.parse(fixture.ability)).toEqual(fixture.ability);
    expect(ManifestGetAgentResponsePayloadSchema.parse(fixture.agent)).toEqual(fixture.agent);
    expect(ManifestListAbilitiesResponsePayloadSchema.parse(fixture.ability_list)).toEqual(
      fixture.ability_list,
    );
    expect(ManifestResolvePayloadSchema.parse(fixture.resolve_request)).toEqual(
      fixture.resolve_request,
    );
    expect(ManifestResolveAgentResponsePayloadSchema.parse(fixture.resolved_agent)).toEqual(
      fixture.resolved_agent,
    );
    for (const data of [fixture.validation_valid, fixture.validation_invalid]) {
      expect(ManifestValidateResponsePayloadSchema.parse(data)).toEqual(data);
    }
  });

  it("keeps broken-file errors separate and rejects the wrong validation field", () => {
    const payload = {
      abilities: [],
      errors: [{ full_name: "audit.broken", error: "Invalid YAML" }],
    };
    expect(ManifestListAbilitiesResponsePayloadSchema.parse(payload)).toEqual(payload);
    expect(
      ManifestValidateResponsePayloadSchema.safeParse({ is_valid: true, errors: [] }).success,
    ).toBe(false);
  });

  it("keeps the optimistic concurrency field optional on writes", () => {
    expect(ManifestPutPayloadSchema.parse({ full_name: "audit.helper", content: "x" })).toEqual({
      full_name: "audit.helper",
      content: "x",
      overwrite: false,
    });
  });
});
