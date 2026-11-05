import { describe, expect, it } from "vitest";
import {
  applyAgentManifestForm,
  dumpAgentManifestForm,
  parseAgentManifestForm,
  parseManifestObject,
} from "./agent-manifest-form";

const completeSource = `# author comment
manifest: agent
version: "2"
metadata:
  namespace: audit
  name: helper
  title: Helper
  description: Display description
  tags: [helper, audit]
  icon: heroicons.book
  deprecated: true
model:
  endpoint_name: test-model
  temperature: 0.2
  max_tokens: 256
  top_p: 0.9
  top_k: 4
  extra_params:
    seed: 42
system_prompt: |
  Keep the entire prompt.
max_iterations: 12
communication_timeout: 120.5
description: Runtime description
abilities:
  - ref: audit.fs.read_file
    permissions:
      require_hitl: true
      fs_read_only: true
      fs_write: false
      net_access: false
      shell_access: false
      allowed_paths: [/workspace/docs, /workspace/reports]
      denied_paths: [/secret]
      allowed_commands: [rg]
      denied_commands: [rm]
context:
  window:
    max_tokens: 4000
    strategies: [sliding, compact]
    tool_call_max_length: 1000
    sliding_window_size: 30
    compact_threshold: 0.8
    compact_keep_recent: 10
    compact_method: summary
  persistence:
    type: sqlite
    compress: true
    auto_persist: true
    snapshot_interval: 60
hooks:
  before_action:
    - type: python
      handler: audit.before
      source: hooks/before.py
      params:
        mode: strict
  on_error:
    - type: builtin
      handler: log_error
future:
  nested:
    keep: true
`;

describe("Agent Manifest structured form conversion", () => {
  it("maps every editable Agent Manifest field", () => {
    const form = parseAgentManifestForm(completeSource);

    expect(form.namespace).toBe("audit");
    expect(form.name).toBe("helper");
    expect(form.title).toBe("Helper");
    expect(form.metadataDescription).toBe("Display description");
    expect(form.runtimeDescription).toBe("Runtime description");
    expect(form.tags).toBe("helper, audit");
    expect(form.icon).toBe("heroicons.book");
    expect(form.deprecated).toBe(true);
    expect(form.endpoint_name).toBe("test-model");
    expect(form.temperature).toBe("0.2");
    expect(form.max_tokens).toBe("256");
    expect(form.top_p).toBe("0.9");
    expect(form.top_k).toBe("4");
    expect(JSON.parse(form.extra_params)).toEqual({ seed: 42 });
    expect(form.communication_timeout).toBe("120.5");
    expect(form.abilities[0]).toMatchObject({
      mode: "ref",
      value: "audit.fs.read_file",
    });
    expect(form.abilities[0].permissions).toMatchObject({
      enabled: true,
      require_hitl: true,
      allowed_paths: "/workspace/docs, /workspace/reports",
      denied_commands: "rm",
    });
    expect(form.contextEnabled).toBe(true);
    expect(form.windowEnabled).toBe(true);
    expect(form.window).toMatchObject({
      max_tokens: "4000",
      strategies: "sliding, compact",
      compact_threshold: "0.8",
      compact_method: "summary",
    });
    expect(form.persistenceEnabled).toBe(true);
    expect(form.persistence).toMatchObject({
      type: "sqlite",
      compress: true,
      snapshot_interval: "60",
    });
    expect(form.hooks.before_action[0]).toMatchObject({
      type: "python",
      handler: "audit.before",
      source: "hooks/before.py",
    });
    expect(JSON.parse(form.hooks.before_action[0].params)).toEqual({ mode: "strict" });
  });

  it("applies a structured edit to the parsed YAML object and preserves unknown fields", () => {
    const form = parseAgentManifestForm(completeSource);
    form.title = "Updated";
    form.top_p = "0.75";
    form.communication_timeout = "180";
    form.abilities[0].permissions.allowed_paths = "/workspace/only";
    form.window.compact_keep_recent = "12";
    form.hooks.on_error[0].handler = "structured_logger";

    const next = parseManifestObject(applyAgentManifestForm(completeSource, form));

    expect(next.future).toEqual({ nested: { keep: true } });
    expect(next.metadata).toMatchObject({ title: "Updated", description: "Display description" });
    expect(next.model).toMatchObject({ top_p: 0.75 });
    expect(next.communication_timeout).toBe(180);
    expect(next.description).toBe("Runtime description");
    expect(
      (next.abilities as Array<{ permissions?: { allowed_paths?: string[] } }>)[0].permissions
        ?.allowed_paths,
    ).toEqual(["/workspace/only"]);
    expect(
      (next.context as { window?: { compact_keep_recent?: number } }).window?.compact_keep_recent,
    ).toBe(12);
    expect((next.hooks as { on_error?: Array<{ handler?: string }> }).on_error?.[0]?.handler).toBe(
      "structured_logger",
    );
  });

  it("serializes all required fields for a new manifest", () => {
    const form = parseAgentManifestForm(completeSource);
    form.namespace = "new";
    form.name = "agent";
    form.endpoint_name = "local-model";
    const source = dumpAgentManifestForm(form);
    const parsed = parseManifestObject(source);

    expect(parsed.manifest).toBe("agent");
    expect(parsed.version).toBe("2");
    expect(parsed.metadata).toMatchObject({ namespace: "new", name: "agent" });
    expect(parsed.model).toMatchObject({ endpoint_name: "local-model" });
    expect(parsed.abilities).toHaveLength(1);
    expect(source.endsWith("\n")).toBe(true);
  });

  it("rejects malformed YAML and malformed JSON without producing a source", () => {
    expect(() => parseAgentManifestForm("manifest: [")).toThrow();
    expect(() => parseManifestObject("- one")).toThrow();

    const form = parseAgentManifestForm(completeSource);
    form.extra_params = "{invalid";
    expect(() => dumpAgentManifestForm(form)).toThrow(/extra_params/);
    form.extra_params = "{}";
    form.hooks.on_error[0].params = "[]";
    expect(() => applyAgentManifestForm(completeSource, form)).toThrow(/hooks\.on_error\.params/);
  });
});
