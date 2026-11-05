// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { PluginNegotiateResultPayload } from "@ghrah/protocol";
import type { PluginsManifest } from "./manifest.js";
import { toWireReport, type TsHalfSpec } from "./spec.js";

/**
 * 协商纯函数：清单 → TS 半上报；协商结果 → 待加载清单与展示态。
 * 权威协商在 Python negotiator（Subject 侧），本模块只构造上报与消费结果。
 */

export function buildTsHalfReports(manifest: PluginsManifest): ReturnType<typeof toWireReport>[] {
  return manifest.plugins.map((spec) => toWireReport(spec));
}

export interface NegotiationOutcome {
  /** matched 且产物 URL 在 manifest 中可解析 → 待加载（url + spec）。 */
  readonly toLoad: ReadonlyArray<{ spec: TsHalfSpec; url: string }>;
  /** matched 但 manifest 缺 URL（装配不一致，只展示不加载）。 */
  readonly matchedWithoutUrl: readonly string[];
}

/** 应用协商结果：只取 matched ∩ manifest，产出宿主加载器需要的最小清单。 */
export function applyNegotiationResult(
  result: PluginNegotiateResultPayload,
  manifest: PluginsManifest,
): NegotiationOutcome {
  const specById = new Map(manifest.plugins.map((spec) => [spec.plugin_id, spec]));
  const toLoad: Array<{ spec: TsHalfSpec; url: string }> = [];
  const matchedWithoutUrl: string[] = [];
  for (const matched of result.matched) {
    const spec = specById.get(matched.plugin_id);
    if (!spec) continue;
    const url = manifest.urls[matched.plugin_id];
    if (!url) {
      matchedWithoutUrl.push(matched.plugin_id);
      continue;
    }
    toLoad.push({ spec, url });
  }
  return { toLoad, matchedWithoutUrl };
}
