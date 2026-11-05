// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from "vitest";
import { loadPlugin, type PluginHostApi, type LoadPluginOptions } from "./loader.js";
import { PluginRegistry } from "./registry.js";

/** 注入原生 import（vitest 的 vite-node 运行时无法执行构造器内的 import）。 */
const nativeImport = (url: string) => import(/* @vite-ignore */ url);

function makeApi(overrides: Partial<PluginHostApi> = {}): PluginHostApi {
  return {
    pluginId: "p1",
    version: "0.1.0",
    registry: new PluginRegistry(),
    registerBadgeRenderer: vi.fn(),
    ...overrides,
  };
}

const opts: LoadPluginOptions = { importModule: nativeImport };

describe("loadPlugin", () => {
  it("returns normalized error when import fails", async () => {
    const result = await loadPlugin("file:///definitely-missing-module-xyz.js", makeApi(), opts);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("import failed");
  });

  it("returns error when module lacks activate()", async () => {
    const url = "data:text/javascript,export const x = 1;";
    const result = await loadPlugin(url, makeApi(), opts);
    expect(result).toEqual({
      ok: false,
      pluginId: "p1",
      error: "module does not export activate()",
    });
  });

  it("calls activate with the host api on success", async () => {
    const url =
      "data:text/javascript,export const activate = (api) => { globalThis.__activatedApi = api; };";
    const api = makeApi();
    const holder = globalThis as { __activatedApi?: PluginHostApi };
    try {
      const result = await loadPlugin(url, api, opts);
      expect(result).toEqual({ ok: true, pluginId: "p1" });
      expect(holder.__activatedApi).toBe(api);
    } finally {
      delete holder.__activatedApi;
    }
  });

  it("returns normalized error when activate throws", async () => {
    const url = "data:text/javascript,export const activate = () => { throw new Error('nope'); };";
    const result = await loadPlugin(url, makeApi(), opts);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("activate() threw");
  });
});
