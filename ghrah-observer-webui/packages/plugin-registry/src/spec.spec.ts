// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { TsHalfReport } from "@ghrah/protocol";
import { describe, expect, it } from "vitest";
import { parseImportMap } from "./import-map.js";
import { parseManifest } from "./manifest.js";
import { applyNegotiationResult, buildTsHalfReports } from "./negotiator.js";
import { PluginRegistry } from "./registry.js";
import { toWireReport, TsHalfSpecSchema, type TsHalfSpec } from "./spec.js";

const baseSpec: TsHalfSpec = {
  plugin_id: "task-commit-attribution",
  version: "0.1.0",
  provides: {
    badge_renderers: ["git_commit"],
    view_renderers: [],
    commands: [],
    capabilities: [],
  },
  requires: { host_capability: [] },
  ui_slots: ["badge"],
  entry: "index.js",
};

describe("TsHalfSpecSchema", () => {
  it("accepts a valid spec", () => {
    expect(TsHalfSpecSchema.parse(baseSpec)).toEqual(baseSpec);
  });

  it("rejects unknown extra fields", () => {
    expect(() => TsHalfSpecSchema.parse({ ...baseSpec, extra: 1 })).toThrow();
  });

  it("rejects missing entry / empty ids", () => {
    const { entry: _entry, ...noEntry } = baseSpec;
    expect(() => TsHalfSpecSchema.parse(noEntry)).toThrow();
    expect(() => TsHalfSpecSchema.parse({ ...baseSpec, plugin_id: "" })).toThrow();
  });
});

describe("toWireReport flattening", () => {
  it("flattens provides with domain prefixes and raw capabilities", () => {
    const report = toWireReport({
      ...baseSpec,
      provides: {
        badge_renderers: ["git_commit", "lint_report"],
        view_renderers: ["board"],
        commands: ["commit.attach"],
        capabilities: ["node-assembler", "evidence:git"],
      },
    });
    expect(report).toEqual({
      plugin_id: "task-commit-attribution",
      version: "0.1.0",
      provides: [
        "badge-renderer/git_commit",
        "badge-renderer/lint_report",
        "view-renderer/board",
        "command/commit.attach",
        "node-assembler",
        "evidence:git",
      ],
    } satisfies TsHalfReport);
  });

  it("keeps declaration order stable and omits nothing else", () => {
    const report = toWireReport(baseSpec);
    expect(report.provides).toEqual(["badge-renderer/git_commit"]);
  });

  /**
   * 拍平约定测试：与 Python `python_half_from_spec`（ghrah-plugin
   * negotiator.py:75-92）同构 —— `<domain>/<name>` 前缀 + capabilities 原文，
   * 且 wire 侧 TsHalfReport 只有 plugin_id/version/provides 三字段。
   */
  it("mirrors the Python provides-flattening convention", () => {
    const spec: TsHalfSpec = {
      ...baseSpec,
      provides: {
        badge_renderers: ["b1"],
        view_renderers: ["v1"],
        commands: ["c1"],
        capabilities: ["cap-x"],
      },
    };
    const flattened = toWireReport(spec).provides;
    // capabilities 原文（无前缀），可参与 capability 闭环
    expect(flattened).toContain("cap-x");
    // 每个非 capability 条目都带 `<domain>/` 前缀，域内名称原样保留
    for (const entry of flattened.filter((item) => item !== "cap-x")) {
      expect(entry).toMatch(/^[a-z-]+\/.+$/);
    }
    // 与 Python 约定同构：命令域前缀一致
    expect(flattened).toContain("command/c1");
  });
});

describe("manifest + host capability", () => {
  const manifest = parseManifest({
    plugins: [baseSpec],
    urls: { "task-commit-attribution": "/plugins/task-commit-attribution/0.1.0/index.js" },
  });

  it("parses manifest with defaults for missing collections", () => {
    expect(parseManifest({})).toEqual({ plugins: [], urls: {} });
    expect(manifest.plugins).toHaveLength(1);
  });

  it("rejects manifest plugins with invalid spec", () => {
    expect(() => parseManifest({ plugins: [{ plugin_id: "x" }] })).toThrow();
  });

  it("accepts spec host_capability satisfied by host", () => {
    const spec: TsHalfSpec = {
      ...baseSpec,
      requires: { host_capability: ["node-assembler"] },
    };
    const registry = new PluginRegistry();
    expect(registry.register(spec)).toBe(true);
    expect(registry.state.rejected).toEqual([]);
  });

  it("rejects spec whose host_capability is missing", () => {
    const spec: TsHalfSpec = {
      ...baseSpec,
      requires: { host_capability: ["webview-sandbox"] },
    };
    const registry = new PluginRegistry();
    expect(registry.register(spec)).toBe(false);
    expect(registry.state.rejected).toEqual(["task-commit-attribution"]);
  });
});

describe("registry buckets", () => {
  it("declares badge/view/commands buckets and resolves only registered components", () => {
    const registry = new PluginRegistry();
    registry.register(baseSpec);
    expect(registry.hasBadge("git_commit")).toBe(true);
    expect(registry.resolveBadge("git_commit")).toBeNull();
    const component = { render: () => null };
    registry.registerBadgeRenderer("git_commit", component);
    expect(registry.resolveBadge("git_commit")).toBe(component);
    expect(registry.hasBadge("unknown")).toBe(false);
    expect(registry.commandOwner("cmd")).toBeNull();
  });

  it("clear() resets buckets and rejections", () => {
    const registry = new PluginRegistry();
    registry.register(baseSpec);
    registry.clear();
    expect(registry.hasBadge("git_commit")).toBe(false);
    expect(registry.state.specs.size).toBe(0);
  });
});

describe("negotiation", () => {
  const manifest = parseManifest({
    plugins: [
      baseSpec,
      { ...baseSpec, plugin_id: "no-url-plugin" },
      { ...baseSpec, plugin_id: "stale-plugin", version: "9.9.9" },
    ],
    urls: {
      "task-commit-attribution": "/plugins/task-commit-attribution/0.1.0/index.js",
      "stale-plugin": "/plugins/stale-plugin/9.9.9/index.js",
    },
  });

  it("builds wire reports for all installed plugins", () => {
    const reports = buildTsHalfReports(manifest);
    expect(reports.map((report) => report.plugin_id)).toEqual([
      "task-commit-attribution",
      "no-url-plugin",
      "stale-plugin",
    ]);
    expect(reports[0]).toEqual({
      plugin_id: "task-commit-attribution",
      version: "0.1.0",
      provides: ["badge-renderer/git_commit"],
    } satisfies TsHalfReport);
  });

  it("applies result: matched∩manifest → toLoad, missing url flagged, conflicts listed", () => {
    const outcome = applyNegotiationResult(
      {
        matched: [
          { plugin_id: "task-commit-attribution", version: "0.1.0", provides: [], instances: [] },
          { plugin_id: "no-url-plugin", version: "0.1.0", provides: [], instances: [] },
        ],
        python_only: [],
        ts_only: [],
        version_conflicts: [
          { plugin_id: "stale-plugin", python_version: "0.2.0", ts_version: "9.9.9" },
        ],
        missing_capabilities: [],
        instances: {},
      },
      manifest,
    );
    expect(outcome.toLoad.map((item) => item.spec.plugin_id)).toEqual(["task-commit-attribution"]);
    expect(outcome.matchedWithoutUrl).toEqual(["no-url-plugin"]);
  });
});

describe("parseImportMap", () => {
  it("parses imports and defaults empty", () => {
    expect(parseImportMap({ imports: { vue: "/plugins/shared/vue.js" } })).toEqual({
      imports: { vue: "/plugins/shared/vue.js" },
    });
    expect(parseImportMap({})).toEqual({ imports: {} });
    expect(() => parseImportMap({ imports: "nope" })).toThrow();
  });
});
