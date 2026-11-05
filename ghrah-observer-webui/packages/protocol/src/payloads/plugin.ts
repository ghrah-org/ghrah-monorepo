// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

// ─── Plugin Payloads（协商命令与插件事件）───
// 协商 wire 形状与 ghrah-plugin negotiator 输出单一权威对齐：
// PluginNegotiateResultPayloadSchema 镜像 NegotiationResult 六字段。

export const TsHalfReportSchema = z.object({
  plugin_id: z.string(),
  version: z.string(),
  provides: z.array(z.string()).optional().default([]),
});

export const MatchedPluginSchema = z.object({
  plugin_id: z.string(),
  version: z.string(),
  provides: z.array(z.string()).optional().default([]),
  instances: z.array(z.string()).optional().default([]),
});

export const PythonHalfInfoSchema = z.object({
  plugin_id: z.string(),
  version: z.string(),
  provides: z.array(z.string()).optional().default([]),
  requires_capabilities: z.array(z.string()).optional().default([]),
  instances: z.array(z.string()).optional().default([]),
});

export const VersionConflictSchema = z.object({
  plugin_id: z.string(),
  python_version: z.string(),
  ts_version: z.string(),
});

export const PluginNegotiatePayloadSchema = z.object({
  enabled_ts: z.array(TsHalfReportSchema).optional().default([]),
});

export const PluginNegotiateResultPayloadSchema = z.object({
  matched: z.array(MatchedPluginSchema).optional().default([]),
  python_only: z.array(PythonHalfInfoSchema).optional().default([]),
  ts_only: z.array(TsHalfReportSchema).optional().default([]),
  version_conflicts: z.array(VersionConflictSchema).optional().default([]),
  missing_capabilities: z.array(z.string()).optional().default([]),
  instances: z.record(z.array(z.string())).optional().default({}),
});

export const PluginLifecyclePayloadSchema = z.object({
  plugin_id: z.string(),
  version: z.string(),
  instance_ids: z.array(z.string()).optional().default([]),
});

export const PluginNegotiatedPayloadSchema = z.object({
  changed: z.array(z.string()).optional().default([]),
});

export const PluginCrashedPayloadSchema = z.object({
  plugin_id: z.string(),
  command: z.string(),
  error: z.string(),
  instance_id: z.string().nullable().optional(),
});

export type TsHalfReport = z.infer<typeof TsHalfReportSchema>;
export type MatchedPlugin = z.infer<typeof MatchedPluginSchema>;
export type PythonHalfInfo = z.infer<typeof PythonHalfInfoSchema>;
export type VersionConflict = z.infer<typeof VersionConflictSchema>;
export type PluginNegotiatePayload = z.infer<typeof PluginNegotiatePayloadSchema>;
export type PluginNegotiateResultPayload = z.infer<typeof PluginNegotiateResultPayloadSchema>;
export type PluginLifecyclePayload = z.infer<typeof PluginLifecyclePayloadSchema>;
export type PluginNegotiatedPayload = z.infer<typeof PluginNegotiatedPayloadSchema>;
export type PluginCrashedPayload = z.infer<typeof PluginCrashedPayloadSchema>;
