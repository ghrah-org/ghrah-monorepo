// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type {
  PluginCrashedPayload,
  PluginLifecyclePayload,
  PluginNegotiateResultPayload,
} from "@ghrah/protocol";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

/**
 * 插件协商投影：协商结果（六字段）+ 插件域事件累积（lifecycle/crashed）。
 * 数据源（bind）：`COMMAND_RESULT(PLUGIN_NEGOTIATE)` 回执 + `PLUGIN_*` 事件。
 * 权威在 Subject 侧 negotiator；本 store 只持投影，可随时经重协商重建。
 */
export const usePluginsStore = defineStore("ghrah-plugins", () => {
  const negotiation = ref<PluginNegotiateResultPayload | null>(null);
  const crashed = ref<PluginCrashedPayload[]>([]);
  const lifecycle = ref<Map<string, PluginLifecyclePayload>>(new Map());

  const matchedById = computed(
    () => new Map((negotiation.value?.matched ?? []).map((item) => [item.plugin_id, item])),
  );

  function setNegotiationResult(result: PluginNegotiateResultPayload) {
    negotiation.value = result;
  }

  function onLifecycleEvent(payload: PluginLifecyclePayload) {
    const next = new Map(lifecycle.value);
    next.set(payload.plugin_id, payload);
    lifecycle.value = next;
  }

  function onCrashed(payload: PluginCrashedPayload) {
    crashed.value = [...crashed.value, payload];
  }

  function clear() {
    negotiation.value = null;
    crashed.value = [];
    lifecycle.value = new Map();
  }

  return {
    negotiation,
    crashed,
    lifecycle,
    matchedById,
    setNegotiationResult,
    onLifecycleEvent,
    onCrashed,
    clear,
  };
});
