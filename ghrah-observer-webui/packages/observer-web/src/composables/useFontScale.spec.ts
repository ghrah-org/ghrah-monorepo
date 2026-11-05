// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";

const STORAGE_KEY = "ghrah-font-scale";

async function loadModule() {
  vi.resetModules();
  return await import("./useFontScale");
}

function appliedScale() {
  return document.documentElement.style.getPropertyValue("--font-scale");
}

describe("useFontScale", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.removeProperty("--font-scale");
    vi.unstubAllGlobals();
  });

  it("defaults to 100% when storage is empty and does not write the key", async () => {
    const { initFontScale, fontScale } = await loadModule();
    initFontScale();
    expect(fontScale.value).toBe(1);
    expect(appliedScale()).toBe("1");
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it.each(["", "   "])("treats blank storage value %j as 100%", async (value) => {
    localStorage.setItem(STORAGE_KEY, value);
    const { initFontScale, fontScale } = await loadModule();
    initFontScale();
    expect(fontScale.value).toBe(1);
    expect(appliedScale()).toBe("1");
  });

  it.each(["0.9", "1.1"])("restores stored scale %j", async (value) => {
    localStorage.setItem(STORAGE_KEY, value);
    const { initFontScale, fontScale } = await loadModule();
    initFontScale();
    expect(fontScale.value).toBe(Number(value));
    expect(appliedScale()).toBe(value);
  });

  it("falls back to 100% for a non-numeric stored value", async () => {
    localStorage.setItem(STORAGE_KEY, "abc");
    const { initFontScale, fontScale } = await loadModule();
    initFontScale();
    expect(fontScale.value).toBe(1);
    expect(appliedScale()).toBe("1");
  });

  it.each([
    ["0", 0.9],
    ["2", 1.1],
  ])("clamps stored value %j to %s", async (value, expected) => {
    localStorage.setItem(STORAGE_KEY, value);
    const { initFontScale, fontScale } = await loadModule();
    initFontScale();
    expect(fontScale.value).toBe(expected);
    expect(appliedScale()).toBe(String(expected));
  });

  it("persists and applies a scale set by the user", async () => {
    const { initFontScale, setFontScale, fontScale } = await loadModule();
    initFontScale();
    setFontScale(1.05);
    expect(fontScale.value).toBe(1.05);
    expect(appliedScale()).toBe("1.05");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1.05");
  });

  it("keeps the in-memory scale when storage access throws", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
    });
    const { initFontScale, setFontScale, fontScale } = await loadModule();
    expect(() => initFontScale()).not.toThrow();
    expect(fontScale.value).toBe(1);
    expect(appliedScale()).toBe("1");
    expect(() => setFontScale(1.1)).not.toThrow();
    expect(fontScale.value).toBe(1.1);
    expect(appliedScale()).toBe("1.1");
  });
});
