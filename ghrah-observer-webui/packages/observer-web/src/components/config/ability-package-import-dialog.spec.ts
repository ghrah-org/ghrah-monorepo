// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { setAbilityControlClientFactory } from "@ghrah/observer-core";
import { AbilityPackageImportViewSchema } from "@ghrah/protocol";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AbilityPackageImportDialog from "./ability-package-import-dialog.vue";

function imported(overrides: Record<string, unknown> = {}) {
  return AbilityPackageImportViewSchema.parse({
    namespace: "acme",
    name: "demo",
    full_name: "acme.demo",
    version: "1.0.0",
    package_digest: "sha256:abc",
    operation_count: 1,
    skill_count: 0,
    archive_format: "zip",
    source_name: "demo.zip",
    ...overrides,
  });
}

function choose(wrapper: VueWrapper, file: File): Promise<void> {
  const input = wrapper.get("input[type='file']");
  Object.defineProperty(input.element, "files", { value: [file], configurable: true });
  return input.trigger("change");
}

describe("AbilityPackageImportDialog", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => setAbilityControlClientFactory(null));

  it("refuses an archive with an unsupported extension", async () => {
    const importPackage = vi.fn();
    setAbilityControlClientFactory(() => ({ importPackage }) as never);
    const wrapper = mount(AbilityPackageImportDialog, {
      props: { open: true, maxBytes: 1024 },
    });

    await choose(wrapper, new File(["x"], "notes.txt", { type: "text/plain" }));
    expect(wrapper.text()).toContain("Only .zip archives are supported.");
    expect(wrapper.get(".import-actions button:last-child").attributes("disabled")).toBeDefined();
    expect(importPackage).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("refuses a file over the size limit", async () => {
    const importPackage = vi.fn();
    setAbilityControlClientFactory(() => ({ importPackage }) as never);
    const wrapper = mount(AbilityPackageImportDialog, {
      props: { open: true, maxBytes: 8 },
    });

    await choose(wrapper, new File(["x".repeat(64)], "big.zip", { type: "application/zip" }));
    expect(wrapper.text()).toContain("This file is larger than");
    expect(importPackage).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("uploads a valid archive and reports the installed package", async () => {
    const importPackage = vi.fn(async (_body: unknown, _options?: unknown) => imported());
    setAbilityControlClientFactory(
      () =>
        ({
          importPackage,
          status: async () => ({
            ability_source: "builtin",
            legacy_deprecated: false,
            features: {
              tree: true,
              grants_write: true,
              versions_read: true,
              versions_write: false,
              packages_read: true,
              packages_install: true,
              mcp_probe: true,
              surface_write: false,
            },
          }),
          packageGroups: async () => ({
            format: "ghrah.ability-control.package-groups.v1",
            scope: "distribution",
            installed_available: true,
            project_id: null,
            agent_id: null,
            groups: [],
          }),
        }) as never,
    );
    const wrapper = mount(AbilityPackageImportDialog, {
      props: { open: true, maxBytes: 1024 },
    });

    await choose(wrapper, new File(["zip-bytes"], "demo.zip", { type: "application/zip" }));
    expect(wrapper.text()).toContain("demo.zip");

    await wrapper.get(".import-actions button:last-child").trigger("click");
    await flushPromises();

    expect(importPackage).toHaveBeenCalledTimes(1);
    const body = importPackage.mock.calls[0][0] as unknown as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("demo.zip");
    expect(wrapper.emitted("installed")).toHaveLength(1);
    wrapper.unmount();
  });

  it("shows a fixed message when the server rejects the package", async () => {
    const importPackage = vi.fn(async () => {
      throw Object.assign(new Error("boom"), { code: "invalid_package" });
    });
    setAbilityControlClientFactory(
      () =>
        ({
          importPackage,
          status: async () => ({
            ability_source: "builtin",
            legacy_deprecated: false,
            features: {
              tree: true,
              grants_write: true,
              versions_read: true,
              versions_write: false,
              packages_read: true,
              packages_install: true,
              mcp_probe: true,
              surface_write: false,
            },
          }),
          packageGroups: async () => ({
            format: "ghrah.ability-control.package-groups.v1",
            scope: "distribution",
            installed_available: true,
            project_id: null,
            agent_id: null,
            groups: [],
          }),
        }) as never,
    );
    const wrapper = mount(AbilityPackageImportDialog, {
      props: { open: true, maxBytes: 1024 },
    });

    await choose(wrapper, new File(["zip-bytes"], "demo.zip", { type: "application/zip" }));
    await wrapper.get(".import-actions button:last-child").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Install failed.");
    expect(wrapper.emitted("installed")).toBeUndefined();
    wrapper.unmount();
  });
});
