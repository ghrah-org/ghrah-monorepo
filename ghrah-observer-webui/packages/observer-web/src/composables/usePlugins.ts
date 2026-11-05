// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  PluginRegistry,
  parseManifest,
  buildTsHalfReports,
  applyNegotiationResult,
  loadPlugin,
} from "@ghrah/plugin-registry";
import { usePluginsStore } from "@ghrah/observer-core";
import { computed, readonly, ref, watch, type Component } from "vue";
import type { TsHalfReport } from "@ghrah/protocol";

/**
 * 插件宿主装配 composable：
 * - `loadManifest()` fetch 装配产物并登记注册表（**必须在 connect 前完成**，D8：
 *   连接后补装不会重发协商）；失败 → 空清单（显式降级，非隐式兜底）；
 * - `tsHalfProvider` 只读快照（供 connectStores 上报）；
 * - watch 协商 matched → 动态加载插件并注册渲染器；
 * - `resolveBadge` 供徽章管线消费（未命中 → null → raw 回退）。
 */

const registry = new PluginRegistry();
const reports = ref<TsHalfReport[]>([]);
const manifestError = ref<string | null>(null);
const loadedIds = new Set<string>();
const loadResults = ref<Array<{ pluginId: string; ok: boolean; error?: string }>>([]);
let started = false;

async function loadManifest(): Promise<void> {
  if (started) return;
  started = true;
  registry.clear();
  loadedIds.clear();
  loadResults.value = [];
  try {
    const response = await fetch("/plugins/plugins-manifest.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const manifest = parseManifest(await response.json());
    for (const spec of manifest.plugins) registry.register(spec);
    reports.value = buildTsHalfReports(manifest);
    manifestError.value = null;
    watchMatched(manifest.urls);
  } catch (err) {
    manifestError.value = err instanceof Error ? err.message : String(err);
    reports.value = [];
  }
}

function watchMatched(urls: Record<string, string>): void {
  const plugins = usePluginsStore();
  watch(
    () => plugins.negotiation,
    (result) => {
      if (!result) return;
      const outcome = applyNegotiationResult(result, {
        plugins: [...registry.state.specs.values()],
        urls,
      });
      for (const { spec, url } of outcome.toLoad) {
        if (loadedIds.has(spec.plugin_id)) continue;
        loadedIds.add(spec.plugin_id);
        void loadPlugin(url, {
          pluginId: spec.plugin_id,
          version: spec.version,
          registry,
          registerBadgeRenderer: (kind: string, component: Component) =>
            registry.registerBadgeRenderer(kind, component),
        }).then((loadResult) => {
          loadResults.value = [...loadResults.value, loadResult];
        });
      }
    },
  );
}

function tsHalfProvider(): TsHalfReport[] {
  return reports.value;
}

function resolveBadge(kind: string): Component | null {
  return registry.resolveBadge(kind);
}

export function usePlugins() {
  const plugins = usePluginsStore();
  return {
    loadManifest,
    tsHalfProvider,
    resolveBadge,
    manifestError: readonly(manifestError),
    loadResults: readonly(loadResults),
    negotiation: computed(() => plugins.negotiation),
  };
}
