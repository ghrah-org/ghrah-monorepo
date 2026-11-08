// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { AbilityApiStatusSchema } from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAbilityControlClientFactory } from "../ability-client.js";
import type { AbilityControlClient } from "../ability-control.js";
import { useAbilityPackagesStore } from "./ability-packages.js";

const builtin = AbilityApiStatusSchema.parse({
  ability_source: "builtin",
  legacy_deprecated: false,
  features: {
    tree: true,
    grants_write: true,
    versions_read: false,
    versions_write: false,
    packages_read: false,
    packages_install: false,
    surface_write: false,
  },
});

describe("useAbilityPackagesStore", () => {
  beforeEach(() => setActivePinia(createPinia()));
  afterEach(() => setAbilityControlClientFactory(null));

  it("reads status once while visible and again after the catalog is marked stale", async () => {
    const status = vi.fn(async () => builtin);
    setAbilityControlClientFactory(() => ({ status }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    store.setSettingsVisible(true);
    await vi.waitFor(() => expect(store.loadedAt).not.toBeNull());
    expect(status).toHaveBeenCalledTimes(1);
    store.setAssemblyVisible(true);
    expect(status).toHaveBeenCalledTimes(1);
    await store.markStale();
    expect(status).toHaveBeenCalledTimes(2);
    expect(store.status?.ability_source).toBe("builtin");
    expect(store.stale).toBe(false);
  });

  it("keeps a hidden catalog stale until the settings page is shown again", async () => {
    const status = vi.fn(async () => builtin);
    setAbilityControlClientFactory(() => ({ status }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    await store.markStale();
    expect(status).not.toHaveBeenCalled();
    expect(store.stale).toBe(true);
    store.setSettingsVisible(true);
    await vi.waitFor(() => expect(store.stale).toBe(false));
    expect(status).toHaveBeenCalledTimes(1);
  });
});
