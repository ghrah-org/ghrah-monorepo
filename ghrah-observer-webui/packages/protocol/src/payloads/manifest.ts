// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

// ─── Manifest CRUD Payloads ───

export const ManifestListPayloadSchema = z.object({
  namespace: z.string().nullable().optional(),
});

export const ManifestGetPayloadSchema = z.object({
  full_name: z.string(),
});

export const ManifestPutPayloadSchema = z.object({
  full_name: z.string(),
  content: z.string(),
  overwrite: z.boolean().optional().default(false),
});

export const ManifestDeletePayloadSchema = z.object({
  full_name: z.string(),
});

export const ManifestValidatePayloadSchema = z.object({
  content: z.string(),
  manifest_type: z.string(),
});

export const ManifestResolvePayloadSchema = z.object({
  agent_full_name: z.string(),
  runtime_name: z.string().nullable().optional(),
});

// ─── Manifest Event Payloads ───

export const ManifestAbilityEventPayloadSchema = z.object({
  full_name: z.string(),
  namespace: z.string(),
  manifest: z.record(z.unknown()).nullable().optional(),
  source: z.string().nullable().optional(),
});

export const ManifestAgentEventPayloadSchema = z.object({
  full_name: z.string(),
  namespace: z.string(),
  manifest: z.record(z.unknown()).nullable().optional(),
  source: z.string().nullable().optional(),
});

export type ManifestListPayload = z.infer<typeof ManifestListPayloadSchema>;
export type ManifestGetPayload = z.infer<typeof ManifestGetPayloadSchema>;
export type ManifestPutPayload = z.infer<typeof ManifestPutPayloadSchema>;
export type ManifestDeletePayload = z.infer<typeof ManifestDeletePayloadSchema>;
export type ManifestValidatePayload = z.infer<typeof ManifestValidatePayloadSchema>;
export type ManifestResolvePayload = z.infer<typeof ManifestResolvePayloadSchema>;
export type ManifestAbilityEventPayload = z.infer<typeof ManifestAbilityEventPayloadSchema>;
export type ManifestAgentEventPayload = z.infer<typeof ManifestAgentEventPayloadSchema>;
