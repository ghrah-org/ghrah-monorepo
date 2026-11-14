// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_PROJECT_API_MODE, resolveProjectApiMode } from "./project-api.js";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("resolveProjectApiMode", () => {
  it("falls back to the compiled default when nothing is configured", () => {
    expect(resolveProjectApiMode()).toBe(DEFAULT_PROJECT_API_MODE);
  });

  it("honours VITE_GHRAH_PROJECT_API", () => {
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "ws");
    expect(resolveProjectApiMode()).toBe("ws");
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "rest");
    expect(resolveProjectApiMode()).toBe("rest");
  });

  it("ignores invalid values and falls back to the default", () => {
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "bogus");
    expect(resolveProjectApiMode()).toBe(DEFAULT_PROJECT_API_MODE);
  });

  it("honours the runtime global when no build-time value exists", () => {
    vi.stubGlobal("__GHRAH_PROJECT_API__", "ws");
    expect(resolveProjectApiMode()).toBe("ws");
  });

  it("prefers the build-time env over the runtime global", () => {
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "rest");
    vi.stubGlobal("__GHRAH_PROJECT_API__", "ws");
    expect(resolveProjectApiMode()).toBe("rest");
  });

  it("re-reads on every call (no cached decision)", () => {
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "ws");
    expect(resolveProjectApiMode()).toBe("ws");
    vi.stubEnv("VITE_GHRAH_PROJECT_API", "rest");
    expect(resolveProjectApiMode()).toBe("rest");
  });
});
