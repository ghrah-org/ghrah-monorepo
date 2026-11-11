// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { AbilityApiStatus, AbilityPackageGroupsView } from "@ghrah/protocol";
import { defineStore } from "pinia";
import { ref } from "vue";

import { abilityControlClient } from "../ability-client.js";

/** 状态端点不带项目路径；这里只为满足客户端的标识校验。 */
const STATUS_PROJECT = "local";

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "request failed";
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
  let chain: Promise<void> = Promise.resolve();

  function interested(): boolean {
    return settingsVisible.value || assemblyVisible.value;
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
    load,
    setSettingsVisible,
    setAssemblyVisible,
    markStale,
    reloadIfVisible,
  };
});
