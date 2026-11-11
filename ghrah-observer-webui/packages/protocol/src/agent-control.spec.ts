import { describe, expect, it } from "vitest";

import {
  AbilityPackageCatalogViewSchema,
  AbilityPackageGroupsViewSchema,
} from "./ability-control.js";
import { AgentCreateRequestSchema, AgentSpecificationSummarySchema } from "./agent-control.js";

describe("agent control contracts", () => {
  it("defaults a new agent to stopped", () => {
    const created = AgentCreateRequestSchema.parse({
      expected_revision: 1,
      name: "reviewer",
      cluster_id: "clu",
    });
    expect(created.desired_state).toBe("stopped");
    expect(created.system_prompt).toBe("");
  });

  it("keeps the prompt body out of the list specification", () => {
    const summary = AgentSpecificationSummarySchema.parse({
      agent_id: "a1",
      name: "reviewer",
      cluster_id: "clu",
      prompt: { digest: "sha256:ab", size_bytes: 5 },
      desired_state: "stopped",
      runtime_status: "stopped",
      project_version: 2,
    });
    expect(summary.manifest_ref).toBe("");
    expect("system_prompt" in summary).toBe(false);
    expect(() =>
      AgentSpecificationSummarySchema.parse({
        agent_id: "a1",
        name: "reviewer",
        cluster_id: "clu",
        prompt: { digest: "sha256:ab", size_bytes: 5 },
        desired_state: "stopped",
        runtime_status: "stopped",
        project_version: 2,
        system_prompt: "secret",
      }),
    ).toThrow();
  });

  it("rejects an install root on the package catalog", () => {
    const catalog = AbilityPackageCatalogViewSchema.parse({ installed_available: false });
    expect(catalog.format).toBe("ghrah.ability-control.packages.v1");
    expect(catalog.packages).toEqual([]);
    expect(() =>
      AbilityPackageCatalogViewSchema.parse({ installed_available: false, root: "/tmp" }),
    ).toThrow();
    const groups = AbilityPackageGroupsViewSchema.parse({
      scope: "distribution",
      installed_available: false,
    });
    expect(groups.format).toBe("ghrah.ability-control.package-groups.v1");
    expect(groups.project_id).toBeNull();
    expect(() =>
      AbilityPackageGroupsViewSchema.parse({
        scope: "distribution",
        installed_available: false,
        root: "/tmp",
      }),
    ).toThrow();
  });
});
