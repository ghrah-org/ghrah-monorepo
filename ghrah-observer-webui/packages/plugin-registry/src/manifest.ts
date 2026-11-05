// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";
import { TsHalfSpecSchema, type TsHalfSpec } from "./spec.js";

/**
 * 宿主装配脚本产出的插件清单（`plugins-manifest.json`）：
 * TS 半 spec 列表 + 每插件入口 URL（`/plugins/<plugin_id>/<version>/<entry>`）。
 */

export const PluginsManifestSchema = z
  .object({
    plugins: z.array(TsHalfSpecSchema).optional().default([]),
    urls: z.record(z.string()).optional().default({}),
  })
  .strict();

export type PluginsManifest = z.infer<typeof PluginsManifestSchema>;

export function parseManifest(raw: unknown): PluginsManifest {
  return PluginsManifestSchema.parse(raw);
}

/** P0 本地宿主能力白名单（父计划 §4.5：node-assembler）。 */
export const HOST_CAPABILITIES: readonly string[] = ["node-assembler"];

export type HostCapabilities = readonly string[];

/** 校验 spec 的 requires.host_capability 是否被当前宿主全部满足。 */
export function hasHostCapability(
  spec: TsHalfSpec,
  hostCaps: HostCapabilities = HOST_CAPABILITIES,
): boolean {
  if (spec.requires.host_capability.length === 0) return true;
  const owned = new Set(hostCaps);
  return spec.requires.host_capability.every((capability) => owned.has(capability));
}
