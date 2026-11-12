import { describe, expect, it } from "vitest";

import {
  AbilityAdminViewSchema,
  AbilityApiErrorResponseSchema,
  AbilityApiStatusSchema,
  AbilityMcpProbeRequestSchema,
  AbilityMcpProbeViewSchema,
  AbilityPackageVersionProfileViewSchema,
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

  it("parses a package version profile and rejects install-root fields", () => {
    const parsed = AbilityPackageVersionProfileViewSchema.parse({
      namespace: "ghrah",
      name: "fs",
      full_name: "ghrah.fs",
      version: "0.1.0",
      origin: "builtin",
      definition_origin: "source",
      lock_state: "builtin",
      operations: [
        {
          key: "read_file",
          title: "Read file",
          description: "Read a file",
          input_schema: { type: "object" },
          output_schema: { type: "object" },
          resources: {},
          permissions: {},
          execution: {
            mode: "request",
            timeout_ms: 30000,
            cancellation: "cooperative",
            on_revoke: "drain",
            retry: "never",
          },
        },
      ],
    });
    expect(parsed.format).toBe("ghrah.ability-control.package-version.v1");
    expect(parsed.definition_origin).toBe("source");
    expect(parsed.operations[0]?.entry).toBeNull();
    expect(parsed.mcp).toBeNull();
    expect(() =>
      AbilityPackageVersionProfileViewSchema.parse({
        namespace: "ghrah",
        name: "fs",
        full_name: "ghrah.fs",
        version: "0.1.0",
        origin: "builtin",
        lock_state: "builtin",
        root: "/home/user/.ghrah/ability",
      }),
    ).toThrow();
  });

  it("bounds the MCP probe timeout and keeps error in sync with ok", () => {
    expect(AbilityMcpProbeRequestSchema.parse({})).toEqual({
      timeout_ms: 5000,
      include_resources: false,
    });
    expect(() => AbilityMcpProbeRequestSchema.parse({ timeout_ms: 999 })).toThrow();
    expect(() => AbilityMcpProbeRequestSchema.parse({ timeout_ms: 10001 })).toThrow();
    expect(() =>
      AbilityMcpProbeRequestSchema.parse({ timeout_ms: 5000, url: "https://example.com" }),
    ).toThrow();

    const failed = AbilityMcpProbeViewSchema.parse({
      namespace: "mcp",
      name: "fetch",
      version: "1.0.0",
      server: "fetch",
      ok: false,
      phase: "connect",
      elapsed_ms: 20,
      tool_count: 0,
      resource_count: 0,
      error: { reason: "timeout", message: "probe timed out" },
    });
    expect(failed.transport).toBe("streamable_http");
    expect(failed.error?.reason).toBe("timeout");
    expect(() =>
      AbilityMcpProbeViewSchema.parse({
        namespace: "mcp",
        name: "fetch",
        version: "1.0.0",
        server: "fetch",
        ok: false,
        phase: "connect",
        elapsed_ms: 20,
        tool_count: 0,
        resource_count: 0,
      }),
    ).toThrow();
    expect(() =>
      AbilityMcpProbeViewSchema.parse({
        namespace: "mcp",
        name: "fetch",
        version: "1.0.0",
        server: "fetch",
        ok: false,
        phase: "connect",
        elapsed_ms: 20,
        tool_count: 0,
        resource_count: 0,
        error: { reason: "unknown_reason", message: "x" },
      }),
    ).toThrow();
  });
});
