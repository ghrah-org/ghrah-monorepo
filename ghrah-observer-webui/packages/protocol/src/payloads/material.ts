// SPDX-FileCopyrightText: 2026 chenxya <ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

export const MaterialStateGetPayloadSchema = z.object({ project_id: z.string().min(1) }).strict();
export const MaterialStateRegisterPayloadSchema = MaterialStateGetPayloadSchema.extend({
  material_id: z.string().min(1),
  material_version: z.string().min(1),
  title: z.string().default(""),
  initial_data: z.record(z.unknown()),
  data_schema: z.record(z.unknown()),
  patch_schema: z.record(z.unknown()).nullable().default(null),
});
export const MaterialStateSyncPayloadSchema = MaterialStateGetPayloadSchema.extend({
  room_id: z.string().min(1),
  limit: z.number().int().min(1).max(1000).default(200),
});
export const MaterialStatePayloadSchema = z.object({
  project_id: z.string(),
  material_id: z.string(),
  material_version: z.string(),
  title: z.string(),
  data: z.record(z.unknown()),
  revision: z.number().int().min(0),
  updated_at: z.string(),
});
export const MaterialPatchFieldPayloadSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.unknown(),
});
export const MaterialPatchEventPayloadSchema = z.object({
  room_log_id: z.string(),
  room_id: z.string(),
  status: z.enum(["applied", "unchanged", "rejected"]),
  revision: z.number().int().min(0),
  fields: z.array(MaterialPatchFieldPayloadSchema).default([]),
  error: z.string().default(""),
});
export const MaterialStateResultPayloadSchema = z.object({
  state: MaterialStatePayloadSchema.nullable().default(null),
  applications: z.array(MaterialPatchEventPayloadSchema).default([]),
  has_more: z.boolean().default(false),
});
export type MaterialStateGetPayload = z.infer<typeof MaterialStateGetPayloadSchema>;
export type MaterialStateRegisterPayload = z.infer<typeof MaterialStateRegisterPayloadSchema>;
export type MaterialStateSyncPayload = z.infer<typeof MaterialStateSyncPayloadSchema>;
export type MaterialStatePayload = z.infer<typeof MaterialStatePayloadSchema>;
export type MaterialPatchFieldPayload = z.infer<typeof MaterialPatchFieldPayloadSchema>;
export type MaterialPatchEventPayload = z.infer<typeof MaterialPatchEventPayloadSchema>;
export type MaterialStateResultPayload = z.infer<typeof MaterialStateResultPayloadSchema>;
