// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { z } from "zod";

const TurnPayloadSchema = z.object({
  project_id: z.string().optional().default(""),
  agent_id: z.string().optional().default(""),
  agent_name: z.string(),
  turn_id: z.string(),
  session_id: z.string(),
});

export const TurnOpenedPayloadSchema = TurnPayloadSchema.extend({
  status: z.string().optional().default("open"),
  opener_delivery_id: z.string().nullable().optional(),
});

export const TurnWaitingHitlPayloadSchema = TurnPayloadSchema.extend({
  promise_id: z.string(),
});

export const TurnClosedPayloadSchema = TurnPayloadSchema.extend({
  status: z.string(),
  closing_kind: z.string(),
  end_node_id: z.string().nullable().optional(),
});

export type TurnOpenedPayload = z.infer<typeof TurnOpenedPayloadSchema>;
export type TurnWaitingHitlPayload = z.infer<typeof TurnWaitingHitlPayloadSchema>;
export type TurnClosedPayload = z.infer<typeof TurnClosedPayloadSchema>;
