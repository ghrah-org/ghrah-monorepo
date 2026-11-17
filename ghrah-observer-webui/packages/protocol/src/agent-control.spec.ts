import { describe, expect, it } from "vitest";

import {
  AbilityPackageCatalogViewSchema,
  AbilityPackageGroupsViewSchema,
} from "./ability-control.js";
import {
  AgentCreateRequestSchema,
  AgentSpecificationSummarySchema,
  AgentTemplateCreateRequestSchema,
  AgentTemplateDetailViewSchema,
  AgentTemplateMetadataViewSchema,
  AgentTemplateOperationViewSchema,
  AgentTemplatePackageViewSchema,
  AgentTemplatePathRequestViewSchema,
  AgentTemplateUpdateRequestSchema,
} from "./agent-control.js";

describe("agent control contracts", () => {
  it("defaults a new agent to stopped", () => {
    const created = AgentCreateRequestSchema.parse({
      expected_revision: 1,
      name: "reviewer",
    });
    expect(created.desired_state).toBe("stopped");
    expect(created.system_prompt).toBe("");
  });

  it("keeps the prompt body out of the list specification", () => {
    const summary = AgentSpecificationSummarySchema.parse({
      agent_id: "a1",
      name: "reviewer",
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

describe("agent template contracts", () => {
  const metadata = { namespace: "writing", name: "weekly_report" };

  it("defaults a sparse template draft", () => {
    const created = AgentTemplateCreateRequestSchema.parse({ metadata });
    expect(created.metadata.title).toBe("");
    expect(created.metadata.tags).toEqual([]);
    expect(created.metadata.deprecated).toBe(false);
    expect(created.model).toBeNull();
    expect(created.prompt).toBeNull();
    expect(created.context_policy).toBeNull();
    expect(created.path_request).toEqual([]);
  });

  it("defaults context policy to unlimited iterations", () => {
    const detail = AgentTemplateDetailViewSchema.parse({
      template_id: "tpl-1",
      revision: 1,
      metadata,
      model: {},
      prompt: {},
      context_policy: {},
      ability_request: {},
      permission_request: {},
      created_at: "2026-11-17T00:00:00Z",
      updated_at: "2026-11-17T00:00:00Z",
    });
    expect(detail.format).toBe("ghrah.agent-templates.v1");
    expect(detail.context_policy.max_iterations).toBe(-1);
    expect(detail.context_policy.communication_timeout).toBe(300);
    expect(detail.ability_request.surface.mode).toBe("auto");
    expect(detail.permission_request.require_approval_by_default).toBe(true);
    expect(detail.archived_at).toBeNull();
    expect(detail.reference_count).toBe(0);
    expect(detail.path_request).toEqual([]);
  });

  it("rejects unknown fields on template contracts", () => {
    expect(() => AgentTemplateCreateRequestSchema.parse({ metadata, unknown: true })).toThrow();
    expect(() => AgentTemplateMetadataViewSchema.parse({ ...metadata, unknown: 1 })).toThrow();
    expect(() =>
      AgentTemplateMetadataViewSchema.parse({ namespace: "Writing", name: "weekly_report" }),
    ).toThrow();
  });

  it("rejects whole_container combined with member applications", () => {
    expect(() =>
      AgentTemplatePackageViewSchema.parse({
        package: "writing.weekly_report",
        origin: "builtin",
        whole_container: true,
        operations: [{ key: "generate" }],
      }),
    ).toThrow();
    expect(
      AgentTemplatePackageViewSchema.parse({
        package: "writing.weekly_report",
        origin: "builtin",
        whole_container: true,
      }).whole_container,
    ).toBe(true);
    expect(() =>
      AgentTemplateOperationViewSchema.parse({ key: "generate", visible: false, available: true }),
    ).toThrow();
  });

  it("rejects path requests whose scope and package disagree", () => {
    const base = { workspace_role: "default", subpath: "docs", access: "read", deny: false };
    expect(
      AgentTemplatePathRequestViewSchema.parse({ ...base, scope: "agent" }).package,
    ).toBeNull();
    expect(() =>
      AgentTemplatePathRequestViewSchema.parse({
        ...base,
        scope: "package",
        package: null,
      }),
    ).toThrow();
    expect(() =>
      AgentTemplatePathRequestViewSchema.parse({
        ...base,
        scope: "agent",
        package: "writing.weekly_report",
      }),
    ).toThrow();
    // subpath 的 `..`/绝对路径检查与「update 至少一个可写字段」由 Python 侧
    // field_validator / model_validator 承担：JSON Schema 表达不了，生成物不复制。
  });

  it("keeps omitted update groups untouched", () => {
    const patch = AgentTemplateUpdateRequestSchema.parse({ expected_revision: 2, title: "周报" });
    expect(patch.title).toBe("周报");
    expect(patch.prompt).toBeNull();
    expect(patch.ability_request).toBeNull();
    expect(patch.path_request).toBeNull();
  });
});
