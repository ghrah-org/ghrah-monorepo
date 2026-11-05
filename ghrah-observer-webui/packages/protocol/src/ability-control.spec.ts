import { describe, expect, it } from "vitest";

import {
  AbilityAdminViewSchema,
  AbilityApiErrorResponseSchema,
  AbilityApiStatusSchema,
  AbilityTreeChangeResultSchema,
} from "./ability-control.js";

const tree = {
  format: "ghrah.ability-tree.v2",
  project_id: "project",
  agent_id: "alice",
  run_id: "run-1",
  revision: 2,
  abilities: [],
  bound_tools: [],
  runtime: null,
};

describe("ability control contracts", () => {
  it("parses status with defaults and marks legacy", () => {
    const parsed = AbilityApiStatusSchema.parse({
      ability_source: "legacy",
      legacy_deprecated: true,
    });
    expect(parsed.legacy_deprecated).toBe(true);
    expect(parsed.features.grants_write).toBe(true);
    expect(parsed.features.packages_install).toBe(false);
  });

  it("accepts the minimal admin projection and keeps null runtimes explicit", () => {
    const parsed = AbilityAdminViewSchema.parse({
      format: "ghrah.ability-control.admin.v1",
      project_id: "project",
      agent_id: "alice",
      revision: 2,
      run_id: "run-1",
      lifecycle: { state: "stopped" },
      grant_policy: null,
      grants: [],
      bound_tools: [],
      surface: { mode: "auto", static: [], effective: [] },
      changes_cursor: 0,
    });
    expect(parsed.surface.current_runtime).toBeNull();
    expect(parsed.surface.pending_respawn.added).toEqual([]);
  });

  it("rejects unknown fields and malformed change results", () => {
    expect(() =>
      AbilityApiStatusSchema.parse({ ability_source: "builtin", extra: true }),
    ).toThrow();
    expect(() =>
      AbilityTreeChangeResultSchema.parse({
        format: "ghrah.ability-control.change.v1",
        view: tree,
        revision: 2,
        error: null,
      }),
    ).toThrow();
  });

  it("requires an error code in the REST error envelope", () => {
    expect(
      AbilityApiErrorResponseSchema.parse({
        error: { code: "conflict", message: "revision changed" },
      }).error.code,
    ).toBe("conflict");
    expect(() => AbilityApiErrorResponseSchema.parse({ error: { message: "no code" } })).toThrow();
  });
});
