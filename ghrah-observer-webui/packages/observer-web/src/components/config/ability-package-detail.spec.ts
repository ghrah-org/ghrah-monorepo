// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { VersionProfileRecord } from "@ghrah/observer-core";
import {
  AbilityPackageVersionProfileViewSchema,
  type AbilityPackageVersionProfileView,
} from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AbilityPackageDetail from "./ability-package-detail.vue";

function profile(overrides: Record<string, unknown> = {}): AbilityPackageVersionProfileView {
  return AbilityPackageVersionProfileViewSchema.parse({
    namespace: "sample",
    name: "reader",
    full_name: "sample.reader",
    version: "1.0.0",
    origin: "builtin",
    lock_state: "builtin",
    ...overrides,
  });
}

function loaded(
  view: AbilityPackageVersionProfileView,
  extra: Partial<VersionProfileRecord> = {},
): VersionProfileRecord {
  return {
    key: `${view.full_name}@${view.version}`,
    profile: view,
    loading: false,
    failed: false,
    errorMessage: null,
    errorCode: null,
    loadedAt: 1,
    ...extra,
  };
}

const operation = {
  key: "read_file",
  title: "Read file",
  description: "Read one file",
  input_schema: { type: "object", required: ["path"] },
  output_schema: { type: "string" },
  effects: ["read"],
  tags: [],
  resources: {},
  permissions: {},
  expose: false,
  execution: {
    mode: "request",
    timeout_ms: 1500,
    cancellation: "cooperative",
    on_revoke: "drain",
    retry: "never",
  },
  entry: {
    path: "tools/read.md",
    media_type: "text/markdown",
    size: 12,
    digest: "abc",
  },
  mcp: { server: "notes", tool: "read" },
};

const skill = {
  key: "guide",
  title: "Guide",
  description: "How to use the package",
  tags: [],
  body: {
    path: "skills/guide.md",
    media_type: "text/markdown",
    size: 20,
    digest: "def",
  },
  assets: [
    {
      key: "logo",
      path: "skills/guide/assets/logo.png",
      media_type: "image/png",
      size: 4,
      digest: "aaa",
    },
  ],
  references: [],
  scripts: [],
};

describe("AbilityPackageDetail", () => {
  it("renders four sections, empty members, and a missing MCP fact", () => {
    const wrapper = mount(AbilityPackageDetail, {
      props: { record: loaded(profile()), mcpProbeEnabled: false },
    });
    expect(wrapper.text()).toContain("Tools");
    expect(wrapper.text()).toContain("Skills and resources");
    expect(wrapper.text()).toContain("MCP");
    expect(wrapper.text()).toContain("Verification");
    expect(wrapper.text()).toContain("This version declares no operations.");
    expect(wrapper.text()).toContain("This version declares no skills.");
    expect(wrapper.text()).toContain("This version does not say whether it is an MCP package.");
    expect(wrapper.text()).toContain("No install verification is recorded for this version.");
    expect(wrapper.text()).not.toContain("This version is not an MCP package.");
    expect(wrapper.findAll("button").some((button) => button.text() === "Test connection")).toBe(
      false,
    );
    wrapper.unmount();
  });

  it("shows operation and skill members without an install root", () => {
    const wrapper = mount(AbilityPackageDetail, {
      props: {
        record: loaded(
          profile({
            definition_origin: "source",
            operations: [operation],
            skills: [skill],
          }),
        ),
        mcpProbeEnabled: true,
      },
    });
    const text = wrapper.text();
    expect(text).toContain("read_file");
    expect(text).toContain("Read file");
    expect(text).toContain("Input: object, required path");
    expect(text).toContain("Output: string");
    expect(text).toContain("Effects: read");
    expect(text).toContain("Not exposed");
    expect(text).toContain("timeout 1500 ms");
    expect(text).toContain("tools/read.md");
    expect(text).toContain("MCP tool read on notes");
    expect(text).toContain("skills/guide.md");
    expect(text).toContain("skills/guide/assets/logo.png");
    expect(text).toContain("No references.");
    expect(text).toContain("No scripts.");
    expect(text).toContain("This version is not an MCP package.");
    expect(text).not.toContain("/home/");
    expect(text).not.toContain("http://");
    wrapper.unmount();
  });

  it("shows an install snapshot and keeps connection tests unlabeled when the gate is closed", () => {
    const wrapper = mount(AbilityPackageDetail, {
      props: {
        record: loaded(
          profile({
            definition_origin: "mcp",
            mcp: {
              server: "notes",
              fetched_at: "2026-11-12T00:00:00Z",
              stale: true,
              last_error: "timed out",
            },
          }),
        ),
        mcpProbeEnabled: false,
      },
    });
    const text = wrapper.text();
    expect(text).toContain("Server notes");
    expect(text).toContain("Fetched 2026-11-12T00:00:00Z");
    expect(text).toContain("The snapshot is stale.");
    expect(text).toContain("Last error: timed out");
    expect(text).toContain("Connection tests are not enabled.");
    expect(text).not.toContain("This version is not an MCP package.");
    expect(wrapper.findAll("button").some((button) => button.text() === "Test connection")).toBe(
      false,
    );
    wrapper.unmount();
  });

  it("renders a verification record and omits empty path lists", () => {
    const wrapper = mount(AbilityPackageDetail, {
      props: {
        record: loaded(
          profile({
            verify: {
              ok: false,
              code: "digest_mismatch",
              message: "bytes differ",
              package_digest: "aaa",
              actual_digest: "bbb",
              added: ["skills/guide.md"],
              removed: [],
              changed: ["tools/read.md"],
            },
          }),
        ),
        mcpProbeEnabled: true,
      },
    });
    const text = wrapper.text();
    expect(text).toContain("Verification failed.");
    expect(text).toContain("Code digest_mismatch");
    expect(text).toContain("bytes differ");
    expect(text).toContain("Package digest aaa");
    expect(text).toContain("Actual digest bbb");
    expect(text).toContain("Added");
    expect(text).toContain("skills/guide.md");
    expect(text).toContain("Changed");
    expect(text).toContain("tools/read.md");
    expect(text).not.toContain("Removed");
    wrapper.unmount();
  });

  it("maps a not-found failure to fixed copy and asks to retry", async () => {
    const wrapper = mount(AbilityPackageDetail, {
      props: {
        record: loaded(profile(), {
          profile: null,
          failed: true,
          errorCode: "not_found",
          errorMessage: "missing /home/secret",
          loadedAt: null,
        }),
        mcpProbeEnabled: false,
      },
    });
    expect(wrapper.text()).toContain("This version is not in the distribution.");
    expect(wrapper.text()).not.toContain("/home/secret");
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
    wrapper.unmount();
  });
});
