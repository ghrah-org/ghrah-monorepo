// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  AbilityApiStatusSchema,
  AbilityMcpProbeViewSchema,
  AbilityPackageVersionProfileViewSchema,
  type AbilityMcpProbeView,
  type AbilityPackageVersionProfileView,
} from "@ghrah/protocol";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAbilityControlClientFactory } from "../ability-client.js";
import type { AbilityControlClient, AbilityMcpProbeOptions } from "../ability-control.js";
import type { HttpAbortSignal } from "../http.js";
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

  it("loads one version profile per key and keeps parallel keys apart", async () => {
    const readable = statusWith({ packages_read: true });
    let release: ((view: AbilityPackageVersionProfileView) => void) | undefined;
    const packageVersion = vi.fn(
      () =>
        new Promise<AbilityPackageVersionProfileView>((resolve) => {
          release = resolve;
        }),
    );
    setAbilityControlClientFactory(() => ({ packageVersion }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    store.status = readable;
    const first = store.loadVersionProfile("ghrah", "fs", "0.1.0");
    const second = store.loadVersionProfile("ghrah", "fs", "0.1.0");
    expect(packageVersion).toHaveBeenCalledTimes(1);
    const other = profileOf("ghrah", "mcp", "0.1.0");
    packageVersion.mockResolvedValueOnce(other);
    const third = store.loadVersionProfile("ghrah", "mcp", "0.1.0");
    expect(packageVersion).toHaveBeenCalledTimes(2);
    release?.(profileOf("ghrah", "fs", "0.1.0"));
    await Promise.all([first, second, third]);
    expect(store.versionProfile("ghrah", "fs", "0.1.0")?.profile?.full_name).toBe("ghrah.fs");
    expect(store.versionProfile("ghrah", "mcp", "0.1.0")?.profile?.full_name).toBe("ghrah.mcp");
    expect(store.versionProfile("ghrah", "fs", "0.1.0")?.failed).toBe(false);
  });

  it("keeps the last profile when a later read fails in transport", async () => {
    const profile = profileOf("ghrah", "fs", "0.1.0");
    const packageVersion = vi
      .fn<AbilityControlClient["packageVersion"]>()
      .mockResolvedValueOnce(profile)
      .mockRejectedValueOnce(new Error("network down"));
    setAbilityControlClientFactory(() => ({ packageVersion }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    store.status = statusWith({ packages_read: true });
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    const record = store.versionProfile("ghrah", "fs", "0.1.0");
    expect(record?.failed).toBe(true);
    expect(record?.errorMessage).toBe("network down");
    expect(record?.profile).toEqual(profile);
    expect(record?.loading).toBe(false);
  });

  it("stores an ok=false probe as an observation and skips a closed gate", async () => {
    const observed = AbilityMcpProbeViewSchema.parse({
      namespace: "mcp",
      name: "local",
      version: "0.1.0",
      server: "notes",
      ok: false,
      phase: "connect",
      elapsed_ms: 12,
      tool_count: 0,
      resource_count: 0,
      error: { reason: "connect_failed", message: "连接失败" },
    });
    const probePackageMcp = vi.fn(async () => observed);
    setAbilityControlClientFactory(() => ({ probePackageMcp }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    await store.probeMcp("mcp", "local", "0.1.0");
    expect(probePackageMcp).not.toHaveBeenCalled();
    store.status = statusWith({ mcp_probe: false });
    await store.probeMcp("mcp", "local", "0.1.0");
    expect(probePackageMcp).not.toHaveBeenCalled();
    expect(store.probe("mcp", "local", "0.1.0")).toBeNull();

    store.status = statusWith({ mcp_probe: true });
    await store.probeMcp("mcp", "local", "0.1.0");
    const record = store.probe("mcp", "local", "0.1.0");
    expect(record?.failed).toBe(false);
    expect(record?.report).toEqual(observed);
    expect(record?.finishedAt).not.toBeNull();
  });

  it("does not request a version profile while the catalog gate is closed", async () => {
    const packageVersion = vi.fn();
    setAbilityControlClientFactory(() => ({ packageVersion }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    store.status = statusWith({ packages_read: false });
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    expect(packageVersion).not.toHaveBeenCalled();
    expect(store.versionProfile("ghrah", "fs", "0.1.0")).toBeNull();
  });

  it("drops an in-flight profile when details are invalidated", async () => {
    let release: ((view: AbilityPackageVersionProfileView) => void) | undefined;
    const packageVersion = vi.fn(
      () =>
        new Promise<AbilityPackageVersionProfileView>((resolve) => {
          release = resolve;
        }),
    );
    setAbilityControlClientFactory(() => ({ packageVersion }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    store.status = statusWith({ packages_read: true });
    const pending = store.loadVersionProfile("ghrah", "fs", "0.1.0");
    await vi.waitFor(() =>
      expect(store.versionProfile("ghrah", "fs", "0.1.0")?.loading).toBe(true),
    );
    store.invalidateDetails();
    release?.(profileOf("ghrah", "fs", "0.1.0"));
    await pending;
    expect(store.versionProfile("ghrah", "fs", "0.1.0")).toBeNull();
    packageVersion.mockResolvedValue(profileOf("ghrah", "fs", "0.1.0"));
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    expect(packageVersion).toHaveBeenCalledTimes(2);
    expect(store.versionProfile("ghrah", "fs", "0.1.0")?.profile?.full_name).toBe("ghrah.fs");
  });

  it("keeps the previous probe when the caller cancels", async () => {
    const observed = probeOk("mcp", "local", "0.1.0");
    const cancellation = cancellable();
    const probePackageMcp = vi.fn(
      async (
        _namespace: string,
        _name: string,
        _version: string,
        options?: AbilityMcpProbeOptions,
      ) => {
        if (options?.signal) {
          return new Promise<AbilityMcpProbeView>((_resolve, reject) => {
            options.signal?.addEventListener("abort", () => {
              reject(new Error("aborted"));
            });
          });
        }
        return observed;
      },
    );
    setAbilityControlClientFactory(() => ({ probePackageMcp }) as unknown as AbilityControlClient);
    const store = useAbilityPackagesStore();
    store.status = statusWith({ mcp_probe: true });
    await store.probeMcp("mcp", "local", "0.1.0");
    const pending = store.probeMcp("mcp", "local", "0.1.0", { signal: cancellation.signal });
    cancellation.abort();
    await pending;
    const record = store.probe("mcp", "local", "0.1.0");
    expect(record?.failed).toBe(false);
    expect(record?.report).toEqual(observed);
    expect(record?.loading).toBe(false);
    expect(record?.errorMessage).toBeNull();
  });

  it("clears version caches after a successful status refresh and keeps them when it fails", async () => {
    const readable = statusWith({ packages_read: true, mcp_probe: true });
    const profile = profileOf("ghrah", "fs", "0.1.0");
    const observed = probeOk("mcp", "local", "0.1.0");
    const status = vi.fn(async () => readable);
    const packageVersion = vi.fn(async () => profile);
    const probePackageMcp = vi.fn(async () => observed);
    const packageGroups = vi.fn(async () => ({
      scope: "distribution",
      installed_available: false,
      project_id: null,
      agent_id: null,
      groups: [],
    }));
    setAbilityControlClientFactory(
      () =>
        ({
          status,
          packageGroups,
          packageVersion,
          probePackageMcp,
        }) as unknown as AbilityControlClient,
    );
    const store = useAbilityPackagesStore();
    store.status = readable;
    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    await store.probeMcp("mcp", "local", "0.1.0");
    await store.load();
    expect(store.failed).toBe(false);
    expect(store.versionProfile("ghrah", "fs", "0.1.0")).toBeNull();
    expect(store.probe("mcp", "local", "0.1.0")).toBeNull();

    await store.loadVersionProfile("ghrah", "fs", "0.1.0");
    expect(store.versionProfile("ghrah", "fs", "0.1.0")?.profile).toEqual(profile);
    status.mockRejectedValueOnce(new Error("offline"));
    await store.load();
    expect(store.failed).toBe(true);
    expect(store.versionProfile("ghrah", "fs", "0.1.0")?.profile).toEqual(profile);
  });
});

function statusWith(features: { packages_read?: boolean; mcp_probe?: boolean }) {
  return AbilityApiStatusSchema.parse({
    ability_source: "builtin",
    legacy_deprecated: false,
    features: {
      tree: true,
      grants_write: true,
      versions_read: false,
      versions_write: false,
      packages_read: features.packages_read ?? false,
      packages_install: false,
      mcp_probe: features.mcp_probe ?? false,
      surface_write: false,
    },
  });
}

function profileOf(
  namespace: string,
  name: string,
  version: string,
): AbilityPackageVersionProfileView {
  return AbilityPackageVersionProfileViewSchema.parse({
    namespace,
    name,
    full_name: `${namespace}.${name}`,
    version,
    origin: "builtin",
    lock_state: "builtin",
  });
}

function cancellable(): { signal: HttpAbortSignal; abort: () => void } {
  let aborted = false;
  const listeners: Array<() => void> = [];
  return {
    signal: {
      get aborted() {
        return aborted;
      },
      addEventListener(_type: "abort", listener: () => void) {
        listeners.push(listener);
      },
    },
    abort() {
      if (aborted) return;
      aborted = true;
      for (const listener of listeners) listener();
    },
  };
}

function probeOk(namespace: string, name: string, version: string): AbilityMcpProbeView {
  return AbilityMcpProbeViewSchema.parse({
    namespace,
    name,
    version,
    server: "notes",
    ok: true,
    phase: "list_tools",
    elapsed_ms: 4,
    tool_count: 0,
    resource_count: 0,
    error: null,
  });
}
