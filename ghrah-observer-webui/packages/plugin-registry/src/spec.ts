// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import type { TsHalfReport } from "@ghrah/protocol";
import { z } from "zod";

/**
 * TS 半插件 spec（`plugin.json`）的 Zod schema。
 *
 * 与 Python 半 `PluginSpec` 字段域不同：TS 半只提供渲染/前端能力
 * （badge/view renderer、命令声明），checkers/evidence_kinds 归 Python 半；
 * 因此不与 Python schema 同源生成，跨语言约定经 provides 拍平测试对齐
 * （与 ``ghrah.plugin.negotiator.python_half_from_spec`` 同构）。
 */

export const TsHalfSpecSchema = z
  .object({
    plugin_id: z.string().min(1),
    version: z.string().min(1),
    provides: z
      .object({
        badge_renderers: z.array(z.string().min(1)).optional().default([]),
        view_renderers: z.array(z.string().min(1)).optional().default([]),
        commands: z.array(z.string().min(1)).optional().default([]),
        capabilities: z.array(z.string().min(1)).optional().default([]),
      })
      .strict()
      .optional()
      .default({}),
    requires: z
      .object({
        host_capability: z.array(z.string().min(1)).optional().default([]),
      })
      .strict()
      .optional()
      .default({}),
    ui_slots: z
      .array(z.enum(["badge", "card_decorator", "view_renderer", "panel"]))
      .optional()
      .default([]),
    entry: z.string().min(1),
  })
  .strict();

export type TsHalfSpec = z.infer<typeof TsHalfSpecSchema>;

/**
 * 拍平 provides 为协商 wire 的字符串清单：
 * ``badge-renderer/<k>`` / ``view-renderer/<n>`` / ``command/<c>``，
 * capabilities 原文（与 Python ``python_half_from_spec`` 的
 * ``checker/`` / ``evidence-kind/`` / ``command/`` 前缀约定同构）。
 */
export function toWireReport(spec: TsHalfSpec): TsHalfReport {
  return {
    plugin_id: spec.plugin_id,
    version: spec.version,
    provides: [
      ...spec.provides.badge_renderers.map((kind) => `badge-renderer/${kind}`),
      ...spec.provides.view_renderers.map((name) => `view-renderer/${name}`),
      ...spec.provides.commands.map((name) => `command/${name}`),
      ...spec.provides.capabilities,
    ],
  };
}
