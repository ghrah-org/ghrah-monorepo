// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

export const CommandResultPayloadSchema = z.object({
  request_id: z.string(),
  success: z.boolean(),
  data: z.unknown().nullable().optional(),
  error: z.string().nullable().optional(),
  error_detail: z.string().nullable().optional(),
});

export const ErrorPayloadSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.record(z.unknown()).nullable().optional(),
});

export type CommandResultPayload = z.infer<typeof CommandResultPayloadSchema>;
export type ErrorPayload = z.infer<typeof ErrorPayloadSchema>;
