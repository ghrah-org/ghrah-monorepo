// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  AbilityApiStatus,
  AbilityMcpProbeView,
  AbilityPackageGroupsView,
  AbilityPackageVersionProfileView,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref, triggerRef } from "vue";

import { abilityControlClient } from "../ability-client.js";
import type { AbilityMcpProbeOptions } from "../ability-control.js";
import { HttpError } from "../http.js";

/** 状态端点不带项目路径；这里只为满足客户端的标识校验。 */
const STATUS_PROJECT = "local";

/** 版本画像与探测缓存的键：`namespace.name@version`。 */
export function abilityPackageVersionKey(namespace: string, name: string, version: string): string {
  return `${namespace}.${name}@${version}`;
}

/** 一个能力包版本的只读画像缓存。画像来自服务端，这里不是权威。 */
export interface VersionProfileRecord {
  key: string;
  profile: AbilityPackageVersionProfileView | null;
  loading: boolean;
  failed: boolean;
  errorMessage: string | null;
  /** 传输失败时的 `HttpError.code`；界面只按这个码显示固定文案。 */
  errorCode: string | null;
  loadedAt: number | null;
}

/** 一次探测的最近观测。`ok=false` 仍是完整报告；传输失败才把 `failed` 置真。 */
export interface ProbeRecord {
  key: string;
  report: AbilityMcpProbeView | null;
  loading: boolean;
  failed: boolean;
  errorMessage: string | null;
  finishedAt: number | null;
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "request failed";
}

function failureCode(error: unknown): string | null {
  return error instanceof HttpError ? error.code : null;
}

function blankProfile(key: string): VersionProfileRecord {
  return {
    key,
    profile: null,
    loading: false,
    failed: false,
    errorMessage: null,
    errorCode: null,
    loadedAt: null,
  };
}

function blankProbe(key: string): ProbeRecord {
  return {
    key,
    report: null,
    loading: false,
    failed: false,
    errorMessage: null,
    finishedAt: null,
  };
}

export const useAbilityPackagesStore = defineStore("ghrah-ability-packages", () => {
  const status = ref<AbilityApiStatus | null>(null);
  const groups = ref<AbilityPackageGroupsView | null>(null);
  const groupsFailed = ref(false);
  const loading = ref(false);
  const stale = ref(false);
  const failed = ref(false);
  const errorMessage = ref<string | null>(null);
  const loadedAt = ref<number | null>(null);
  const settingsVisible = ref(false);
  const assemblyVisible = ref(false);
  const versionProfiles = ref(new Map<string, VersionProfileRecord>());
  const probes = ref(new Map<string, ProbeRecord>());
  const profileInflight = new Map<string, Promise<void>>();
  const probeInflight = new Map<string, Promise<void>>();
  const profileGeneration = new Map<string, number>();
  const probeGeneration = new Map<string, number>();
  let chain: Promise<void> = Promise.resolve();

  function interested(): boolean {
    return settingsVisible.value || assemblyVisible.value;
  }

  function publishProfiles(): void {
    triggerRef(versionProfiles);
  }

  function publishProbes(): void {
    triggerRef(probes);
  }

  function versionProfile(
    namespace: string,
    name: string,
    version: string,
  ): VersionProfileRecord | null {
    return versionProfiles.value.get(abilityPackageVersionKey(namespace, name, version)) ?? null;
  }

  function probe(namespace: string, name: string, version: string): ProbeRecord | null {
    return probes.value.get(abilityPackageVersionKey(namespace, name, version)) ?? null;
  }

  function ensureProfile(key: string): VersionProfileRecord {
    const existing = versionProfiles.value.get(key);
    if (existing) return existing;
    versionProfiles.value.set(key, blankProfile(key));
    const created = versionProfiles.value.get(key);
    if (!created) return blankProfile(key);
    return created;
  }

  function ensureProbe(key: string): ProbeRecord {
    const existing = probes.value.get(key);
    if (existing) return existing;
    probes.value.set(key, blankProbe(key));
    const created = probes.value.get(key);
    if (!created) return blankProbe(key);
    return created;
  }

  /** 清空版本画像和探测缓存，并作废尚未写回的响应。 */
  function invalidateDetails(): void {
    for (const [key, generation] of profileGeneration) {
      profileGeneration.set(key, generation + 1);
    }
    for (const [key, generation] of probeGeneration) {
      probeGeneration.set(key, generation + 1);
    }
    profileInflight.clear();
    probeInflight.clear();
    versionProfiles.value = new Map();
    probes.value = new Map();
  }

  function load(): Promise<void> {
    const run = chain.then(() => read());
    chain = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  async function read(): Promise<void> {
    loading.value = true;
    failed.value = false;
    errorMessage.value = null;
    try {
      const client = abilityControlClient(STATUS_PROJECT);
      status.value = await client.status();
      stale.value = false;
      loadedAt.value = Date.now();
      invalidateDetails();
      if (status.value.features.packages_read) {
        try {
          groups.value = await client.packageGroups();
          groupsFailed.value = false;
        } catch (error) {
          groups.value = null;
          groupsFailed.value = true;
          errorMessage.value = errorText(error);
        }
      } else {
        groups.value = null;
        groupsFailed.value = false;
      }
    } catch (error) {
      failed.value = true;
      groups.value = null;
      groupsFailed.value = false;
      errorMessage.value = errorText(error);
    } finally {
      loading.value = false;
    }
  }

  function setSettingsVisible(shown: boolean) {
    settingsVisible.value = shown;
    if (shown && (stale.value || loadedAt.value === null)) void load();
  }

  function setAssemblyVisible(shown: boolean) {
    assemblyVisible.value = shown;
    if (shown && (stale.value || loadedAt.value === null)) void load();
  }

  async function markStale(): Promise<void> {
    stale.value = true;
    if (interested()) await load();
  }

  async function reloadIfVisible(): Promise<void> {
    stale.value = true;
    if (interested()) await load();
  }

  /**
   * 按需读取一个版本画像。
   *
   * 目录门未开或状态尚未读到时不发请求。同一键同时只飞一次；刷新作废后的响应不会写回。
   */
  function loadVersionProfile(namespace: string, name: string, version: string): Promise<void> {
    if (status.value?.features.packages_read !== true) return Promise.resolve();
    const key = abilityPackageVersionKey(namespace, name, version);
    const pending = profileInflight.get(key);
    if (pending) return pending;
    const current = (profileGeneration.get(key) ?? 0) + 1;
    profileGeneration.set(key, current);
    const task = readVersionProfile(namespace, name, version, key, current);
    profileInflight.set(key, task);
    return task.finally(() => {
      if (profileInflight.get(key) === task) profileInflight.delete(key);
    });
  }

  async function readVersionProfile(
    namespace: string,
    name: string,
    version: string,
    key: string,
    current: number,
  ): Promise<void> {
    const entry = ensureProfile(key);
    entry.loading = true;
    entry.failed = false;
    entry.errorMessage = null;
    entry.errorCode = null;
    publishProfiles();
    try {
      const view = await abilityControlClient(STATUS_PROJECT).packageVersion(
        namespace,
        name,
        version,
      );
      if (profileGeneration.get(key) !== current) return;
      entry.profile = view;
      entry.loadedAt = Date.now();
      entry.failed = false;
      entry.errorCode = null;
    } catch (error) {
      if (profileGeneration.get(key) !== current) return;
      entry.failed = true;
      entry.errorMessage = errorText(error);
      entry.errorCode = failureCode(error);
    } finally {
      if (profileGeneration.get(key) === current) entry.loading = false;
      publishProfiles();
    }
  }

  /**
   * 按包版本做一次只读探测，并记下最近一次完整观测。
   *
   * `mcp_probe` 关闭时不发请求。HTTP 200 且 `ok=false` 写入 `report`；传输失败保留上一份报告。
   * 调用方取消时不记失败，也不覆盖已有报告。
   */
  function probeMcp(
    namespace: string,
    name: string,
    version: string,
    options: AbilityMcpProbeOptions = {},
  ): Promise<void> {
    if (status.value?.features.mcp_probe !== true) return Promise.resolve();
    const key = abilityPackageVersionKey(namespace, name, version);
    const pending = probeInflight.get(key);
    if (pending) return pending;
    const current = (probeGeneration.get(key) ?? 0) + 1;
    probeGeneration.set(key, current);
    const task = readProbe(namespace, name, version, options, key, current);
    probeInflight.set(key, task);
    return task.finally(() => {
      if (probeInflight.get(key) === task) probeInflight.delete(key);
    });
  }

  async function readProbe(
    namespace: string,
    name: string,
    version: string,
    options: AbilityMcpProbeOptions,
    key: string,
    current: number,
  ): Promise<void> {
    const entry = ensureProbe(key);
    entry.loading = true;
    entry.failed = false;
    entry.errorMessage = null;
    publishProbes();
    try {
      const view = await abilityControlClient(STATUS_PROJECT).probePackageMcp(
        namespace,
        name,
        version,
        options,
      );
      if (probeGeneration.get(key) !== current) return;
      entry.report = view;
      entry.finishedAt = Date.now();
      entry.failed = false;
      entry.errorMessage = null;
    } catch (error) {
      if (probeGeneration.get(key) !== current) return;
      if (options.signal?.aborted) return;
      entry.failed = true;
      entry.errorMessage = errorText(error);
    } finally {
      if (probeGeneration.get(key) === current) entry.loading = false;
      publishProbes();
    }
  }

  return {
    status,
    groups,
    groupsFailed,
    loading,
    stale,
    failed,
    errorMessage,
    loadedAt,
    settingsVisible,
    assemblyVisible,
    versionProfiles,
    probes,
    load,
    setSettingsVisible,
    setAssemblyVisible,
    markStale,
    reloadIfVisible,
    versionProfile,
    probe,
    loadVersionProfile,
    probeMcp,
    invalidateDetails,
  };
});
