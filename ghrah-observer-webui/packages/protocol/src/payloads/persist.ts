// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

export const PersistSavePayloadSchema = z.object({
  key: z.string(),
  data: z.record(z.unknown()),
  namespace: z.string().optional().default("default"),
});

export const PersistLoadPayloadSchema = z.object({
  key: z.string(),
  namespace: z.string().optional().default("default"),
});

export const PersistDeletePayloadSchema = z.object({
  key: z.string(),
  namespace: z.string().optional().default("default"),
});

export const PersistListPayloadSchema = z.object({
  namespace: z.string().optional().default("default"),
  prefix: z.string().nullable().optional(),
});

export type PersistSavePayload = z.infer<typeof PersistSavePayloadSchema>;
export type PersistLoadPayload = z.infer<typeof PersistLoadPayloadSchema>;
export type PersistDeletePayload = z.infer<typeof PersistDeletePayloadSchema>;
export type PersistListPayload = z.infer<typeof PersistListPayloadSchema>;
